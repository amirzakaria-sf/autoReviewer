"""The three-verb review flow: approve, reject, and ask for something else.

The verb that matters here is the third one. Before it existed, a reviewer's
only way to say "wrong approach" was a boolean rejection that recorded no
reason, left the issue unreachable, and threw away the one judgment a human
has that the council does not.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session
from app.enums import FixReviewStatus, FixStatus, IssueStatus
from app.fix_review import (
    MAX_REVISIONS,
    RevisionRejected,
    compile_feedback,
    open_review,
    proposal_summary,
    request_revision,
)
from app.models import Fix, FixReview, Issue, Repo

ISSUE_NUMBER_BASE = 881000


async def _repo(db) -> Repo:
    repo = (
        await db.execute(select(Repo).where(Repo.github_full_name == settings.fixture_repo))
    ).scalars().first()
    if repo is None:
        repo = Repo(github_full_name=settings.fixture_repo)
        db.add(repo)
        await db.flush()
    return repo


async def _seed(number: int, *, score: int = 90) -> tuple[uuid.UUID, uuid.UUID]:
    async with async_session() as db:
        repo = await _repo(db)
        issue = Issue(
            repo_id=repo.id, category="ui", origin="detected", github_issue_number=number,
            title="Delete removes the wrong item", severity=2, status=IssueStatus.FIX_PROPOSED,
        )
        db.add(issue)
        await db.flush()
        fix = Fix(
            issue_id=issue.id, resolution_score=score, branch_name=f"whipguard/{number}-x",
            diff="diff --git a/app.js b/app.js", attempt=1, status=FixStatus.AWAITING_APPROVAL,
            resolution_rubric={"verdict": "resolved", "factors": []},
        )
        db.add(fix)
        await db.flush()
        await open_review(db, issue.id, fix, summary=proposal_summary(fix, "resolved", []))
        await db.commit()
        return issue.id, fix.id


async def _cleanup(issue_id: uuid.UUID) -> None:
    """Order matters: a decision writes a calibration event and a memory trace
    that both reference the fix, so those go first or the delete trips their
    foreign keys."""
    from app.models import CalibrationEvent, CouncilRun, MemoryTrace, Notification, OutcomeCheck

    async with async_session() as db:
        fix_ids = (
            await db.execute(select(Fix.id).where(Fix.issue_id == issue_id))
        ).scalars().all()
        if fix_ids:
            for model in (CalibrationEvent, MemoryTrace, Notification, OutcomeCheck, CouncilRun):
                await db.execute(delete(model).where(model.fix_id.in_(fix_ids)))
        await db.execute(delete(FixReview).where(FixReview.issue_id == issue_id))
        await db.execute(
            Fix.__table__.update().where(Fix.issue_id == issue_id).values(superseded_by_id=None)
        )
        await db.execute(delete(Fix).where(Fix.issue_id == issue_id))
        await db.execute(delete(MemoryTrace).where(MemoryTrace.issue_id == issue_id))
        await db.execute(delete(Issue).where(Issue.id == issue_id))
        await db.commit()


# --- revise -------------------------------------------------------------------


async def test_a_revision_queues_rework_and_never_executes_it_inline():
    """The web process has no Docker socket by design (plan.md §15). Re-running
    the council clones and patches repository code, so this side may only ask
    for it."""
    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 1)
    try:
        async with async_session() as db:
            result = await request_revision(
                db, fix_id, instruction="use the existing helper instead of a new one",
                actor="dev@example.com", surface="dashboard",
            )

        assert result["queued"] is True

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()
            queued = (
                await db.execute(
                    select(Fix).where(Fix.issue_id == issue_id)
                )
            ).scalars().all()

        assert fix.status == FixStatus.REVISING
        assert fix.decision_note == "use the existing helper instead of a new one"
        assert review.status == FixReviewStatus.REVISING.value
        assert review.transcript[-1]["kind"] == "revision-request"
        # No new attempt yet -- that is the worker's job, not this call's.
        assert len(queued) == 1
    finally:
        await _cleanup(issue_id)


async def test_a_second_concurrent_revision_is_refused_rather_than_queued_twice():
    """A double-clicked button would otherwise pass a plain status check twice
    and run two councils against one fix, both appending to one transcript.
    The sibling `opencode` deployment hit exactly this on its planning
    sessions and fixed it with the same atomic conditional UPDATE."""
    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 2)
    try:
        async with async_session() as db:
            await request_revision(db, fix_id, instruction="first", actor="a", surface="dashboard")

        async with async_session() as db:
            with pytest.raises(RevisionRejected) as error:
                await request_revision(db, fix_id, instruction="second", actor="a", surface="dashboard")

        assert "no longer awaiting a decision" in str(error.value)
        assert "revising" in str(error.value)
    finally:
        await _cleanup(issue_id)


async def test_an_empty_instruction_is_refused_because_it_cannot_change_anything():
    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 3)
    try:
        async with async_session() as db:
            with pytest.raises(RevisionRejected):
                await request_revision(db, fix_id, instruction="   ", actor="a", surface="dashboard")

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
        assert fix.status == FixStatus.AWAITING_APPROVAL, "a refused revision must not move the fix"
    finally:
        await _cleanup(issue_id)


async def test_the_revision_loop_is_bounded():
    """Each revision is a full council run -- retrieval, generation, a sandbox
    verification and an adversarial score. Unbounded, this is real money spent
    on an issue nobody is going to merge."""
    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 4)
    try:
        async with async_session() as db:
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()
            review.attempts = MAX_REVISIONS
            await db.commit()

        async with async_session() as db:
            with pytest.raises(RevisionRejected) as error:
                await request_revision(db, fix_id, instruction="again", actor="a", surface="dashboard")

        assert str(MAX_REVISIONS) in str(error.value)
    finally:
        await _cleanup(issue_id)


# --- the feedback that reaches the next attempt --------------------------------


def test_every_human_turn_is_replayed_not_just_the_latest():
    """"Use the existing helper", said on attempt 1, is still true on attempt
    3. A council prompted with only the most recent sentence re-breaks what
    the earlier ones asked for, which reads as the system ignoring the user."""
    transcript = [
        {"role": "council", "text": "Attempt 1: score 80."},
        {"role": "human", "text": "use the existing helper"},
        {"role": "council", "text": "Attempt 2: score 88."},
        {"role": "human", "text": "and do not add a dependency"},
    ]
    feedback = compile_feedback(transcript)
    assert "use the existing helper" in feedback
    assert "do not add a dependency" in feedback
    assert "Attempt 1: score 80." not in feedback, "the council's own turns are not instructions"


def test_a_thread_with_no_human_turns_compiles_to_nothing():
    """An empty string, not a header with no body -- `prior_rejection` is
    passed as None when empty, and a prompt claiming prior feedback that does
    not exist invents constraints."""
    assert compile_feedback([{"role": "council", "text": "Attempt 1."}]) == ""
    assert compile_feedback([]) == ""
    assert compile_feedback(None) == ""


def test_the_replayed_feedback_is_token_budgeted():
    huge = [{"role": "human", "text": "please rewrite this module " * 400} for _ in range(20)]
    feedback = compile_feedback(huge)
    from app.prompt_compiler import count_tokens

    assert count_tokens(feedback) <= 1300


# --- the proposal summary ------------------------------------------------------


def test_the_proposal_summary_says_what_the_human_is_deciding_on():
    fix = Fix(attempt=2, resolution_score=88)
    summary = proposal_summary(
        fix, "resolved at source", [{"factor": "scope", "note": "one file touched"}]
    )
    assert "Attempt 2" in summary
    assert "88/100" in summary
    # Named for what it expresses. This is a rubric-weighted judgement an
    # Arbiter assigns, not a count of anything, and "score" alone told the
    # reader nothing about which of the two numbers on the page it was.
    assert "resolution confidence" in summary
    assert "resolution score" not in summary
    assert "resolved at source" in summary
    assert "one file touched" in summary
    # It has to invite the third verb, or nobody discovers it exists.
    assert "differently" in summary


# --- decisions -----------------------------------------------------------------


async def test_rejecting_records_the_reason_and_makes_the_issue_actionable_again():
    """Two bugs in one test. Rejection used to record no reason at all, and it
    never reopened the issue -- which left it at `fix-proposed`, where the
    dashboard's retry control (rendered only for `raised`) does not appear.
    Rejecting was a dead end with no way back."""
    from app.graphs.approval_graph import resolve_approval

    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 5)
    try:
        async with async_session() as db:
            result = await resolve_approval(
                db, fix_id, approved=False, actor="dev@example.com", surface="dashboard",
                note="this changes the public API, we cannot ship that",
            )

        assert result["ok"] is True

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            issue = await db.get(Issue, issue_id)
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()

        assert fix.status == FixStatus.REJECTED
        assert "public API" in (fix.decision_note or "")
        assert issue.status == IssueStatus.RAISED
        assert review.status == FixReviewStatus.REJECTED.value
        assert "public API" in review.transcript[-1]["text"]
    finally:
        await _cleanup(issue_id)


async def test_rejecting_touches_nothing_on_github_because_nothing_was_pushed():
    """The reason the deferred push is worth the change: a rejection is now
    purely a database write. Before, it left an open draft PR and a pushed
    branch behind that nothing in the codebase ever cleaned up."""
    from unittest.mock import patch

    from app.graphs.approval_graph import resolve_approval

    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 6)
    try:
        with (
            patch("app.integrations.github_client.push_branch") as push,
            patch("app.integrations.github_client.create_draft_pr") as pr,
            patch("app.integrations.github_client.delete_branch") as delete_branch,
        ):
            async with async_session() as db:
                await resolve_approval(
                    db, fix_id, approved=False, actor="a", surface="dashboard", note="no",
                )

        push.assert_not_called()
        pr.assert_not_called()
        delete_branch.assert_not_called()
    finally:
        await _cleanup(issue_id)


async def test_approving_from_the_web_queues_the_build_instead_of_running_it():
    """The security seam (plan.md §15). Everything up to the decision is a
    database write the web-facing process may do; pushing a branch, opening a
    PR and deploying are privileged work that belongs to the worker."""
    from app.graphs.approval_graph import resolve_approval

    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 7)
    try:
        async with async_session() as db:
            result = await resolve_approval(
                db, fix_id, approved=True, actor="dev@example.com", surface="dashboard",
                note="looks right",
            )

        assert result["queued"] is True
        assert result["status"] == FixStatus.APPROVED.value

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()
            from app.models import WorkItem

            queued = (
                await db.execute(select(WorkItem).where(WorkItem.kind == "apply_approval"))
            ).scalars().all()

        assert fix.status == FixStatus.APPROVED
        assert fix.pr_number is None, "the PR is opened by the worker, after approval"
        assert review.status == FixReviewStatus.APPROVED.value
        assert any(item.payload.get("fix_id") == str(fix_id) for item in queued)
    finally:
        async with async_session() as db:
            from app.models import WorkItem

            await db.execute(delete(WorkItem).where(WorkItem.kind == "apply_approval"))
            await db.commit()
        await _cleanup(issue_id)


async def test_a_decision_on_an_already_decided_fix_is_reported_not_reapplied():
    """Four surfaces can act on one fix -- dashboard, Slack, email, a GitHub
    comment. Whichever lands first wins; the others must say who handled it
    rather than double-applying anything."""
    from app.graphs.approval_graph import resolve_approval

    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 8)
    try:
        async with async_session() as db:
            await resolve_approval(db, fix_id, approved=False, actor="first@example.com", surface="slack", note="no")

        async with async_session() as db:
            second = await resolve_approval(
                db, fix_id, approved=True, actor="second@example.com", surface="dashboard", note="yes",
            )

        assert second["already_handled"] is True
        assert "first@example.com" in second["message"]
    finally:
        await _cleanup(issue_id)


async def test_a_rework_that_fails_to_run_puts_the_thread_back_rather_than_stranding_it():
    """A rework that dies leaves the fix in REVISING with nothing left to move
    it forward, and the UI keeps rendering a working indicator for a job that
    is already dead -- the queue item's own error is not shown anywhere near
    the fix. Found live: the first real revision failed on a reused worktree
    path, and the thread sat on "reworking" indefinitely."""
    from unittest.mock import patch

    from app.enums import FixReviewStatus
    from app.worker import _handle_revise_fix

    issue_id, fix_id = await _seed(ISSUE_NUMBER_BASE + 9)
    try:
        async with async_session() as db:
            await request_revision(db, fix_id, instruction="try again", actor="a", surface="dashboard")

        with patch("app.runner.trigger_fix_council", side_effect=RuntimeError("worktree add failed")):
            with pytest.raises(RuntimeError):
                await _handle_revise_fix(
                    {"fix_id": str(fix_id), "issue_id": str(issue_id), "actor": "a", "surface": "dashboard"}
                )

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()

        assert fix.status == FixStatus.AWAITING_APPROVAL, "the earlier attempt is still decidable"
        assert review.status == FixReviewStatus.AWAITING_DECISION.value
        assert review.transcript[-1]["role"] == "system"
        assert "Nothing changed" in review.transcript[-1]["text"]
    finally:
        await _cleanup(issue_id)

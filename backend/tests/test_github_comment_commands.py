"""`/reject [reason]` and `/revise <instruction>` from a GitHub comment.

A comment is the only approval surface that is naturally free text, so it is
the only one besides the dashboard that can carry the third verb -- Slack's
buttons cannot, and a one-click email link cannot.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session
from app.enums import FixStatus, IssueStatus
from app.models import Fix, FixReview, Issue, Repo
from app.routers.webhooks import _handle_issue_comment

ISSUE_NUMBER = 882000


async def _seed(number: int) -> tuple[uuid.UUID, uuid.UUID]:
    async with async_session() as db:
        repo = (
            await db.execute(select(Repo).where(Repo.github_full_name == settings.fixture_repo))
        ).scalars().first()
        if repo is None:
            repo = Repo(github_full_name=settings.fixture_repo)
            db.add(repo)
            await db.flush()
        issue = Issue(
            repo_id=repo.id, category="ui", origin="detected", github_issue_number=number,
            title="Delete removes the wrong item", severity=2, status=IssueStatus.FIX_PROPOSED,
        )
        db.add(issue)
        await db.flush()
        fix = Fix(
            issue_id=issue.id, resolution_score=90, branch_name=f"whipguard/{number}-x",
            diff="d", attempt=1, status=FixStatus.AWAITING_APPROVAL,
            resolution_rubric={"verdict": "ok", "factors": []},
        )
        db.add(fix)
        await db.flush()
        db.add(FixReview(issue_id=issue.id, current_fix_id=fix.id, transcript=[], attempts=1))
        await db.commit()
        return issue.id, fix.id


async def _cleanup(issue_id: uuid.UUID) -> None:
    from app.models import CalibrationEvent, CouncilRun, MemoryTrace, Notification, OutcomeCheck

    async with async_session() as db:
        fix_ids = (await db.execute(select(Fix.id).where(Fix.issue_id == issue_id))).scalars().all()
        if fix_ids:
            for model in (CalibrationEvent, MemoryTrace, Notification, OutcomeCheck, CouncilRun):
                await db.execute(delete(model).where(model.fix_id.in_(fix_ids)))
        await db.execute(delete(FixReview).where(FixReview.issue_id == issue_id))
        await db.execute(delete(Fix).where(Fix.issue_id == issue_id))
        await db.execute(delete(MemoryTrace).where(MemoryTrace.issue_id == issue_id))
        await db.execute(delete(Issue).where(Issue.id == issue_id))
        await db.commit()


def _payload(body: str, number: int) -> dict:
    return {
        "action": "created",
        "comment": {"body": body, "user": {"login": "a-reviewer"}},
        "issue": {"number": number},
        "repository": {"full_name": settings.fixture_repo},
    }


async def test_reject_carries_the_reason_the_commenter_wrote():
    issue_id, fix_id = await _seed(ISSUE_NUMBER + 1)
    try:
        async with async_session() as db:
            await _handle_issue_comment(db, _payload("/reject this breaks the public API", ISSUE_NUMBER + 1))
            await db.commit()

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            issue = await db.get(Issue, issue_id)

        assert fix.status == FixStatus.REJECTED
        assert fix.decision_note == "this breaks the public API"
        assert fix.approved_by == "a-reviewer"
        assert issue.status == IssueStatus.RAISED
    finally:
        await _cleanup(issue_id)


async def test_a_bare_reject_still_works_and_records_no_reason():
    """The original syntax. Someone who types just `/reject` must not get an
    error because a newer form takes an argument."""
    issue_id, fix_id = await _seed(ISSUE_NUMBER + 2)
    try:
        async with async_session() as db:
            await _handle_issue_comment(db, _payload("/reject", ISSUE_NUMBER + 2))
            await db.commit()

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)

        assert fix.status == FixStatus.REJECTED
        assert fix.decision_note is None
    finally:
        await _cleanup(issue_id)


async def test_revise_queues_a_rework_with_the_instruction():
    issue_id, fix_id = await _seed(ISSUE_NUMBER + 3)
    try:
        with patch("app.work_queue.enqueue", new_callable=AsyncMock) as enqueue:
            async with async_session() as db:
                await _handle_issue_comment(
                    db, _payload("/revise use the existing helper instead", ISSUE_NUMBER + 3)
                )

        assert enqueue.await_count == 1
        assert enqueue.await_args[0][0] == "revise_fix"

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()

        assert fix.status == FixStatus.REVISING
        assert fix.decision_note == "use the existing helper instead"
        assert review.transcript[-1]["text"] == "use the existing helper instead"
        assert review.transcript[-1]["kind"] == "revision-request"
    finally:
        await _cleanup(issue_id)


async def test_revise_with_no_instruction_does_nothing():
    """A bare `/revise` carries nothing to act on. Silently ignoring it beats
    queueing a rework that regenerates the same patch for no reason."""
    issue_id, fix_id = await _seed(ISSUE_NUMBER + 4)
    try:
        with patch("app.work_queue.enqueue", new_callable=AsyncMock) as enqueue:
            async with async_session() as db:
                await _handle_issue_comment(db, _payload("/revise   ", ISSUE_NUMBER + 4))

        enqueue.assert_not_awaited()
        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
        assert fix.status == FixStatus.AWAITING_APPROVAL
    finally:
        await _cleanup(issue_id)


async def test_a_refused_revision_is_answered_on_the_thread():
    """A webhook that silently does nothing is indistinguishable from a broken
    one. If the fix has already moved on, say so where the person is standing."""
    issue_id, fix_id = await _seed(ISSUE_NUMBER + 5)
    try:
        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            fix.status = FixStatus.APPROVED
            await db.commit()

        with patch("app.integrations.github_client.comment_issue") as comment:
            async with async_session() as db:
                await _handle_issue_comment(db, _payload("/revise try again", ISSUE_NUMBER + 5))

        comment.assert_called_once()
        assert "no longer awaiting a decision" in comment.call_args[0][2]
    finally:
        await _cleanup(issue_id)


async def test_ordinary_comments_are_ignored():
    issue_id, fix_id = await _seed(ISSUE_NUMBER + 6)
    try:
        async with async_session() as db:
            await _handle_issue_comment(db, _payload("looks good to me", ISSUE_NUMBER + 6))
            await _handle_issue_comment(db, _payload("/rejected-by-accident", ISSUE_NUMBER + 6))
            await db.commit()

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
        assert fix.status == FixStatus.AWAITING_APPROVAL
    finally:
        await _cleanup(issue_id)

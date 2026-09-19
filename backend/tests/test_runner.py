import uuid

from unittest.mock import MagicMock, patch

from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session
from app.enums import FixStatus, IssueStatus
from app.models import Fix, FixReview, Issue, Repo
from app.runner import trigger_fix_council

ABOVE_THRESHOLD_ISSUE_NUMBER = 880001
BELOW_THRESHOLD_ISSUE_NUMBER = 880002


async def _get_or_create_repo(db) -> Repo:
    repo = (
        await db.execute(select(Repo).where(Repo.github_full_name == settings.fixture_repo))
    ).scalars().first()
    if repo is None:
        repo = Repo(github_full_name=settings.fixture_repo)
        db.add(repo)
        await db.flush()
    return repo


async def _make_issue(github_issue_number: int) -> uuid.UUID:
    async with async_session() as db:
        repo = await _get_or_create_repo(db)
        issue = Issue(
            repo_id=repo.id,
            category="ui",
            origin="filed-externally",
            github_issue_number=github_issue_number,
            title="Delete removes wrong item",
            severity=2,
            status=IssueStatus.RAISED,
        )
        db.add(issue)
        await db.commit()
        return issue.id


async def _cleanup(issue_id: uuid.UUID) -> None:
    async with async_session() as db:
        await db.execute(delete(FixReview).where(FixReview.issue_id == issue_id))
        await db.execute(delete(Fix).where(Fix.issue_id == issue_id))
        await db.execute(delete(Issue).where(Issue.id == issue_id))
        await db.commit()


async def test_a_proposed_fix_touches_nothing_on_github():
    """The gate moved. A proposal is now an offer a human can refuse without
    the repository ever having heard about it -- so nothing is pushed and no
    PR is opened until approval (app/graphs/approval_graph.py does both).

    Before this, rejecting a fix left an open draft PR and a pushed branch
    behind forever, because nothing in the codebase ever closed either."""
    issue_id = await _make_issue(ABOVE_THRESHOLD_ISSUE_NUMBER)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "score": 92,
            "rubric": [{"factor": "root cause", "note": "addressed at source"}],
            "verdict": "fixed it",
            "diff": "diff --git a/app.js b/app.js",
        }

        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
            patch("app.integrations.github_client.push_branch") as mock_push,
            patch("app.integrations.github_client.create_draft_pr") as mock_pr,
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            fix = (await db.execute(select(Fix).where(Fix.issue_id == issue_id))).scalars().first()
            issue = await db.get(Issue, issue_id)

        assert fix is not None
        assert fix.status == FixStatus.AWAITING_APPROVAL
        assert fix.pr_number is None
        mock_push.assert_not_called()
        mock_pr.assert_not_called()
        assert issue.status == IssueStatus.FIX_PROPOSED
    finally:
        await _cleanup(issue_id)


async def test_the_diff_is_persisted_because_there_is_no_pr_to_read_it_from():
    """With the PR deferred to approval, this column IS the review surface.
    An unsaved diff would leave the reviewer deciding on a score alone."""
    issue_id = await _make_issue(ABOVE_THRESHOLD_ISSUE_NUMBER + 10)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "score": 88, "rubric": [], "verdict": "ok",
            "diff": "diff --git a/cart.js b/cart.js\n-  items.splice(i, 1)\n+  items.splice(index, 1)",
        }
        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            fix = (await db.execute(select(Fix).where(Fix.issue_id == issue_id))).scalars().first()

        assert "items.splice(index, 1)" in (fix.diff or "")
    finally:
        await _cleanup(issue_id)


async def test_a_proposal_opens_a_review_thread_a_human_can_reply_to():
    issue_id = await _make_issue(ABOVE_THRESHOLD_ISSUE_NUMBER + 20)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "score": 91,
            "rubric": [{"factor": "scope", "note": "one file touched"}],
            "verdict": "resolved",
            "diff": "diff --git a/app.js b/app.js",
        }
        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()

        assert review is not None
        assert review.attempts == 1
        assert review.transcript[0]["role"] == "council"
        assert review.transcript[0]["kind"] == "proposal"
        # The summary is what the human actually reads and replies to, so it
        # has to carry the verdict, not just a number.
        assert "resolved" in review.transcript[0]["text"]
    finally:
        await _cleanup(issue_id)


async def test_a_revision_supersedes_the_earlier_attempt_rather_than_replacing_it():
    """The rejected approach and the reason for rejecting it are the highest
    signal this system produces. A revision that overwrote the previous row
    would delete exactly that."""
    issue_id = await _make_issue(ABOVE_THRESHOLD_ISSUE_NUMBER + 30)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"score": 90, "rubric": [], "verdict": "ok", "diff": "d1"}
        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            first = (await db.execute(select(Fix).where(Fix.issue_id == issue_id))).scalars().first()
            first_id = first.id

        mock_graph.invoke.return_value = {"score": 94, "rubric": [], "verdict": "better", "diff": "d2"}
        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
        ):
            await trigger_fix_council(
                issue_id, feedback="use the existing helper", attempt=2, supersedes=first_id
            )

        async with async_session() as db:
            first = await db.get(Fix, first_id)
            fixes = (
                await db.execute(select(Fix).where(Fix.issue_id == issue_id).order_by(Fix.created_at))
            ).scalars().all()
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()

        assert len(fixes) == 2
        assert first.status == FixStatus.SUPERSEDED
        assert first.superseded_by_id == fixes[1].id
        assert fixes[1].attempt == 2
        assert fixes[1].status == FixStatus.AWAITING_APPROVAL
        assert review.current_fix_id == fixes[1].id
        assert review.attempts == 2
    finally:
        await _cleanup(issue_id)


async def test_the_reviewers_instructions_reach_the_council_as_prior_rejection():
    """`prior_rejection` has been threaded through FixCouncilState and into the
    patch prompt since the council was written, and nothing ever populated it
    -- runner.py passed a hardcoded None. This is that wire."""
    issue_id = await _make_issue(ABOVE_THRESHOLD_ISSUE_NUMBER + 40)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"score": 90, "rubric": [], "verdict": "ok", "diff": "d"}
        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
        ):
            await trigger_fix_council(issue_id, feedback="do not add a dependency", attempt=3)

        state = mock_graph.invoke.call_args[0][0]
        assert state["prior_rejection"] == "do not add a dependency"
        assert state["attempt"] == 3
    finally:
        await _cleanup(issue_id)


async def test_trigger_fix_council_below_threshold_rejects_and_never_pushes_or_opens_pr():
    issue_id = await _make_issue(BELOW_THRESHOLD_ISSUE_NUMBER)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "score": 40,
            "rubric": [],
            "verdict": "weak evidence",
            "diff": "",
        }

        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/2"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
            patch("app.integrations.github_client.push_branch") as mock_push,
            patch("app.integrations.github_client.create_draft_pr") as mock_pr,
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            fix = (
                await db.execute(select(Fix).where(Fix.issue_id == issue_id))
            ).scalars().first()
            issue = await db.get(Issue, issue_id)

        assert fix is not None
        assert fix.status == FixStatus.REJECTED
        mock_push.assert_not_called()
        mock_pr.assert_not_called()
        assert issue.status == IssueStatus.RAISED  # unchanged, no fix was proposed
    finally:
        await _cleanup(issue_id)


async def test_trigger_fix_council_returns_cleanly_for_unknown_issue_id():
    missing_issue_id = uuid.uuid4()

    with (
        patch("app.runner.ensure_mirror") as mock_ensure_mirror,
        patch("app.runner.create_worktree") as mock_create_worktree,
    ):
        result = await trigger_fix_council(missing_issue_id)  # must not raise

    assert result is None
    mock_ensure_mirror.assert_not_called()
    mock_create_worktree.assert_not_called()

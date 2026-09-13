import uuid

from unittest.mock import MagicMock, patch

from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session
from app.enums import FixStatus, IssueStatus
from app.models import Fix, Issue, Repo
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
        await db.execute(delete(Fix).where(Fix.issue_id == issue_id))
        await db.execute(delete(Issue).where(Issue.id == issue_id))
        await db.commit()


async def test_trigger_fix_council_above_threshold_pushes_branch_and_opens_draft_pr():
    issue_id = await _make_issue(ABOVE_THRESHOLD_ISSUE_NUMBER)
    try:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "score": 92,
            "rubric": [],
            "verdict": "fixed it",
            "diff": "diff --git a/app.js b/app.js",
        }

        with (
            patch("app.runner.ensure_mirror", return_value="/tmp/mirror"),
            patch("app.runner.create_worktree", return_value="/tmp/mirror/fixes/1"),
            patch("app.graphs.fix_council.build_fix_council_graph", return_value=mock_graph),
            patch("app.runner.github_client.push_branch") as mock_push,
            patch("app.runner.github_client.create_draft_pr", return_value=77) as mock_pr,
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            fix = (
                await db.execute(select(Fix).where(Fix.issue_id == issue_id))
            ).scalars().first()
            issue = await db.get(Issue, issue_id)

        assert fix is not None
        assert fix.status == FixStatus.AWAITING_APPROVAL
        assert fix.pr_number == 77
        mock_push.assert_called_once()
        mock_pr.assert_called_once()
        assert issue.status == IssueStatus.FIX_PROPOSED
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
            patch("app.runner.github_client.push_branch") as mock_push,
            patch("app.runner.github_client.create_draft_pr") as mock_pr,
        ):
            await trigger_fix_council(issue_id)

        async with async_session() as db:
            fix = (
                await db.execute(select(Fix).where(Fix.issue_id == issue_id))
            ).scalars().first()
            issue = await db.get(Issue, issue_id)

        assert fix is not None
        assert fix.status == FixStatus.REJECTED
        # A rejected fix must never push a branch or open a PR.
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

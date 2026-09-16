import asyncio

from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session
from app.enums import IssueStatus
from app.models import Issue, Repo
from app.poller import _poll_once

# Large, made-up GitHub issue numbers that won't collide with real fixture-repo
# issues (the demo repo's real issues are single/double digit).
NEW_ISSUE_NUMBER = 990001
ALREADY_TRACKED_ISSUE_NUMBER = 990002


async def _get_or_create_repo(db) -> Repo:
    repo = (
        await db.execute(select(Repo).where(Repo.github_full_name == settings.fixture_repo))
    ).scalars().first()
    if repo is None:
        repo = Repo(github_full_name=settings.fixture_repo)
        db.add(repo)
        await db.flush()
    return repo


async def _cleanup_issue(github_issue_number: int) -> None:
    async with async_session() as db:
        await db.execute(delete(Issue).where(Issue.github_issue_number == github_issue_number))
        await db.commit()


async def test_poll_once_creates_issue_for_new_labeled_github_issue():
    """A GitHub issue carrying whipguard:fix-me that isn't tracked yet should
    become an Issue row with origin=filed-externally, and should trigger the
    Fix Council as a background task."""
    await _cleanup_issue(NEW_ISSUE_NUMBER)
    try:
        with (
            patch("app.poller.github_client.list_issues_with_label") as mock_list,
            patch("app.poller.enqueue", new_callable=AsyncMock) as mock_trigger,
        ):
            mock_list.return_value = [
                {"number": NEW_ISSUE_NUMBER, "title": "Delete removes wrong item"}
            ]

            await _poll_once()
            await asyncio.sleep(0)  # let the asyncio.create_task'd coroutine run

        async with async_session() as db:
            issue = (
                await db.execute(select(Issue).where(Issue.github_issue_number == NEW_ISSUE_NUMBER))
            ).scalars().first()

        assert issue is not None
        assert issue.origin == "filed-externally"
        assert issue.github_issue_number == NEW_ISSUE_NUMBER
        assert issue.status == IssueStatus.RAISED

        # Queued for the privileged worker rather than run in-process --
        # see app/work_queue.py for why the poller cannot execute it itself.
        mock_trigger.assert_called_once_with("fix_council", {"issue_id": str(issue.id)})
    finally:
        await _cleanup_issue(NEW_ISSUE_NUMBER)


async def test_poll_once_does_not_duplicate_or_retrigger_an_already_tracked_issue():
    """Idempotency: an issue already tracked in the DB (by github_issue_number)
    must not be re-created and must not fire the Fix Council a second time —
    this is what makes it safe to run _poll_once on a fixed interval forever."""
    await _cleanup_issue(ALREADY_TRACKED_ISSUE_NUMBER)
    async with async_session() as db:
        repo = await _get_or_create_repo(db)
        existing = Issue(
            repo_id=repo.id,
            category="ui",
            origin="filed-externally",
            github_issue_number=ALREADY_TRACKED_ISSUE_NUMBER,
            title="Already tracked issue",
            severity=2,
            status=IssueStatus.RAISED,
        )
        db.add(existing)
        await db.commit()

    try:
        with (
            patch("app.poller.github_client.list_issues_with_label") as mock_list,
            patch("app.poller.enqueue", new_callable=AsyncMock) as mock_trigger,
        ):
            mock_list.return_value = [
                {"number": ALREADY_TRACKED_ISSUE_NUMBER, "title": "Already tracked issue"}
            ]

            await _poll_once()
            await asyncio.sleep(0)

        async with async_session() as db:
            issues = (
                await db.execute(
                    select(Issue).where(Issue.github_issue_number == ALREADY_TRACKED_ISSUE_NUMBER)
                )
            ).scalars().all()

        assert len(issues) == 1
        mock_trigger.assert_not_called()
    finally:
        await _cleanup_issue(ALREADY_TRACKED_ISSUE_NUMBER)


async def test_poll_once_swallows_github_api_errors_without_propagating():
    """plan.md §15: a GitHub API error fetching labeled issues fails closed —
    one cycle's failure must not raise out of _poll_once (which would otherwise
    kill the whole poller loop if this try/except regressed)."""
    with patch(
        "app.poller.github_client.list_issues_with_label",
        side_effect=RuntimeError("GitHub API is down"),
    ):
        result = await _poll_once()  # must not raise

    assert result is None

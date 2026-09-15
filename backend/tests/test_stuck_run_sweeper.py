"""Real Postgres integration tests (this project's established pattern --
see test_poller.py) for the stuck-run sweeper: a Fix left sitting in an
in-flight status (APPROVED/IN_PROGRESS/DEPLOYED) past
STUCK_RUN_THRESHOLD_SECONDS must be swept to verification-failed with its
issue reopened for a retry; one well within the threshold must be left
untouched."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.config import settings
from app.db import async_session
from app.enums import FixStatus, IssueStatus
from app.graphs.approval_graph import STUCK_RUN_THRESHOLD_SECONDS
from app.models import CalibrationEvent, Fix, Issue, Repo
from app.stuck_run_sweeper import _sweep_once

# Large, made-up numbers so these never collide with real fixture-repo issues.
STUCK_ISSUE_NUMBER = 990101
FRESH_ISSUE_NUMBER = 990102


async def _get_or_create_repo(db) -> Repo:
    repo = (
        await db.execute(select(Repo).where(Repo.github_full_name == settings.fixture_repo))
    ).scalars().first()
    if repo is None:
        repo = Repo(github_full_name=settings.fixture_repo)
        db.add(repo)
        await db.flush()
    return repo


async def _make_in_flight_fix(github_issue_number: int, status: FixStatus) -> tuple:
    async with async_session() as db:
        repo = await _get_or_create_repo(db)
        issue = Issue(
            repo_id=repo.id,
            category="ui",
            origin="detected",
            github_issue_number=github_issue_number,
            title="Sweeper test issue",
            severity=2,
            status=IssueStatus.FIX_PROPOSED,
        )
        db.add(issue)
        await db.flush()

        fix = Fix(issue_id=issue.id, branch_name="whipguard/sweeper-test", status=status)
        db.add(fix)
        await db.commit()
        return issue.id, fix.id


async def _backdate(fix_id, seconds_ago: int) -> None:
    async with async_session() as db:
        stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        await db.execute(update(Fix).where(Fix.id == fix_id).values(updated_at=stamp))
        await db.commit()


async def _cleanup(github_issue_number: int) -> None:
    async with async_session() as db:
        issue = (
            await db.execute(select(Issue).where(Issue.github_issue_number == github_issue_number))
        ).scalars().first()
        if issue:
            await db.execute(
                delete(CalibrationEvent).where(
                    CalibrationEvent.fix_id.in_(select(Fix.id).where(Fix.issue_id == issue.id))
                )
            )
            await db.execute(delete(Fix).where(Fix.issue_id == issue.id))
            await db.execute(delete(Issue).where(Issue.id == issue.id))
            await db.commit()


async def test_sweep_flips_a_genuinely_stuck_fix_and_reopens_its_issue():
    await _cleanup(STUCK_ISSUE_NUMBER)
    try:
        issue_id, fix_id = await _make_in_flight_fix(STUCK_ISSUE_NUMBER, FixStatus.IN_PROGRESS)
        await _backdate(fix_id, STUCK_RUN_THRESHOLD_SECONDS + 60)

        await _sweep_once()

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            issue = await db.get(Issue, issue_id)

        assert fix.status == FixStatus.VERIFICATION_FAILED
        assert issue.status == IssueStatus.RAISED
    finally:
        await _cleanup(STUCK_ISSUE_NUMBER)


async def test_sweep_leaves_a_fresh_in_flight_fix_alone():
    """Still well within the budget -- a healthy run genuinely mid-flow must
    never get swept out from under itself."""
    await _cleanup(FRESH_ISSUE_NUMBER)
    try:
        issue_id, fix_id = await _make_in_flight_fix(FRESH_ISSUE_NUMBER, FixStatus.DEPLOYED)

        await _sweep_once()

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            issue = await db.get(Issue, issue_id)

        assert fix.status == FixStatus.DEPLOYED
        assert issue.status == IssueStatus.FIX_PROPOSED
    finally:
        await _cleanup(FRESH_ISSUE_NUMBER)

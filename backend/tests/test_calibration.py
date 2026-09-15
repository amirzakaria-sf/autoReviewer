"""Real Postgres integration tests (see test_poller.py / test_stuck_run_sweeper.py
for this project's established pattern) for the calibration loop: enough
negative outcomes for a (repo, category) pair must raise its resolution
threshold, enough clean outcomes must lower it, and a sample below
MIN_SAMPLE_SIZE must never trigger an adjustment at all."""

from __future__ import annotations

from sqlalchemy import delete, select

from app.calibration import (
    HIGH_NEGATIVE_RATE,
    LOOKBACK_EVENTS,
    MIN_SAMPLE_SIZE,
    _calibrate_one,
)
from app.config import settings
from app.db import async_session
from app.enums import FixStatus, IssueStatus
from app.models import AuditLog, CalibrationEvent, Fix, Issue, Repo

CATEGORY = "backend"  # registry default resolution_threshold=85, see app/categories.py


async def _fresh_repo(github_full_name: str) -> Repo:
    async with async_session() as db:
        existing = (
            await db.execute(select(Repo).where(Repo.github_full_name == github_full_name))
        ).scalars().first()
        if existing:
            await db.execute(delete(AuditLog).where(AuditLog.target_id.like(f"{existing.id}:%")))
            await db.execute(
                delete(CalibrationEvent).where(
                    CalibrationEvent.fix_id.in_(
                        select(Fix.id).join(Issue, Issue.id == Fix.issue_id).where(Issue.repo_id == existing.id)
                    )
                )
            )
            await db.execute(delete(Fix).where(Fix.issue_id.in_(select(Issue.id).where(Issue.repo_id == existing.id))))
            await db.execute(delete(Issue).where(Issue.repo_id == existing.id))
            await db.execute(delete(Repo).where(Repo.id == existing.id))
            await db.commit()

        repo = Repo(github_full_name=github_full_name)
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return repo


async def _seed_events(repo_id, outcomes: list[str]) -> None:
    async with async_session() as db:
        for i, outcome in enumerate(outcomes):
            issue = Issue(
                repo_id=repo_id, category=CATEGORY, origin="detected",
                title=f"calibration seed {i}", severity=2, status=IssueStatus.CLOSED,
            )
            db.add(issue)
            await db.flush()
            fix = Fix(issue_id=issue.id, branch_name=f"whipguard/cal-{i}", status=FixStatus.MERGED)
            db.add(fix)
            await db.flush()
            db.add(CalibrationEvent(fix_id=fix.id, outcome=outcome, detail={}))
        await db.commit()


async def test_high_negative_rate_raises_the_threshold():
    repo = await _fresh_repo("calibration-test/high-negative")
    outcomes = ["rejected_by_human"] * 3 + ["merged"] * 2  # 60% negative, over HIGH_NEGATIVE_RATE
    assert len(outcomes) >= MIN_SAMPLE_SIZE
    await _seed_events(repo.id, outcomes)

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        before = (repo.thresholds or {}).get(CATEGORY, {}).get("resolution", 85)
        await _calibrate_one(db, repo, CATEGORY)
        await db.commit()

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        after = repo.thresholds[CATEGORY]["resolution"]

    assert after > before

    async with async_session() as db:
        log = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == "threshold_adjusted", AuditLog.target_id.like(f"{repo.id}:%"))
                .order_by(AuditLog.created_at.desc())
            )
        ).scalars().first()
    assert log is not None
    assert log.metadata_json["new_resolution_threshold"] == after


async def test_clean_history_lowers_the_threshold():
    repo = await _fresh_repo("calibration-test/all-clean")
    outcomes = ["merged"] * MIN_SAMPLE_SIZE
    await _seed_events(repo.id, outcomes)

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        repo.thresholds = {CATEGORY: {"resolution": 90}}
        await db.commit()

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        await _calibrate_one(db, repo, CATEGORY)
        await db.commit()

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        after = repo.thresholds[CATEGORY]["resolution"]

    assert after < 90


async def test_below_minimum_sample_size_never_adjusts():
    repo = await _fresh_repo("calibration-test/too-few")
    outcomes = ["rejected_by_human"] * (MIN_SAMPLE_SIZE - 1)
    await _seed_events(repo.id, outcomes)

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        before = repo.thresholds
        await _calibrate_one(db, repo, CATEGORY)
        await db.commit()

    async with async_session() as db:
        repo = await db.get(Repo, repo.id)
        assert repo.thresholds == before

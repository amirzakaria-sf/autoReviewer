"""Calibration (plan.md §5.4 / §14 point 7): real post-ship outcomes feed
threshold tuning IN CODE, never back into a prompt -- letting a model see
"your last three scores were too generous" and adjust its own future score
is the self-grading problem this whole design exists to avoid.

Two halves:
- record_calibration_event(): called from every place in the codebase that
  observes a real outcome for a shipped fix (webhooks.py, approval_graph.py,
  stuck_run_sweeper.py) -- a cheap, mechanical write, never a judgment call.
- run_calibration_pass(): a periodic job (see main.py's lifespan) that reads
  recent outcomes per (repo, category) and nudges Repo.thresholds -- bounded,
  gated on a minimum sample size, and only ever a few points per pass.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.categories import CATEGORY_REGISTRY
from app.db import async_session
from app.models import AuditLog, CalibrationEvent, Fix, Issue, Repo

logger = logging.getLogger("whipguard.calibration")

# Outcomes that mean the fix did NOT actually hold up -- a human rejected it,
# it failed its own verification, the outcome checker caught a mismatch, or
# it never finished and had to be swept. "merged" with no later "reopened"
# is the only positive signal; everything else here is negative.
NEGATIVE_OUTCOMES = {
    "rejected_by_human", "rejected_on_github", "verification_failed",
    "outcome_check_failed", "stuck_timeout", "reopened",
}

CALIBRATION_INTERVAL_SECONDS = 1800  # 30 min -- outcomes accrue slowly, no need to poll often
MIN_SAMPLE_SIZE = 5  # never adjust off fewer real outcomes than this -- a noisy small sample
LOOKBACK_EVENTS = 20  # per (repo, category), most recent N only -- old outcomes shouldn't anchor forever
ADJUSTMENT_STEP = 3  # small, bounded nudge per pass -- a threshold hunts slowly, never lurches
HIGH_NEGATIVE_RATE = 0.4  # raise the bar (be more conservative) above this
LOW_NEGATIVE_RATE = 0.05  # ease the bar (be more permissive) below this
THRESHOLD_FLOOR = 50
THRESHOLD_CEILING = 95


async def record_calibration_event(db, fix_id, outcome: str, detail: dict | None = None) -> None:
    db.add(CalibrationEvent(fix_id=fix_id, outcome=outcome, detail=detail or {}))


async def run_calibration_loop() -> None:
    while True:
        try:
            await run_calibration_pass()
        except Exception:
            logger.exception("calibration pass failed, will retry next interval")
        await asyncio.sleep(CALIBRATION_INTERVAL_SECONDS)


async def run_calibration_pass() -> None:
    async with async_session() as db:
        repos = (await db.execute(select(Repo))).scalars().all()
        for repo in repos:
            for category in CATEGORY_REGISTRY:
                await _calibrate_one(db, repo, category)
        await db.commit()

    from app.ask_mode import learn_ask_mode_pass

    await learn_ask_mode_pass()


async def _calibrate_one(db, repo: Repo, category: str) -> None:
    rows = (
        await db.execute(
            select(CalibrationEvent.outcome)
            .join(Fix, Fix.id == CalibrationEvent.fix_id)
            .join(Issue, Issue.id == Fix.issue_id)
            .where(Issue.repo_id == repo.id, Issue.category == category)
            .order_by(CalibrationEvent.observed_at.desc())
            .limit(LOOKBACK_EVENTS)
        )
    ).scalars().all()

    if len(rows) < MIN_SAMPLE_SIZE:
        return

    negative_rate = sum(1 for o in rows if o in NEGATIVE_OUTCOMES) / len(rows)

    thresholds = dict(repo.thresholds or {})
    category_thresholds = dict(thresholds.get(category, {}))
    current = category_thresholds.get("resolution", CATEGORY_REGISTRY[category].resolution_threshold)

    if negative_rate >= HIGH_NEGATIVE_RATE:
        new_value = min(THRESHOLD_CEILING, current + ADJUSTMENT_STEP)
    elif negative_rate <= LOW_NEGATIVE_RATE:
        new_value = max(THRESHOLD_FLOOR, current - ADJUSTMENT_STEP)
    else:
        return

    if new_value == current:
        return

    category_thresholds["resolution"] = new_value
    thresholds[category] = category_thresholds
    repo.thresholds = thresholds

    db.add(
        AuditLog(
            actor_surface="calibration_loop",
            action="threshold_adjusted",
            target_type="repo_category",
            target_id=f"{repo.id}:{category}",
            metadata_json={
                "category": category,
                "previous_resolution_threshold": current,
                "new_resolution_threshold": new_value,
                "negative_rate": round(negative_rate, 2),
                "sample_size": len(rows),
            },
        )
    )
    logger.info(
        "calibration: repo=%s category=%s resolution_threshold %s -> %s (negative_rate=%.2f, n=%d)",
        repo.id, category, current, new_value, negative_rate, len(rows),
    )

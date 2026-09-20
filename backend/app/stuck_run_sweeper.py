"""Stuck-run sweeper (plan.md §14, point 6).

A Fix sitting in an in-flight status (APPROVED / IN_PROGRESS / DEPLOYED --
see approval_graph.IN_FLIGHT_FIX_STATUSES) represents work resolve_approval
is supposedly still doing. If the backend process dies mid-flow (an OOM
kill, a container restart) or an exception escapes unhandled, nothing ever
moves that row forward again -- it sits there indefinitely, which reads on
the dashboard as work still happening when the truth is nothing is.

Every blocking step inside that flow now carries an explicit timeout (git
fetch/merge-base/push, the sandbox runs, the Cloudflare deploy -- see the
budget comment on STUCK_RUN_THRESHOLD_SECONDS in approval_graph.py), so a
row that has sat in an in-flight status longer than that budget cannot still
be genuinely, healthily in progress -- every constituent call would already
have hit its own ceiling and raised. Past the threshold, "confirmed not
still running" follows from that budget, not from guessing.

Runs as an asyncio background task, not a separate worker process --
matches app/poller.py's shape, which this project already established as
its pattern for periodic background work.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.calibration import record_calibration_event
from app.db import async_session
from app.enums import FixStatus
from app.graphs.approval_graph import IN_FLIGHT_FIX_STATUSES, STUCK_RUN_THRESHOLD_SECONDS, _reopen_issue_for_retry
from app.models import Fix, Issue
from app.routers.ws import emit_event

logger = logging.getLogger("whipguard.stuck_run_sweeper")

SWEEP_INTERVAL_SECONDS = 60


async def sweep_stuck_runs() -> None:
    while True:
        try:
            await _sweep_once()
        except Exception:
            logger.exception("stuck-run sweep: one pass failed, will retry next interval")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


async def _sweep_once() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STUCK_RUN_THRESHOLD_SECONDS)

    async with async_session() as db:
        stuck = (
            await db.execute(
                select(Fix).where(Fix.status.in_(IN_FLIGHT_FIX_STATUSES), Fix.updated_at < cutoff)
            )
        ).scalars().all()

        for fix in stuck:
            # Captured before commit() -- AsyncSession's default
            # expire_on_commit means every mapped attribute (including these)
            # goes stale the instant commit() returns, and reading a stale
            # attribute triggers an implicit synchronous reload that raises
            # MissingGreenlet outside an active async context (found by
            # actually running this once, per plan.md's own §9.8 principle).
            fix_id = fix.id
            previous_status = fix.status.value
            last_updated = fix.updated_at
            issue = await db.get(Issue, fix.issue_id)
            issue_title = issue.title if issue else str(fix_id)

            fix.status = FixStatus.VERIFICATION_FAILED
            _reopen_issue_for_retry(issue)
            await record_calibration_event(db, fix_id=fix_id, outcome="stuck_timeout", detail={"stuck_at": previous_status})
            await db.commit()

            logger.warning(
                "fix %s stuck at %s for over %ss (last update %s) -- swept to verification-failed",
                fix_id, previous_status, STUCK_RUN_THRESHOLD_SECONDS, last_updated,
            )
            emit_event({
                "type": "run", "kind": "stuck_run_sweep", "status": "done",
                # Explicit rather than ambient: the sweeper walks several
                # fixes in one pass, each belonging to a different repository.
                "repo_id": str(issue.repo_id) if issue is not None and issue.repo_id else "",
                "message": f"Fix stuck at '{previous_status}' for over {STUCK_RUN_THRESHOLD_SECONDS}s "
                           f"({issue_title}) -- issue reopened for a fresh attempt",
            })

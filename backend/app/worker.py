"""The privileged worker: the only process that executes repository code.

plan.md §15's first rule, made real. This process holds the Docker socket,
the deploy credentials and the workspace checkouts. It has **no inbound
port** and nothing can call it -- it polls `work_items` and decides for
itself what to do with what it finds. The dashboard's entire power over it
is to insert a row.

Everything that was previously an `asyncio.create_task(...)` inside an HTTP
handler now enqueues instead, so a compromised web process can ask for work
that was already possible through the UI and nothing more. It cannot choose
the command, the image, the mount, or the credentials.

Run as: `python -m app.worker`
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid

from app.config import settings
from app.work_queue import claim_next, connection, enqueue, finish, requeue_stale

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("whipguard.worker")

POLL_INTERVAL_SECONDS = 2.0
STALE_SWEEP_EVERY_SECONDS = 300

_shutdown = asyncio.Event()


async def _handle_scan_repo(payload: dict) -> None:
    from app.db import async_session
    from app.graphs.bug_council import run_and_persist
    from app.models import Repo

    repo_id = uuid.UUID(payload["repo_id"])
    for category in payload.get("categories") or []:
        async with async_session() as db:
            repo = await db.get(Repo, repo_id)
            if repo is None:
                return
            await run_and_persist(db, repo, category=category)


async def _handle_fix_council(payload: dict) -> None:
    from app.runner import trigger_fix_council

    await trigger_fix_council(uuid.UUID(payload["issue_id"]))


async def _handle_resume_human_input(payload: dict) -> None:
    from app.db import async_session
    from app.graphs.bug_council import resume_with_clarification_answer
    from app.models import HumanInputRequest

    async with async_session() as db:
        request = await db.get(HumanInputRequest, uuid.UUID(payload["request_id"]))
        if request is None:
            return
        await resume_with_clarification_answer(db, request, payload.get("answer", ""))


async def _handle_revise_fix(payload: dict) -> None:
    """Re-run the Fix Council with the reviewer's instructions in context.

    Runs HERE rather than in the web process for the same reason approval
    application does: it clones, patches and executes repository code inside
    a sandbox container, which needs the Docker socket the web half
    deliberately does not hold (plan.md §15).
    """
    from sqlalchemy import select

    from app.db import async_session
    from app.enums import FixStatus
    from app.fix_review import compile_feedback
    from app.models import Fix, FixReview
    from app.runner import trigger_fix_council

    fix_id = uuid.UUID(payload["fix_id"])
    issue_id = uuid.UUID(payload["issue_id"])

    async with async_session() as db:
        review = (
            await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
        ).scalars().first()
        feedback = compile_feedback(review.transcript if review else [])
        attempt = (review.attempts or 1) + 1 if review else 2

    try:
        await trigger_fix_council(issue_id, feedback=feedback, attempt=attempt, supersedes=fix_id)
    except Exception:
        # Both the fix AND the thread were moved to "revising" before this was
        # queued, so a failure here strands them with nothing left to move
        # them forward -- invisible, because the queue item's own error is not
        # shown next to the fix, and the UI keeps rendering a working
        # indicator for a rework that already died. Put both back, and say so
        # on the thread rather than leaving the human to wonder.
        from app.enums import FixReviewStatus
        from app.fix_review import turn

        async with async_session() as db:
            fix = await db.get(Fix, fix_id)
            if fix is not None and fix.status == FixStatus.REVISING:
                fix.status = FixStatus.AWAITING_APPROVAL
            review = (
                await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
            ).scalars().first()
            if review is not None and review.status == FixReviewStatus.REVISING.value:
                review.status = FixReviewStatus.AWAITING_DECISION.value
                review.transcript = [
                    *(review.transcript or []),
                    turn(
                        "system",
                        "The rework failed to run. Nothing changed -- the previous attempt is still "
                        "on the table. Try again, or reject it.",
                        kind="error",
                    ),
                ]
            await db.commit()
        raise


async def _handle_apply_approval(payload: dict) -> None:
    from app.db import async_session
    from app.graphs.approval_graph import resolve_approval

    async with async_session() as db:
        await resolve_approval(
            db,
            uuid.UUID(payload["fix_id"]),
            # Only approvals are ever queued -- a rejection is entirely a
            # database write and completes in the web process. Defaulting to
            # True rather than KeyError-ing keeps items enqueued by the older
            # payload shape (which omitted this key) working after a deploy.
            bool(payload.get("approved", True)),
            payload.get("actor", "unknown"),
            payload.get("surface", "dashboard"),
            apply=True,
            note=payload.get("note", ""),
        )


async def _handle_reindex_repo(payload: dict) -> None:
    from app.graph_index import index_repo_symbols
    from app.retrieval import index_repo_files
    from app.sandbox.worktree import ensure_mirror, ensure_read_worktree

    repo_id = uuid.UUID(payload["repo_id"])
    mirror = await asyncio.to_thread(ensure_mirror, payload["repo_full_name"])

    # Counsel reads this tree. Refreshed here because this is the same moment
    # the indexes it queries are rebuilt -- letting the two drift would mean
    # the agent citing line numbers from a different revision than it ranked.
    tree = await asyncio.to_thread(ensure_read_worktree, mirror)

    await asyncio.to_thread(index_repo_files, repo_id, str(tree))
    await asyncio.to_thread(index_repo_symbols, repo_id, str(tree))


async def _handle_redeploy(payload: dict) -> None:
    """Runs deploy.sh detached so it outlives this worker -- the script
    recreates containers, and on some paths that includes this one."""
    import subprocess
    from pathlib import Path

    if not settings.repo_root:
        raise RuntimeError("REPO_ROOT is not configured on the worker")
    script = Path(settings.repo_root) / "deploy.sh"
    if not script.exists():
        raise RuntimeError(f"deploy.sh not found at {script}")

    subprocess.Popen(
        ["bash", str(script)],
        cwd=settings.repo_root,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("redeploy launched by %s", payload.get("by", "unknown"))


async def _handle_run_eval(payload: dict) -> dict:
    """The detection eval: real sandboxed detectors against controlled
    worktrees. Returns the report so it lands on the work item, which is how
    the web process reads it back across the process boundary."""
    from app.eval_harness import run_detection_eval

    report = await asyncio.to_thread(run_detection_eval, payload.get("repo") or settings.fixture_repo)
    return report.to_dict()


def _counsel_context(payload: dict):
    """Resolve the same read tree Counsel uses in the web process."""
    from pathlib import Path

    from app.counsel.tools import ToolContext
    from app.sandbox.worktree import repo_root

    worktree = repo_root(payload["repo_full_name"]) / "counsel"
    return ToolContext(
        repo_id=payload["repo_id"],
        repo_full_name=payload["repo_full_name"],
        worktree_path=str(worktree),
        user_email=payload.get("user_email", ""),
        is_admin=bool(payload.get("is_admin")),
    )


async def _handle_counsel_prd(payload: dict) -> dict:
    """The PRD sub-council. Minutes and several frontier calls, which is why
    it is a work item rather than a chat turn."""
    from app.counsel import prd
    from app.routers.ws import emit_event
    from app.sandbox.worktree import ensure_mirror, ensure_read_worktree

    job_id = payload.get("job_id", "")

    def emit(message: str) -> None:
        # Reaches the browser over the same Postgres NOTIFY bus the activity
        # feed uses -- this process has no websocket of its own.
        emit_event({"type": "counsel_job", "job_id": job_id, "status": "running", "message": message})

    mirror = await asyncio.to_thread(ensure_mirror, payload["repo_full_name"])
    await asyncio.to_thread(ensure_read_worktree, mirror)

    result = await asyncio.to_thread(
        prd.draft,
        repo_id=payload["repo_id"],
        repo_name=payload["repo_full_name"],
        worktree_path=str(mirror.parent / "counsel"),
        requirements_text=payload["requirements"],
        emit=emit,
    )
    emit_event({"type": "counsel_job", "job_id": job_id, "status": "done", "message": "PRD ready"})
    return result.to_dict()


async def _handle_counsel_investigate(payload: dict) -> dict:
    """Gather evidence for a hypothesis, then hand it to the Bug Council to
    RULE on -- Counsel never decides whether something is a bug itself."""
    from app.counsel import tools as counsel_tools
    from app.routers.ws import emit_event
    from app.sandbox.worktree import ensure_mirror, ensure_read_worktree

    job_id = payload.get("job_id", "")
    hypothesis = payload["hypothesis"]

    def emit(message: str) -> None:
        emit_event({"type": "counsel_job", "job_id": job_id, "status": "running", "message": message})

    mirror = await asyncio.to_thread(ensure_mirror, payload["repo_full_name"])
    await asyncio.to_thread(ensure_read_worktree, mirror)
    context = _counsel_context(payload)

    emit("Locating the code involved")
    where = await asyncio.to_thread(counsel_tools.explain_subsystem, context, hypothesis)

    emit("Checking what was already tried")
    history = await asyncio.to_thread(counsel_tools.prior_attempts, context, hypothesis)

    categories = payload.get("categories") or []
    dispatched: list[str] = []
    if categories:
        emit(f"Asking the Bug Council to check: {', '.join(categories)}")
        await enqueue("scan_repo", {"repo_id": payload["repo_id"], "categories": categories})
        dispatched = categories

    emit_event({"type": "counsel_job", "job_id": job_id, "status": "done", "message": "Investigation complete"})
    return {
        "hypothesis": hypothesis,
        "where": where[:6000],
        "history": history[:3000],
        "dispatched_categories": dispatched,
        # Stated explicitly because it is the boundary the whole design rests
        # on: evidence was gathered, no verdict was reached.
        "note": (
            "Evidence only. Whether any of this is a real defect is the Bug Council's ruling, "
            "not mine."
        ),
    }


HANDLERS = {
    "scan_repo": _handle_scan_repo,
    "fix_council": _handle_fix_council,
    "resume_human_input": _handle_resume_human_input,
    "apply_approval": _handle_apply_approval,
    "revise_fix": _handle_revise_fix,
    "reindex_repo": _handle_reindex_repo,
    "redeploy": _handle_redeploy,
    "run_eval": _handle_run_eval,
    "counsel_prd": _handle_counsel_prd,
    "counsel_investigate": _handle_counsel_investigate,
}


async def _run_item(item: dict) -> None:
    handler = HANDLERS.get(item["kind"])
    if handler is None:
        with connection() as conn:
            finish(conn, item["id"], error=f"no handler for kind {item['kind']}")
        return

    # The handler runs OUTSIDE the connection block on purpose: a PRD council
    # takes minutes, and holding one of a small pool's connections open for
    # that long would starve every other query in the process.
    try:
        result = await handler(item["payload"])
        with connection() as conn:
            finish(conn, item["id"], result=result if isinstance(result, dict) else None)
        logger.info("completed %s (%s)", item["kind"], item["id"])
    except Exception as error:  # noqa: BLE001 - one bad item must not kill the worker
        logger.exception("work item %s (%s) failed", item["kind"], item["id"])
        with connection() as conn:
            finish(conn, item["id"], error=f"{type(error).__name__}: {error}")


async def _background_loops() -> None:
    """The loops that TRIGGER privileged work belong on this side of the
    split too -- leaving the poller in the web process would mean the web
    process still decided when a council runs."""
    from app.calibration import run_calibration_loop
    from app.poller import poll_for_externally_filed_bugs
    from app.stuck_run_sweeper import sweep_stuck_runs

    await asyncio.gather(
        poll_for_externally_filed_bugs(),
        sweep_stuck_runs(),
        run_calibration_loop(),
        return_exceptions=True,
    )


async def main() -> None:
    logger.info("whipguard worker starting (workspace=%s)", settings.workspace_root)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown.set)

    # Events emitted here reach the dashboard over Postgres NOTIFY
    # (app/routers/ws.py); there is no shared process to broadcast through.
    background = asyncio.create_task(_background_loops())

    last_sweep = 0.0
    running: set[asyncio.Task] = set()
    try:
        while not _shutdown.is_set():
            now = loop.time()
            if now - last_sweep > STALE_SWEEP_EVERY_SECONDS:
                last_sweep = now
                with connection() as conn:
                    requeue_stale(conn)

            with connection() as conn:
                # Only kinds this build knows how to run -- see claim_next.
                item = claim_next(conn, kinds=list(HANDLERS))

            if item is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            logger.info("claimed %s (%s), attempt %s", item["kind"], item["id"], item["attempts"])
            # Concurrent by design: several categories/repos run at once, the
            # same as the create_task model this replaced.
            task = asyncio.create_task(_run_item(item))
            running.add(task)
            task.add_done_callback(running.discard)
    finally:
        background.cancel()
        if running:
            logger.info("waiting for %s in-flight item(s) to finish", len(running))
            await asyncio.gather(*running, return_exceptions=True)
        logger.info("whipguard worker stopped")


if __name__ == "__main__":
    asyncio.run(main())

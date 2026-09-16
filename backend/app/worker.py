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
from app.work_queue import claim_next, connect, finish, requeue_stale

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


async def _handle_apply_approval(payload: dict) -> None:
    from app.db import async_session
    from app.graphs.approval_graph import resolve_approval

    async with async_session() as db:
        await resolve_approval(
            db,
            uuid.UUID(payload["fix_id"]),
            bool(payload["approved"]),
            payload.get("actor", "unknown"),
            payload.get("surface", "dashboard"),
            apply=True,
        )


async def _handle_reindex_repo(payload: dict) -> None:
    from app.graph_index import index_repo_symbols
    from app.retrieval import index_repo_files
    from app.sandbox.worktree import ensure_mirror

    repo_id = uuid.UUID(payload["repo_id"])
    mirror = await asyncio.to_thread(ensure_mirror, payload["repo_full_name"])
    await asyncio.to_thread(index_repo_files, repo_id, str(mirror))
    await asyncio.to_thread(index_repo_symbols, repo_id, str(mirror))


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


HANDLERS = {
    "scan_repo": _handle_scan_repo,
    "fix_council": _handle_fix_council,
    "resume_human_input": _handle_resume_human_input,
    "apply_approval": _handle_apply_approval,
    "reindex_repo": _handle_reindex_repo,
    "redeploy": _handle_redeploy,
    "run_eval": _handle_run_eval,
}


async def _run_item(item: dict) -> None:
    handler = HANDLERS.get(item["kind"])
    conn = connect()
    try:
        if handler is None:
            finish(conn, item["id"], error=f"no handler for kind {item['kind']}")
            return
        try:
            result = await handler(item["payload"])
            finish(conn, item["id"], result=result if isinstance(result, dict) else None)
            logger.info("completed %s (%s)", item["kind"], item["id"])
        except Exception as error:  # noqa: BLE001 - one bad item must not kill the worker
            logger.exception("work item %s (%s) failed", item["kind"], item["id"])
            finish(conn, item["id"], error=f"{type(error).__name__}: {error}")
    finally:
        conn.close()


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
                conn = connect()
                try:
                    requeue_stale(conn)
                finally:
                    conn.close()

            conn = connect()
            try:
                item = claim_next(conn)
            finally:
                conn.close()

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

"""The queue the web process writes to and the privileged worker reads from.

This is the whole interface between the two halves of the system
(plan.md §15). The web side can express exactly one thing -- "somebody asked
for this" -- and has no way to say how, where, or with what privileges it
should happen. The worker decides that.

Claiming uses `FOR UPDATE SKIP LOCKED`, which is the standard Postgres
queue idiom: two workers racing for the same row do not block each other and
do not both get it. Without SKIP LOCKED the second worker waits on the first
worker's lock and then claims the row it already took.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid

import psycopg

from app.retrieval import _sync_dsn

logger = logging.getLogger("whipguard.work_queue")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# Kinds the worker knows how to run. Anything else is rejected at ENQUEUE
# time rather than discovered at claim time, so a typo surfaces in the
# request that caused it instead of in a background loop an hour later.
KINDS = frozenset(
    {"scan_repo", "fix_council", "resume_human_input", "apply_approval", "reindex_repo", "redeploy", "run_eval"}
)


async def enqueue(kind: str, payload: dict) -> uuid.UUID:
    """Ask the worker to do something. Never executes anything itself."""
    if kind not in KINDS:
        raise ValueError(f"unknown work kind: {kind}")

    from sqlalchemy import text

    from app.db import async_session

    item_id = uuid.uuid4()
    async with async_session() as db:
        await db.execute(
            text(
                "INSERT INTO work_items (id, kind, payload, status, attempts, created_at) "
                "VALUES (:id, :kind, CAST(:payload AS jsonb), 'queued', 0, now())"
            ),
            {"id": str(item_id), "kind": kind, "payload": json.dumps(payload)},
        )
        await db.commit()
    logger.info("enqueued %s (%s)", kind, item_id)
    return item_id


def claim_next(conn: psycopg.Connection, kinds: list[str] | None = None) -> dict | None:
    """Atomically take the oldest queued item, or None. Worker-side, sync.

    `kinds` restricts the claim to what this worker can actually run. A
    worker only ever claims kinds it has a handler for, which matters during
    a rolling deploy: an old worker that grabbed a kind introduced by newer
    code would claim it, find no handler, and mark it permanently failed --
    destroying the work instead of leaving it for a worker that can do it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE work_items SET status = 'running', claimed_by = %s, claimed_at = now(),
                                  attempts = attempts + 1
            WHERE id = (
                SELECT id FROM work_items
                WHERE status = 'queued' AND (%s::text[] IS NULL OR kind = ANY(%s::text[]))
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, kind, payload, attempts
            """,
            (WORKER_ID, kinds, kinds),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return None
    return {"id": row[0], "kind": row[1], "payload": row[2] or {}, "attempts": row[3]}


def finish(conn: psycopg.Connection, item_id, *, error: str | None = None, result: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE work_items SET status = %s, finished_at = now(), error = %s, "
            "result = CAST(%s AS jsonb) WHERE id = %s",
            (
                "failed" if error else "done",
                (error or "")[:4000] or None,
                json.dumps(result) if result is not None else None,
                item_id,
            ),
        )
        conn.commit()


def requeue_stale(conn: psycopg.Connection, *, older_than_minutes: int = 30, max_attempts: int = 3) -> int:
    """Return items whose worker died mid-run to the queue.

    A crashed worker leaves its row in 'running' forever, and nothing else
    will ever pick it up -- the queue silently loses work rather than
    retrying it. Items that have already burned their attempts are failed
    rather than cycled, so a genuinely poisonous item cannot loop.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE work_items
            SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
                error = CASE WHEN attempts >= %s THEN 'abandoned after max attempts' ELSE error END
            WHERE status = 'running' AND claimed_at < now() - make_interval(mins => %s)
            """,
            (max_attempts, max_attempts, older_than_minutes),
        )
        count = cur.rowcount or 0
        conn.commit()
    if count:
        logger.warning("requeued/failed %s stale work items", count)
    return count


def connect() -> psycopg.Connection:
    return psycopg.connect(_sync_dsn())

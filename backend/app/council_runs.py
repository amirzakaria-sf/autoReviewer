"""Writes the CouncilRun receipt for every model call any council makes
(plan.md §5.1) -- this is what makes the score rubric on the dashboard real
instead of decorative, and what the admin usage dashboard and eval harness's
cost/latency reporting both read.

Same plain-sync-psycopg pattern as app/retrieval.py, for the same reason:
azure_client._chat() is a sync function called from sync graph nodes (run
via asyncio.to_thread), which never carry an AsyncSession. A write here
must never be allowed to break the model call it's recording -- every
caller wraps this in a bare `except Exception: pass`-equivalent.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

import psycopg

from app import sync_db

from app.config import settings

logger = logging.getLogger("whipguard.council_runs")


def _sync_dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def record_council_run(
    role: str,
    model: str,
    prefix: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    verdict: dict,
    issue_id: uuid.UUID | None = None,
    fix_id: uuid.UUID | None = None,
) -> None:
    prompt_hash = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]
    try:
        with sync_db.connection() as conn:
            conn.execute(
                """
                INSERT INTO council_runs
                    (id, issue_id, fix_id, role, model, prompt_hash,
                     input_tokens, cached_input_tokens, output_tokens, latency_ms, verdict)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(), issue_id, fix_id, role, model, prompt_hash,
                    input_tokens, cached_input_tokens, output_tokens, latency_ms,
                    psycopg.types.json.Json(verdict),
                ),
            )
    except Exception:
        logger.exception("failed to record CouncilRun for role=%s model=%s", role, model)

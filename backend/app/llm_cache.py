"""Exact-input response cache for the deterministic council calls.

plan.md §13.2 named this from the start -- "an exact-match response cache for
genuinely repeated calls" -- and nothing implemented it. The calls it is for
are the evaluative ones: an Arbiter scoring the same diff against the same
evidence, a juror re-reading a finding that has not changed. Those are pure
functions of their input, and a council that re-runs after an unrelated
retry was paying full price and full latency to be told the same thing.

Two rules, both load-bearing:

**Opt in at the call site.** This is not wired into every model call. Patch
GENERATION must never be served from here: if the same prompt produces the
same rejected diff twice, the repetition is itself the signal that the run
is looping, and a cache hit would erase the only evidence of it.

**The key contains everything that could change the answer** -- the exact
prefix, the exact suffix, the deployment name and the calling role. The TTL
is a backstop against a deployment being swapped underneath its own name,
not the primary guard.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import psycopg

from app import sync_db
from app.config import settings
from app.retrieval import _sync_dsn

logger = logging.getLogger("whipguard.llm_cache")


def _enabled() -> bool:
    return bool(getattr(settings, "llm_cache_enabled", True))


def _ttl_seconds() -> int:
    return max(int(getattr(settings, "llm_cache_ttl_seconds", 24 * 3600) or 0), 0)


def cache_key_for(*, role: str, deployment: str, prefix: str, suffix: str) -> str:
    digest = hashlib.sha256()
    for part in (role, deployment, prefix, suffix):
        # Length-prefixed so ("ab", "c") and ("a", "bc") cannot collide.
        digest.update(str(len(part)).encode())
        digest.update(part.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def get(key: str) -> str | None:
    """Return a live cached response, or None. Never raises -- a cache that
    fails must degrade to a real model call, not to an error."""
    if not _enabled():
        return None
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE llm_response_cache SET hit_count = hit_count + 1 "
                "WHERE cache_key = %s AND expires_at > now() RETURNING response_text",
                (key,),
            )
            row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as error:  # noqa: BLE001
        logger.warning("cache read failed: %s", error)
        return None


def put(key: str, response_text: str, *, role: str = "", deployment: str = "") -> None:
    if not _enabled() or not response_text:
        return
    try:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=_ttl_seconds())
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_response_cache
                    (id, cache_key, node, deployment, response_text, hit_count, created_at, expires_at)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, 0, now(), %s)
                ON CONFLICT (cache_key) DO UPDATE
                    SET response_text = EXCLUDED.response_text, expires_at = EXCLUDED.expires_at
                """,
                (key, role[:64], deployment[:64], response_text, expires_at),
            )
            conn.commit()
    except Exception as error:  # noqa: BLE001
        logger.warning("cache write failed: %s", error)


def purge_expired() -> int:
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM llm_response_cache WHERE expires_at <= now()")
            deleted = cur.rowcount
            conn.commit()
        return deleted or 0
    except Exception as error:  # noqa: BLE001
        logger.warning("cache purge failed: %s", error)
        return 0


def stats() -> dict:
    """Surfaced on the admin dashboard -- a saving that leaves no trace
    cannot be told apart from a feature nobody reached."""
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), coalesce(sum(hit_count), 0) FROM llm_response_cache WHERE expires_at > now()"
            )
            entries, hits = cur.fetchone()
        return {"entries": int(entries or 0), "hits": int(hits or 0)}
    except Exception:  # noqa: BLE001
        return {"entries": 0, "hits": 0}

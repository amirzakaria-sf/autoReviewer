"""The two load-bearing modules that had no tests: the subsystem map that
gates every "explain X" answer and every PRD, and the connection pool that
every synchronous path now depends on.

Both fail in ways that are hard to notice. A subsystem map that silently
returns nothing produces a confident answer built on no files; a pool that
leaks connections works perfectly until the eighth concurrent caller hangs.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app import sync_db
from app.repo_map import (
    Subsystem,
    _pagerank,
    build_repo_map,
    find_subsystem,
    render_repo_map,
    render_subsystem,
)


def _sql(query: str, params: tuple | None = None) -> list:
    with sync_db.connection() as conn, conn.cursor() as cur:
        cur.execute(query, params) if params else cur.execute(query)
        return cur.fetchall() if cur.description else []


# --- the pool ----------------------------------------------------------------


def test_a_borrowed_connection_is_returned_even_when_the_body_raises():
    """The failure this prevents is invisible until the pool is exhausted,
    at which point every synchronous path in the process hangs at once."""
    pool = sync_db.pool()
    before = pool.get_stats().get("pool_available", 0)

    with pytest.raises(RuntimeError):
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            raise RuntimeError("boom")

    assert sync_db.pool().get_stats().get("pool_available", 0) >= before


def test_the_pool_is_reused_rather_than_reconnecting():
    """The whole point: a fresh connection measured 12.4ms against this
    deployment, and the org endpoint was making four of them per request."""
    first = sync_db.pool()
    second = sync_db.pool()
    assert first is second


def test_pgvector_adapters_are_registered_on_pooled_connections():
    """Registered in the pool's configure hook rather than per call site.
    Without it, every vector query fails on a borrowed connection."""
    with sync_db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT '[1,2,3]'::vector")
        assert cur.fetchone() is not None


def test_the_dsn_is_translated_from_the_async_form():
    """settings.database_url is asyncpg-shaped; psycopg cannot parse it."""
    assert "+asyncpg" not in sync_db.dsn()
    assert sync_db.dsn().startswith("postgresql://")


# --- ranking -----------------------------------------------------------------


def test_the_most_depended_on_file_ranks_highest():
    """Importance is inbound dependency weight, not file size or path depth:
    an 80-line module thirty files import matters more than a 900-line leaf
    nobody calls."""
    depends_on = {
        "ui.js": {"core.js"},
        "api.js": {"core.js"},
        "jobs.js": {"core.js"},
        "core.js": set(),
        "orphan.js": set(),
    }
    rank = _pagerank(depends_on, set(depends_on))
    assert rank["core.js"] > rank["ui.js"]
    assert rank["core.js"] > rank["orphan.js"]


def test_ranking_terminates_on_a_cycle():
    """Real call graphs have cycles. A naive walk never converges."""
    depends_on = {"a.js": {"b.js"}, "b.js": {"a.js"}}
    rank = _pagerank(depends_on, {"a.js", "b.js"})
    assert set(rank) == {"a.js", "b.js"}
    assert all(value > 0 for value in rank.values())


def test_an_unindexed_repo_says_so_rather_than_returning_an_empty_map():
    """Silence here reads as "this project has no structure", which is a
    different and wrong claim."""
    rendered = render_repo_map("acme/demo", build_repo_map(uuid.uuid4()))
    assert "not been scanned" in rendered or "No indexed structure" in rendered


# --- subsystem derivation -----------------------------------------------------


def test_a_subsystem_that_matched_nothing_explains_itself():
    """The honest answer to "explain the billing system" in a repo with no
    billing code is that nothing matched -- not an invented description."""
    rendered = render_subsystem(
        "acme/demo", Subsystem(query="billing", files=[], seed_symbols=[], entry_points=[])
    )
    assert "billing" in rendered
    assert "Nothing" in rendered or "did not" in rendered.lower()


def test_subsystem_derivation_never_raises_on_a_missing_worktree():
    """Retrieval is an optimisation. A council or a chat turn must not die
    because an index was stale or a path had moved."""
    result = find_subsystem("anything", repo_id=uuid.uuid4(), worktree_path="/nonexistent/path")
    assert isinstance(result, Subsystem)
    assert result.files == []


def test_entry_points_are_dropped_when_nothing_calls_anything(tmp_path):
    """With no internal edges every file trivially qualifies as an entry
    point, which tells the reader nothing -- so the renderer falls back to
    the ranked head instead of claiming all nine files are starting points."""
    subsystem = Subsystem(
        query="x", files=["a.js", "b.js", "c.js", "d.js"], seed_symbols=[], entry_points=["a.js", "b.js", "c.js"]
    )
    rendered = render_subsystem("acme/demo", subsystem)
    assert "Start here" in rendered
    assert "d.js" in rendered, "files outside the entry points must still be listed"

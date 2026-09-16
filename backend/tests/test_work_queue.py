"""The queue that is the entire interface between the web process and the
privileged worker (plan.md §15).

Runs against real Postgres, deliberately: the two guarantees under test --
SKIP LOCKED claiming and the stale-item sweep -- are properties of the
database's locking, not of this Python. A mocked connection would assert
nothing about either.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import async_session
from app.work_queue import KINDS, claim_next, connect, enqueue, finish, requeue_stale


# A kind no worker has a handler for, so `claim_next(kinds=list(HANDLERS))`
# never sees it. Without this the LIVE worker container -- which polls the
# same database -- claims the row a test just enqueued, and the test fails
# intermittently with "nothing queued" depending on who won the race.
TEST_KIND = "__queue_test__"


@pytest.fixture(autouse=True)
async def _clean_queue():
    async def wipe():
        async with async_session() as db:
            await db.execute(text("DELETE FROM work_items WHERE kind = :k"), {"k": TEST_KIND})
            await db.commit()

    await wipe()
    yield
    await wipe()


async def _enqueue_test_item():
    """Inserted directly: enqueue() rightly refuses an unknown kind, and the
    point here is to exercise CLAIMING, not the kind whitelist."""
    async with async_session() as db:
        await db.execute(
            text(
                "INSERT INTO work_items (id, kind, payload, status, attempts, created_at) "
                "VALUES (gen_random_uuid(), :k, '{}'::jsonb, 'queued', 0, now())"
            ),
            {"k": TEST_KIND},
        )
        await db.commit()


async def test_unknown_kind_is_rejected_at_enqueue_time():
    """A typo should fail in the request that caused it, not silently sit in
    the queue until a worker cannot find a handler for it."""
    with pytest.raises(ValueError):
        await enqueue("delete_everything", {})


async def test_every_kind_has_a_handler():
    from app.worker import HANDLERS

    assert set(HANDLERS) == set(KINDS), "a queued kind with no handler would fail every time it ran"


async def test_two_workers_never_claim_the_same_item():
    """The whole point of FOR UPDATE SKIP LOCKED. Without it the second
    connection blocks on the first one's lock and then claims the row it had
    already taken -- the same fix would be applied twice."""
    await _enqueue_test_item()

    first, second = connect(), connect()
    try:
        claimed_a = claim_next(first, kinds=[TEST_KIND])
        claimed_b = claim_next(second, kinds=[TEST_KIND])
        assert claimed_a is not None
        assert claimed_b is None or claimed_b["id"] != claimed_a["id"]
        finish(first, claimed_a["id"])
    finally:
        first.close()
        second.close()


async def test_finish_records_a_result_that_survives_the_process_boundary():
    await _enqueue_test_item()
    conn = connect()
    try:
        item = claim_next(conn, kinds=[TEST_KIND])
        finish(conn, item["id"], result={"passed": 3})
        with conn.cursor() as cur:
            cur.execute("SELECT status, result FROM work_items WHERE id = %s", (item["id"],))
            status, result = cur.fetchone()
    finally:
        conn.close()
    assert status == "done"
    assert result == {"passed": 3}


async def test_a_crashed_workers_item_is_returned_to_the_queue():
    """A worker that dies mid-run leaves its row in 'running' forever, and
    nothing else will ever pick it up -- the queue would silently lose the
    work rather than retry it."""
    await _enqueue_test_item()
    conn = connect()
    try:
        item = claim_next(conn, kinds=[TEST_KIND])
        with conn.cursor() as cur:
            cur.execute("UPDATE work_items SET claimed_at = now() - interval '2 hours' WHERE id = %s", (item["id"],))
            conn.commit()

        assert requeue_stale(conn, older_than_minutes=30, max_attempts=3) >= 1
        reclaimed = claim_next(conn, kinds=[TEST_KIND])
        assert reclaimed is not None and reclaimed["id"] == item["id"]
        finish(conn, item["id"])
    finally:
        conn.close()


async def test_a_poisonous_item_is_failed_rather_than_cycled_forever():
    await _enqueue_test_item()
    conn = connect()
    try:
        item = claim_next(conn, kinds=[TEST_KIND])
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE work_items SET claimed_at = now() - interval '2 hours', attempts = 5 WHERE id = %s",
                (item["id"],),
            )
            conn.commit()
        requeue_stale(conn, older_than_minutes=30, max_attempts=3)
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM work_items WHERE id = %s", (item["id"],))
            assert cur.fetchone()[0] == "failed"
    finally:
        conn.close()

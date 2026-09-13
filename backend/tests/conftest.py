"""pytest-asyncio (asyncio_mode = "auto") gives each async test function its
own event loop. app.db.engine's asyncpg connection pool, however, is a single
module-level object shared across the whole test session — an asyncpg
connection opened under one test's event loop is unusable (and raises
"cannot perform operation: another operation is in progress") once a later
test's *different* event loop tries to reuse it from the pool.

Disposing the pool after every test that touches the DB forces the engine to
open fresh connections bound to whatever event loop is current next time,
which is exactly what test_poller.py / test_runner.py need since they use the
real async Postgres DB (app.db.async_session) directly rather than mocking it.
"""

import pytest_asyncio

from app.db import engine


@pytest_asyncio.fixture(autouse=True)
async def _dispose_db_engine_after_test():
    yield
    await engine.dispose()

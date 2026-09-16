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

import pytest
import pytest_asyncio

from app.config import settings
from app.db import engine


@pytest_asyncio.fixture(autouse=True)
async def _dispose_db_engine_after_test():
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def _disable_llm_response_cache():
    """The exact-input response cache must never be live during tests.

    A test that drives the same council role twice with the same prompt and a
    DIFFERENT mocked answer is asking for a cache hit and will get one -- and
    because the cache is a real Postgres table, a row written by one test is
    still there for the next one and for the next RUN. That is shared state
    no test intended to share, and it fails in a way that looks like the
    code under test returning a stale verdict.
    """
    original = settings.llm_cache_enabled
    settings.llm_cache_enabled = False
    yield
    settings.llm_cache_enabled = original

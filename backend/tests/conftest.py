"""Test-session setup.

The first thing this file does is redirect the database, and it has to happen
before anything imports `app.config` -- pydantic reads the environment when
the Settings object is constructed, and `app.db` builds its engine from that
at import time.

Why it matters: these tests use the REAL async Postgres session rather than
mocking it, and until this existed they used the real PRODUCTION database.
Test fixtures were being written into live tables, and rows left behind by a
run that failed part-way through showed up on the dashboard as genuine issues
awaiting a decision. This is the second time shared state with production has
caused a problem here (the first was the LLM response cache, guarded below);
a separate database ends the class of bug rather than the instance.
"""

import os

_TEST_DATABASE = "whipguard_test"
_LIVE_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://whipguard:whipguard@localhost:5433/whipguard"
)
# Same server, same credentials, different database.
os.environ["DATABASE_URL"] = _LIVE_DATABASE_URL.rsplit("/", 1)[0] + "/" + _TEST_DATABASE

import psycopg
import pytest
import pytest_asyncio

from app.config import settings
from app.db import Base, engine


def _ensure_test_database() -> None:
    """Drop and recreate the test database, then enable pgvector on it.

    CREATE DATABASE cannot run inside a transaction, hence autocommit, and the
    connection is made to `postgres` rather than to the database being
    dropped.
    """
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    admin_dsn = dsn.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DATABASE,))
        if cur.fetchone() is not None:
            # Rebuilt every session rather than reused. `create_all` adds
            # missing tables but never alters an existing one, so a kept
            # database drifts from the models exactly as production did --
            # and a test suite that cannot see a schema change is worse than
            # no test suite. It costs well under a second.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (_TEST_DATABASE,),
            )
            cur.execute(f'DROP DATABASE "{_TEST_DATABASE}"')
        # TEMPLATE template0, not the default template1: this server's
        # template1 was created under a different glibc, and CREATE DATABASE
        # from it fails outright with "collation version mismatch".
        cur.execute(f'CREATE DATABASE "{_TEST_DATABASE}" TEMPLATE template0')

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # pgvector is an extension per DATABASE, not per server -- a fresh
        # test database does not inherit it from the live one.
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    import asyncio

    from app import models  # noqa: F401  (registers tables on Base.metadata)
    from app.schema_sync import sync_additive_columns, sync_enum_labels

    _ensure_test_database()

    async def build() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await sync_enum_labels(conn)
            await sync_additive_columns(conn)
        await engine.dispose()

    asyncio.run(build())
    asyncio.run(_seed_admin())
    yield


async def _seed_admin() -> None:
    """The same bootstrap admin a real deployment creates on its first boot.

    Several tests act as an already-existing admin (approving an access
    request records `decided_by`, which is a foreign key into users). Against
    the production database that account happened to be there; a fresh test
    database has nobody at all.
    """
    from app.security import hash_password
    from app.db import async_session
    from app.enums import UserRole, UserStatus
    from app.models import User
    from sqlalchemy import select

    async with async_session() as db:
        existing = (await db.execute(select(User.id).limit(1))).scalars().first()
        if existing is None:
            db.add(
                User(
                    email=(settings.notify_email or "admin@example.com").strip().lower(),
                    password_hash=hash_password(settings.admin_password or "test-password"),
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                )
            )
            await db.commit()
    await engine.dispose()



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

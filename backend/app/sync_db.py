"""A pooled synchronous connection, shared by everything that is not async.

The async SQLAlchemy engine covers the request path. Everything else here --
retrieval, the call graph, memory traces, the response cache, the work queue,
org membership -- is synchronous by design, because it runs inside council
graph nodes on worker threads where an async session's connections (bound to
the main event loop) are unusable.

Those callers were each opening a fresh connection per query. Measured
against this deployment that costs **12.4ms every time**, and the org
endpoint alone made four of them, so more than fifty of its fifty-seven
milliseconds were TCP and auth rather than work. A pool removes that
entirely.

Created lazily and per process: the web container and the worker each get
their own, which is what you want -- they have very different concurrency
profiles and neither should be able to exhaust the other's connections.
"""

from __future__ import annotations

import atexit
import logging
import threading

from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger("whipguard.sync_db")

# Small on purpose. Postgres connections are not free on the server side
# either, and there are two processes sharing one database. If this is ever
# genuinely the bottleneck, that is a signal to move work off the request
# path rather than to raise the ceiling.
_MIN_SIZE = 1
_MAX_SIZE = 8

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def dsn() -> str:
    """settings.database_url is the asyncpg form; psycopg wants plain
    postgresql://."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _configure(conn) -> None:
    """Run once per pooled connection, not per query.

    pgvector's adapters are registered on the CONNECTION, so with a pool this
    belongs here -- doing it at each call site would either re-register on
    every borrow or, worse, be applied to the pool's context manager instead
    of the connection it yields.
    """
    try:
        from pgvector.psycopg import register_vector

        register_vector(conn)
    except Exception as error:  # noqa: BLE001 - only vector queries need this
        logger.warning("could not register pgvector adapters: %s", error)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ConnectionPool(
                    dsn(),
                    min_size=_MIN_SIZE,
                    max_size=_MAX_SIZE,
                    # Fail fast rather than hanging a council node forever on
                    # an exhausted pool -- a slow answer is recoverable, a
                    # wedged worker is not.
                    timeout=10.0,
                    # A connection idle this long is more likely to have been
                    # killed by the server than to be worth reusing.
                    max_idle=300.0,
                    configure=_configure,
                    open=True,
                )
                atexit.register(_close)
                logger.info("sync connection pool opened (max %s)", _MAX_SIZE)
    return _pool


def _close() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001 - shutdown, nothing left to recover to
            pass
        _pool = None


def connection():
    """Borrow a connection. Use exactly like `psycopg.connect(...)`:

        with sync_db.connection() as conn, conn.cursor() as cur:
            ...

    The pool commits on a clean exit and rolls back on an exception, so an
    explicit `conn.commit()` is harmless but no longer required.
    """
    return pool().connection()

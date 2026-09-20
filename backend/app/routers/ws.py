"""The activity feed: one websocket per browser, fed from both processes.

Every event is addressed to exactly one repository, and a connection only ever
receives events for repositories its viewer can already see. That is not
belt-and-braces on top of the HTTP scoping -- it is the same boundary, and for
most of this project's life the feed was the widest hole in it: the socket had
no authentication at all (the session middleware is `@app.middleware("http")`,
which never runs for a websocket, and `/ws/activity` is not under `/api/`
either), and `broadcast` wrote every event to every open connection. Anyone who
could reach the host could watch every organisation's councils run, with the
file paths and assertion text those messages carry.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from contextlib import contextmanager

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger("whipguard.ws")

# Which repository the work running on this thread/task belongs to.
#
# There are ~47 `emit_event` call sites and threading a repo id through all of
# them is how one gets missed -- and a missed one is either a leak or a dead
# feed. An ambient variable set once per run at each entry point is six places
# instead of forty-seven. `asyncio.to_thread` copies the context, so a graph
# node running in a worker thread still sees it.
_event_repo_id: contextvars.ContextVar[str] = contextvars.ContextVar("whipguard_event_repo_id", default="")


def set_event_repo(repo_id) -> None:
    """Stamp every event emitted from here on in this context."""
    _event_repo_id.set(str(repo_id) if repo_id else "")


@contextmanager
def activity_scope(repo_id):
    """Scoped form, for a block that must not leak its stamp to whatever runs
    next on the same thread."""
    token = _event_repo_id.set(str(repo_id) if repo_id else "")
    try:
        yield
    finally:
        _event_repo_id.reset(token)


class ActivityBroadcaster:
    """Connections, each carrying the set of repositories its viewer may see."""

    def __init__(self) -> None:
        self.connections: list[tuple[WebSocket, frozenset[str]]] = []

    async def connect(self, ws: WebSocket, repo_ids) -> None:
        await ws.accept()
        self.connections.append((ws, frozenset(str(repo_id) for repo_id in repo_ids)))

    def disconnect(self, ws: WebSocket) -> None:
        self.connections = [entry for entry in self.connections if entry[0] is not ws]

    async def broadcast(self, event: dict) -> None:
        """Deliver to the connections allowed to see this event, and no others.

        An event with no `repo_id` reaches NOBODY. That is deliberate: the only
        two failure modes available are "an unattributed event is invisible in
        the feed" and "an unattributed event goes to everyone". The first is a
        bug someone reports; the second is the leak this exists to close.
        """
        repo_id = str(event.get("repo_id") or "")
        if not repo_id:
            logger.warning(
                "dropping an unattributed activity event (type=%s kind=%s node=%s) -- "
                "its emitter needs an activity_scope",
                event.get("type"), event.get("kind"), event.get("node"),
            )
            return

        dead = []
        for ws, visible in self.connections:
            if repo_id not in visible:
                continue
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


broadcaster = ActivityBroadcaster()

# Graph nodes (Bug/Fix Council, approval) do blocking sync I/O (subprocess,
# sync httpx) and run inside asyncio.to_thread worker threads, not on the main
# loop -- calling broadcaster.broadcast() (a coroutine) directly from a worker
# thread has no loop to run on. run_coroutine_threadsafe against the stored
# main-loop reference is what actually gets an event from a worker thread onto
# a browser's open websocket in real time, rather than the whole batch of
# events only flushing after the blocking call finally returns.
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


# Postgres NOTIFY channel. Council work runs in the privileged WORKER
# process (app/worker.py) after the security split, so an in-process
# broadcaster alone would mean the dashboard's activity feed silently went
# dead for every event that matters -- the emitter and the websocket are no
# longer in the same process. Postgres is already a shared dependency of
# both, so it carries the events; no broker, no new service.
EVENT_CHANNEL = "whipguard_events"
# NOTIFY payloads are capped at 8000 bytes by Postgres. An oversized event is
# truncated rather than dropped, since losing the event entirely is worse
# than losing the tail of one long message.
_MAX_PAYLOAD_BYTES = 7000


def emit_event(event: dict) -> None:
    """Thread-safe fire-and-forget, from any process or thread. Never raises
    -- a dropped activity-feed event is not worth failing a council run
    over."""
    import os as _os

    event = {"at": time.time(), **event}
    # Addressed to a repository, from the ambient scope unless the caller was
    # explicit. Unaddressed events are dropped at the broadcaster, so this is
    # what keeps the feed working rather than what keeps it safe.
    if not event.get("repo_id"):
        ambient = _event_repo_id.get()
        if ambient:
            event["repo_id"] = ambient
    # Stamped so the LISTEN relay can tell this process's own echo apart from
    # a genuine event emitted by the worker. Without it, every web-originated
    # event would be broadcast twice: once locally, once when it came back.
    event.setdefault("_pid", _os.getpid())

    # Local listeners first: in the web process this is the fast path, and it
    # still works if Postgres NOTIFY is unavailable for any reason.
    if _main_loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(broadcaster.broadcast(event), _main_loop)
        except Exception:
            pass

    try:
        from app import sync_db

        payload = json.dumps(event)
        if len(payload.encode()) > _MAX_PAYLOAD_BYTES:
            trimmed = dict(event)
            trimmed["message"] = str(trimmed.get("message", ""))[:800] + "…"
            payload = json.dumps(trimmed)[:_MAX_PAYLOAD_BYTES]
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_notify(%s, %s)", (EVENT_CHANNEL, payload))
            conn.commit()
    except Exception:
        pass


async def listen_for_events() -> None:
    """Web-process side: relay NOTIFYs from the worker onto open websockets.

    Skips anything this process emitted itself -- those already went through
    the local fast path above, and rebroadcasting them would show every
    web-originated event twice.
    """
    import os

    import psycopg

    from app.retrieval import _sync_dsn

    origin = os.getpid()
    while True:
        try:
            # NOT pooled, deliberately: a LISTEN connection is held open for
            # the life of the process, and parking one of a small pool's
            # connections there forever would starve everything else.
            conn = await asyncio.to_thread(psycopg.connect, _sync_dsn())
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {EVENT_CHANNEL}")
            while True:
                got = await asyncio.to_thread(lambda: list(conn.notifies(timeout=5)))
                for notify in got:
                    try:
                        event = json.loads(notify.payload)
                    except Exception:
                        continue
                    if event.get("_pid") == origin:
                        continue
                    await broadcaster.broadcast(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A dropped LISTEN connection must reconnect rather than end the
            # feed for the life of the process.
            await asyncio.sleep(3)


@router.websocket("/ws/activity")
async def activity_stream(ws: WebSocket):
    """Authenticated here, not by middleware.

    `@app.middleware("http")` never runs for a websocket scope, so this handler
    is the only place the session can be checked. The browser sends the
    `access_token` cookie with the handshake because the socket is same-origin;
    there is no header to read on a browser websocket, which is why the cookie
    is the credential rather than a bearer token.
    """
    import uuid as uuid_module

    from app import orgs
    from app.security import decode_access_token

    token = ws.cookies.get("access_token")
    decoded = decode_access_token(token) if token else None
    if not decoded:
        # 4401 rather than 1008: the frontend retries after a silent token
        # refresh for this code, and gives up for a generic policy violation.
        await ws.close(code=4401)
        return

    try:
        user_id = uuid_module.UUID(decoded["sub"])
    except (KeyError, ValueError, TypeError):
        await ws.close(code=4401)
        return

    # Resolved once, at connect. A membership change mid-session takes effect
    # on the next connection, which is the same latency the HTTP surface has.
    repo_ids = await asyncio.to_thread(orgs.repo_ids_for_user, user_id)

    await broadcaster.connect(ws, repo_ids)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)
    except Exception:  # noqa: BLE001 - a broken socket must not leak a connection entry
        broadcaster.disconnect(ws)

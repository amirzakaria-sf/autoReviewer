from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ActivityBroadcaster:
    def __init__(self) -> None:
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws in self.connections:
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
    await broadcaster.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)

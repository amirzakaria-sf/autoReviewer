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


def emit_event(event: dict) -> None:
    """Thread-safe fire-and-forget: safe to call from a worker thread OR the
    main loop. Never raises -- a dropped activity-feed event is not worth
    failing a council run over."""
    event = {"at": time.time(), **event}
    if _main_loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(broadcaster.broadcast(event), _main_loop)
    except Exception:
        pass


@router.websocket("/ws/activity")
async def activity_stream(ws: WebSocket):
    await broadcaster.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)

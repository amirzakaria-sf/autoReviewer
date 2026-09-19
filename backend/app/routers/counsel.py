"""Counsel's HTTP surface: one streaming endpoint, one conversation store.

Server-sent events rather than a WebSocket. The activity feed's socket is a
broadcast channel — everyone connected sees every event — and a private
conversation must not go there. SSE is also a better fit for the shape of
this traffic: one request in, a stream of events out, no client-to-server
messages after the first, and it reconnects on its own.

The agent loop is synchronous and does blocking I/O (git, Postgres, the
model). It runs in a worker thread and its events are handed to the event
loop through a queue, so one person's long question never freezes the
process for everyone else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.counsel.tools import ToolContext
from app.db import get_db
from app.deps import current_user
from app.models import Repo, User

router = APIRouter(prefix="/api/counsel")
logger = logging.getLogger("whipguard.counsel.router")

# How much prior conversation is replayed to the model. Tool results are
# never replayed (see agent.py), so this is cheap -- but an unbounded history
# still grows the prompt without bound across a long session.
HISTORY_TURNS = 8


async def _resolve_context(db: AsyncSession, user: User, repo_id: uuid.UUID | None) -> ToolContext:
    from sqlalchemy import select

    repo = await db.get(Repo, repo_id) if repo_id else (await db.execute(select(Repo))).scalars().first()
    if repo is None:
        raise HTTPException(400, "No repository is connected yet — connect one before asking about code.")

    from app.sandbox.worktree import repo_root

    # The persistent read tree, not the bare mirror and not a fix worktree.
    # The mirror has no working files at all (BM25 finds nothing, reads fail);
    # a fix worktree is one PROPOSED change, not "the code".
    worktree = repo_root(repo.github_full_name) / "counsel"
    if not worktree.is_dir():
        raise HTTPException(
            409,
            "This repo has not been indexed yet, so I have nothing to read. "
            "Run a scan from the dashboard and try again.",
        )
    return ToolContext(
        repo_id=str(repo.id),
        repo_full_name=repo.github_full_name,
        worktree_path=str(worktree),
        user_email=user.email,
        is_admin=user.role.value == "admin",
    )


async def _load_history(db: AsyncSession, conversation_id: uuid.UUID) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT role, content FROM counsel_messages WHERE conversation_id = :cid "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"cid": str(conversation_id), "limit": HISTORY_TURNS},
        )
    ).all()
    return [{"role": role, "content": content} for role, content in reversed(rows)]


async def _save_message(db: AsyncSession, conversation_id: uuid.UUID, role: str, content: str) -> None:
    await db.execute(
        text(
            "INSERT INTO counsel_messages (id, conversation_id, role, content, created_at) "
            "VALUES (gen_random_uuid(), :cid, :role, :content, now())"
        ),
        {"cid": str(conversation_id), "role": role, "content": content},
    )
    await db.commit()


@router.post("/conversations")
async def create_conversation(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    conversation_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO counsel_conversations (id, user_id, title, created_at) "
            "VALUES (:id, :uid, :title, now())"
        ),
        {"id": str(conversation_id), "uid": str(user.id), "title": ""},
    )
    await db.commit()
    return {"id": str(conversation_id)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    owner = (
        await db.execute(
            text("SELECT user_id FROM counsel_conversations WHERE id = :cid"), {"cid": str(conversation_id)}
        )
    ).scalar()
    if owner is None:
        raise HTTPException(404, "conversation not found")
    if str(owner) != str(user.id):
        raise HTTPException(403, "not your conversation")

    rows = (
        await db.execute(
            text(
                "SELECT role, content, created_at FROM counsel_messages "
                "WHERE conversation_id = :cid ORDER BY created_at"
            ),
            {"cid": str(conversation_id)},
        )
    ).all()
    return {
        "id": str(conversation_id),
        "messages": [
            {"role": role, "content": content, "at": at.isoformat() if at else None} for role, content, at in rows
        ],
    }


@router.post("/ask")
async def ask(body: dict, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(400, "Ask a question first.")

    conversation_id = uuid.UUID(body["conversation_id"]) if body.get("conversation_id") else uuid.uuid4()
    repo_id = uuid.UUID(body["repo_id"]) if body.get("repo_id") else None

    context = await _resolve_context(db, user, repo_id)
    history = await _load_history(db, conversation_id) if body.get("conversation_id") else []

    if not body.get("conversation_id"):
        await db.execute(
            text(
                "INSERT INTO counsel_conversations (id, user_id, title, created_at) "
                "VALUES (:id, :uid, :title, now()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(conversation_id), "uid": str(user.id), "title": question[:120]},
        )
        await db.commit()

    await _save_message(db, conversation_id, "user", question)

    from app.counsel import agent

    async def event_stream():
        loop = asyncio.get_running_loop()
        events: queue.Queue = queue.Queue()
        _SENTINEL = object()

        def produce():
            """The agent loop is blocking; keep it off the event loop."""
            try:
                for event in agent.run(context, question, history):
                    events.put(event)
            except Exception as error:  # noqa: BLE001
                logger.exception("counsel stream failed")
                events.put({"type": "error", "text": f"Something failed: {type(error).__name__}"})
            finally:
                events.put(_SENTINEL)

        task = loop.run_in_executor(None, produce)

        yield f"data: {json.dumps({'type': 'open', 'conversation_id': str(conversation_id)})}\n\n"

        answer_parts: list[str] = []
        try:
            while True:
                event = await loop.run_in_executor(None, events.get)
                if event is _SENTINEL:
                    break
                if event.get("type") == "token":
                    answer_parts.append(event["text"])
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            await task

        # Persisted after the stream completes so a reconnect replays the
        # finished answer rather than a half-written one.
        answer = "".join(answer_parts).strip()
        if answer:
            try:
                from app.db import async_session

                async with async_session() as scoped:
                    await scoped.execute(
                        text(
                            "INSERT INTO counsel_messages (id, conversation_id, role, content, created_at) "
                            "VALUES (gen_random_uuid(), :cid, 'assistant', :content, now())"
                        ),
                        {"cid": str(conversation_id), "content": answer},
                    )
                    await scoped.commit()
            except Exception:  # noqa: BLE001 - losing the transcript must not break the reply
                logger.exception("could not persist counsel answer")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which holds every
            # event until the whole stream closes -- the exact "blank, then a
            # block appears" behaviour this endpoint exists to avoid.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Follow a long job Counsel started.

    PRDs and investigations take minutes, so they are work items rather than
    chat turns. The sidebar polls this to render a live card instead of
    leaving the reader wondering whether anything is happening.
    """
    row = (
        await db.execute(
            text(
                "SELECT kind, status, result, error FROM work_items "
                "WHERE payload->>'job_id' = :job_id ORDER BY created_at DESC LIMIT 1"
            ),
            {"job_id": job_id},
        )
    ).first()
    if row is None:
        # Not an error: the row appears a moment after the tool returns its id.
        return {"job_id": job_id, "status": "queued", "result": None, "error": None}

    kind, status, result, error = row
    return {"job_id": job_id, "kind": kind, "status": status, "result": result, "error": error}

"""The general HIL primitive's API surface (plan.md §10.5) -- one function,
one endpoint set, whether the question came from the Arbiter's own
needs_clarification path or (once wired) the Fix Council's ask_human tool.
Rendered identically on every surface the same way approve/reject is: a
select control on the dashboard here; Slack/email rendering is a further
extension of the same rows, not a second system.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import HumanInputRequest

router = APIRouter(prefix="/api/human-input")


def _request_dict(r: HumanInputRequest) -> dict:
    return {
        "id": str(r.id),
        "issue_id": str(r.issue_id) if r.issue_id else None,
        "fix_id": str(r.fix_id) if r.fix_id else None,
        "node_name": r.node_name,
        "kind": r.kind,
        "question": r.question,
        "options": r.options,
        "allow_other": r.allow_other,
        "status": r.status,
        "answer": r.answer,
        "thread": r.thread,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "answered_at": r.answered_at.isoformat() if r.answered_at else None,
    }


@router.get("")
async def list_requests(status: str | None = "pending", db: AsyncSession = Depends(get_db)):
    stmt = select(HumanInputRequest).order_by(HumanInputRequest.created_at.desc())
    if status:
        stmt = stmt.where(HumanInputRequest.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [_request_dict(r) for r in rows]


@router.post("/{request_id}/answer")
async def answer_request(request_id: uuid.UUID, body: dict, db: AsyncSession = Depends(get_db)):
    request = await db.get(HumanInputRequest, request_id)
    if not request:
        raise HTTPException(404, "request not found")
    if request.status != "pending":
        return {"ok": False, "already_handled": True, "status": request.status}

    answer_text = body.get("answer", "")
    actor = body.get("actor", "dashboard-user")

    request.status = "answered"
    request.answer = {"text": answer_text}
    request.answered_by = actor
    request.answered_via = "dashboard"
    request.answered_at = datetime.now(timezone.utc)
    request.thread = [*request.thread, {"from": "human", "text": answer_text, "at": datetime.now(timezone.utc).isoformat()}]
    await db.commit()

    # Resume, node-specific: only the Arbiter's needs_clarification path
    # exists today (plan.md §16's phasing -- the Fix Council's ask_human tool
    # and the ApprovalGraph's bidirectional thread are the same primitive,
    # not yet wired to this endpoint).
    if request.node_name == "bug_council.arbiter":
        from app.db import async_session
        from app.graphs.bug_council import resume_with_clarification_answer

        async def _resume():
            async with async_session() as scoped_db:
                fresh_request = await scoped_db.get(HumanInputRequest, request_id)
                await resume_with_clarification_answer(scoped_db, fresh_request, answer_text)

        asyncio.create_task(_resume())

    return {"ok": True, "status": "answered"}

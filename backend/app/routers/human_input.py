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
from sqlalchemy import or_ as sa_or
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, visible_repo_ids
from app.models import Fix, HumanInputRequest, Issue, User

router = APIRouter(prefix="/api/human-input")


def _within(visible: list[uuid.UUID]):
    """The tenancy predicate for a clarification request.

    These rows carry no `repo_id` of their own -- they reach one through their
    issue, or through their fix's issue -- which is exactly why this endpoint
    pair was never scoped: there was no column to scope by and the join is not
    obvious. It leaked in both directions. Reading returned every
    organisation's open questions, each carrying the question text and the
    `already_considered` context a patch worker wrote about their code. And
    answering was worse than a read: `POST /answer` enqueues a `fix_council`
    work item, so one organisation could steer another's Fix Council by
    answering a question it was never asked.

    A row attached to neither an issue nor a fix belongs to nobody and is
    returned to nobody. Fail closed.
    """
    owned_issue = select(Issue.id).where(Issue.repo_id.in_(visible))
    return sa_or(
        HumanInputRequest.issue_id.in_(owned_issue),
        HumanInputRequest.fix_id.in_(
            select(Fix.id).where(Fix.issue_id.in_(owned_issue))
        ),
    )


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
async def list_requests(
    status: str | None = "pending",
    db: AsyncSession = Depends(get_db),
    visible: list[uuid.UUID] = Depends(visible_repo_ids),
):
    if not visible:
        return []
    stmt = (
        select(HumanInputRequest)
        .where(_within(visible))
        .order_by(HumanInputRequest.created_at.desc())
    )
    if status:
        stmt = stmt.where(HumanInputRequest.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [_request_dict(r) for r in rows]


@router.post("/{request_id}/answer")
async def answer_request(
    request_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    visible: list[uuid.UUID] = Depends(visible_repo_ids),
):
    # 404 whether it does not exist, belongs to another organisation, or the
    # caller belongs to no organisation at all.
    if not visible:
        raise HTTPException(404, "request not found")
    request = (
        await db.execute(
            select(HumanInputRequest).where(HumanInputRequest.id == request_id, _within(visible))
        )
    ).scalars().first()
    if not request:
        raise HTTPException(404, "request not found")
    if request.status != "pending":
        return {"ok": False, "already_handled": True, "status": request.status}

    answer_text = body.get("answer", "")
    # The session decides who answered, not the request body. An answer is
    # what resumes a council run, so "who said this" has to be something the
    # caller cannot choose.
    actor = user.email

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
        from app.work_queue import enqueue

        await enqueue("resume_human_input", {"request_id": str(request_id), "answer": answer_text})
    elif request.node_name == "fix_council.patch_generation":
        from app.work_queue import enqueue

        if request.issue_id:
            await enqueue(
                "fix_council",
                {"issue_id": str(request.issue_id), "feedback": answer_text},
            )

    return {"ok": True, "status": "answered"}

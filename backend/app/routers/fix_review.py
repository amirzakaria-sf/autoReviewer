"""The review conversation: read the thread, ask for a different approach.

Approve and reject stay in routers/api.py alongside the other fix actions --
they are decisions about a fix. This router owns the part that is a
conversation.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, visible_repo_ids
from app.enums import FIX_STATUS_RENDER
from app.fix_review import MAX_REVISIONS, RevisionRejected, request_revision
from app.models import Fix, FixReview, Issue, User

router = APIRouter(prefix="/api")
logger = logging.getLogger("whipguard.fix_review.router")


class RevisionIn(BaseModel):
    # Long enough for a real instruction, short enough that nobody pastes a
    # design document into a field that is replayed into every later prompt.
    instruction: str = Field(min_length=1, max_length=4000)


def _fix_card(fix: Fix | None) -> dict | None:
    if fix is None:
        return None
    render = FIX_STATUS_RENDER[fix.status]
    return {
        "id": str(fix.id),
        "attempt": fix.attempt,
        "status": fix.status.value,
        "badge": render["dashboard_badge"],
        "color": render["dashboard_color"],
        "score": fix.resolution_score,
        "verdict": (fix.resolution_rubric or {}).get("verdict"),
        "rubric": (fix.resolution_rubric or {}).get("factors", []),
        "diff": fix.diff or "",
        "branch_name": fix.branch_name,
        "pr_number": fix.pr_number,
        "preview_url": fix.preview_url,
        "decision_note": fix.decision_note,
    }


@router.get("/issues/{issue_id}/review")
async def get_review(
    issue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
) -> dict:
    """Everything the review UI needs in one call: the conversation, the
    attempt on the table, and every superseded attempt behind it."""
    issue = await db.get(Issue, issue_id)
    # Scoped to the caller's organization: the thread carries the proposed
    # diff, which is source code.
    if issue is None or issue.repo_id not in repo_ids:
        raise HTTPException(404, "issue not found")

    review = (
        await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
    ).scalars().first()

    fixes = (
        await db.execute(select(Fix).where(Fix.issue_id == issue_id).order_by(Fix.created_at))
    ).scalars().all()

    current = None
    if review is not None and review.current_fix_id is not None:
        current = next((f for f in fixes if f.id == review.current_fix_id), None)
    if current is None and fixes:
        current = fixes[-1]

    return {
        "issue_id": str(issue_id),
        "issue_title": issue.title,
        "status": review.status if review else None,
        "attempts": review.attempts if review else 0,
        "revisions_left": max(0, MAX_REVISIONS - (review.attempts if review else 0)),
        "transcript": (review.transcript if review else []) or [],
        "current": _fix_card(current),
        "history": [_fix_card(f) for f in fixes if current is None or f.id != current.id],
    }


@router.post("/fixes/{fix_id}/revise")
async def revise_fix(
    fix_id: uuid.UUID,
    body: RevisionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    repo_ids: list[uuid.UUID] = Depends(visible_repo_ids),
) -> dict:
    """"Not like that -- do it this way instead."

    Deliberately not `approve=false`: a rejection is terminal and a revision
    is not, and a single boolean cannot carry the instruction that makes the
    next attempt different.
    """
    fix = await db.get(Fix, fix_id)
    issue = await db.get(Issue, fix.issue_id) if fix else None
    if fix is None or issue is None or issue.repo_id not in repo_ids:
        raise HTTPException(404, "fix not found")

    try:
        return await request_revision(
            db, fix_id, instruction=body.instruction, actor=user.email, surface="dashboard"
        )
    except RevisionRejected as error:
        # 409, not 400: the request was well-formed, the fix had simply moved
        # on. The frontend refreshes on this rather than showing a form error.
        raise HTTPException(409, str(error)) from error

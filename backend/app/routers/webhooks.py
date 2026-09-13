from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.graphs.approval_graph import resolve_approval
from app.integrations.slack_client import verify_signature
from app.models import Fix, Issue

router = APIRouter(prefix="/api")


@router.post("/webhooks/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()

    if "comment" in payload and payload.get("action") == "created":
        body = payload["comment"]["body"].strip().lower()
        if body == "/reject":
            issue_number = payload["issue"]["number"]
            issue = (
                await db.execute(select(Issue).where(Issue.github_issue_number == issue_number))
            ).scalars().first()
            fix = None
            if issue:
                fix = (
                    await db.execute(
                        select(Fix).where(Fix.issue_id == issue.id).order_by(Fix.created_at.desc())
                    )
                ).scalars().first()
            if fix:
                actor = payload["comment"]["user"]["login"]
                await resolve_approval(db, fix.id, approved=False, actor=actor, surface="github-comment")

    return {"ok": True}


@router.post("/slack/interactions")
async def slack_interactions(request: Request, db: AsyncSession = Depends(get_db)):
    body_bytes = await request.body()
    body_str = body_bytes.decode()

    if not verify_signature(dict(request.headers), body_str, settings.slack_signing_secret):
        raise HTTPException(401, "invalid Slack signature")

    form = dict(pair.split("=", 1) for pair in body_str.split("&") if "=" in pair)
    from urllib.parse import unquote_plus

    payload = json.loads(unquote_plus(form["payload"]))

    action = payload["actions"][0]
    fix_id = uuid.UUID(action["value"].split(":")[1])
    approved = action["action_id"] == "approve_fix"
    actor = payload["user"]["username"]

    result = await resolve_approval(db, fix_id, approved=approved, actor=actor, surface="slack")
    return result

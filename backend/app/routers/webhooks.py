from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.graphs.approval_graph import resolve_approval
from app.integrations.slack_client import verify_signature
from app.models import Fix, Issue, Repo

router = APIRouter(prefix="/api")

# GitHub redelivers webhooks (retries, and a slow ack can duplicate a delivery).
# Deduped on X-GitHub-Delivery so a retry can never fire a second scan for the
# same push (plan.md §14.9). In-memory only — acceptable for a single-instance
# hackathon deployment; a restart just means a redelivered event within that
# short window could re-scan once, not double-raise anything (raising itself is
# idempotent on GitHub issue content, only wasteful, not incorrect).
_SEEN_DELIVERIES: set[str] = set()
_SEEN_DELIVERIES_MAX = 500


@router.post("/webhooks/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if delivery_id:
        if delivery_id in _SEEN_DELIVERIES:
            return {"ok": True, "deduped": True}
        _SEEN_DELIVERIES.add(delivery_id)
        if len(_SEEN_DELIVERIES) > _SEEN_DELIVERIES_MAX:
            _SEEN_DELIVERIES.pop()

    payload = await request.json()

    if "ref" in payload and "commits" in payload and "comment" not in payload:
        repo_full_name = payload.get("repository", {}).get("full_name")
        if repo_full_name:
            repo = (
                await db.execute(select(Repo).where(Repo.github_full_name == repo_full_name))
            ).scalars().first()
            if repo:
                from app.graphs.bug_council import run_and_persist
                from app.db import async_session

                async def _scan():
                    async with async_session() as scoped_db:
                        fresh_repo = await scoped_db.get(Repo, repo.id)
                        await run_and_persist(scoped_db, fresh_repo)

                asyncio.create_task(_scan())

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

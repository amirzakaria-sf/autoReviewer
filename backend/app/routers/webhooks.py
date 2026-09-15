from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calibration import record_calibration_event
from app.config import settings
from app.db import get_db
from app.enums import FixStatus, IssueStatus
from app.graphs.approval_graph import resolve_approval
from app.integrations import github_client
from app.integrations.slack_client import verify_signature
from app.models import Fix, Issue, Repo

router = APIRouter(prefix="/api")
logger = logging.getLogger("whipguard.webhooks")

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

    event = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    if event == "push":
        await _handle_push(db, payload)
    elif event == "pull_request":
        await _handle_pull_request(db, payload)
    elif event == "issues":
        await _handle_issues(db, payload)
    elif event == "issue_comment":
        await _handle_issue_comment(db, payload)

    return {"ok": True}


async def _handle_push(db: AsyncSession, payload: dict) -> None:
    repo_full_name = payload.get("repository", {}).get("full_name")
    if not repo_full_name:
        return
    repo = (await db.execute(select(Repo).where(Repo.github_full_name == repo_full_name))).scalars().first()
    if not repo:
        return

    from app.categories import enabled_categories_for
    from app.db import async_session
    from app.graphs.bug_council import run_and_persist

    async def _reindex():
        from app.config import settings
        from app.graph_index import index_repo_symbols
        from app.retrieval import index_repo_files
        from app.sandbox.worktree import create_worktree, ensure_mirror, remove_worktree

        mirror = ensure_mirror(settings.fixture_repo)
        worktree = create_worktree(mirror, issue_number=0, slug="reindex")
        try:
            await asyncio.to_thread(index_repo_files, repo.id, str(worktree))
            await asyncio.to_thread(index_repo_symbols, repo.id, str(worktree))
        finally:
            remove_worktree(mirror, worktree)

    async def _scan(cat: str):
        async with async_session() as scoped_db:
            fresh_repo = await scoped_db.get(Repo, repo.id)
            await run_and_persist(scoped_db, fresh_repo, category=cat)

    asyncio.create_task(_reindex())
    if repo.detection_paused:
        return
    for cat in enabled_categories_for(repo):
        asyncio.create_task(_scan(cat))


async def _handle_pull_request(db: AsyncSession, payload: dict) -> None:
    """Syncs a human's own action on GitHub (merging or closing a PR directly,
    outside WhipGuard entirely) back into the dashboard -- without this, the
    dashboard silently goes stale the moment anyone touches the PR by hand,
    which is exactly the gap that made a real merge invisible in the UI."""
    if payload.get("action") != "closed":
        return

    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    merged = bool(pr.get("merged"))

    fix = (await db.execute(select(Fix).where(Fix.pr_number == pr_number))).scalars().first()
    if not fix:
        return

    issue = await db.get(Issue, fix.issue_id)

    if merged:
        fix.status = FixStatus.MERGED
        if issue:
            issue.status = IssueStatus.CLOSED
        logger.info("PR #%s merged by a human directly on GitHub; fix %s -> merged", pr_number, fix.id)
        await record_calibration_event(db, fix_id=fix.id, outcome="merged")

        repo_full_name = payload.get("repository", {}).get("full_name")
        if repo_full_name and fix.branch_name:
            try:
                await asyncio.to_thread(github_client.delete_branch, repo_full_name, fix.branch_name)
                logger.info("deleted merged branch %s on %s", fix.branch_name, repo_full_name)
            except Exception:
                logger.exception("could not delete merged branch %s on %s", fix.branch_name, repo_full_name)

            from app.routers.ws import emit_event

            emit_event({
                "type": "run", "kind": "cleanup", "status": "done",
                "message": f"PR #{pr_number} merged — branch {fix.branch_name} deleted",
            })
    elif fix.status not in (FixStatus.REJECTED, FixStatus.MERGED):
        # Closed WITHOUT merging, and not already resolved some other way --
        # a human decided not to take this fix. Reject, don't leave it stuck
        # showing "awaiting approval" for a PR that no longer exists as open.
        fix.status = FixStatus.REJECTED
        logger.info("PR #%s closed without merging; fix %s -> rejected", pr_number, fix.id)
        await record_calibration_event(db, fix_id=fix.id, outcome="rejected_on_github")

    await db.commit()

    if issue:
        from app.config import settings
        from app.graphs.approval_graph import _update_slack_status

        repo = await db.get(Repo, issue.repo_id)
        channel_id = (repo.slack_channel_id if repo else None) or settings.slack_channel_id
        _update_slack_status(fix, issue.title, channel_id, f"PR #{pr_number} {'merged' if merged else 'closed'} on GitHub")


async def _handle_issues(db: AsyncSession, payload: dict) -> None:
    """A human closing the GitHub issue directly (not via a merged fix) is
    also a real resolution the dashboard has to reflect. A REOPEN is the
    strongest calibration signal this app can observe mechanically: the jury
    said fixed, a human merged it, and it turned out not to hold -- record
    that against whichever fix most recently claimed to resolve it."""
    action = payload.get("action")
    if action not in ("closed", "reopened"):
        return

    issue_number = payload.get("issue", {}).get("number")
    issue = (
        await db.execute(select(Issue).where(Issue.github_issue_number == issue_number))
    ).scalars().first()
    if not issue:
        return

    if action == "closed":
        if issue.status == IssueStatus.CLOSED:
            return
        issue.status = IssueStatus.CLOSED
        await db.commit()
        logger.info("issue #%s closed on GitHub; issue %s -> closed", issue_number, issue.id)
        return

    # action == "reopened"
    last_merged_fix = (
        await db.execute(
            select(Fix)
            .where(Fix.issue_id == issue.id, Fix.status == FixStatus.MERGED)
            .order_by(Fix.approved_at.desc().nullslast(), Fix.created_at.desc())
        )
    ).scalars().first()

    if last_merged_fix:
        await record_calibration_event(db, fix_id=last_merged_fix.id, outcome="reopened")
        logger.warning("issue #%s reopened on GitHub after fix %s was merged -- calibration event recorded", issue_number, last_merged_fix.id)

    from app.graphs.approval_graph import _reopen_issue_for_retry

    _reopen_issue_for_retry(issue)
    await db.commit()


async def _handle_issue_comment(db: AsyncSession, payload: dict) -> None:
    if payload.get("action") != "created":
        return
    body = payload["comment"]["body"].strip().lower()
    if body != "/reject":
        return

    issue_number = payload["issue"]["number"]
    issue = (
        await db.execute(select(Issue).where(Issue.github_issue_number == issue_number))
    ).scalars().first()
    if not issue:
        return
    fix = (
        await db.execute(select(Fix).where(Fix.issue_id == issue.id).order_by(Fix.created_at.desc()))
    ).scalars().first()
    if fix:
        actor = payload["comment"]["user"]["login"]
        await resolve_approval(db, fix.id, approved=False, actor=actor, surface="github-comment")


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

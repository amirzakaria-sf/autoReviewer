from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

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
    from app.work_queue import enqueue

    # Both of these execute repository code (a git fetch into the workspace,
    # then a sandboxed detector run), so they are queued for the privileged
    # worker rather than run in this web-facing process.
    #
    # `repo.github_full_name`, not settings.fixture_repo: this reindexed the
    # FIXTURE repo on every push to any connected repository, so a second
    # repo's push silently re-embedded the wrong codebase.
    await enqueue("reindex_repo", {"repo_id": str(repo.id), "repo_full_name": repo.github_full_name})

    if repo.detection_paused:
        return
    await enqueue("scan_repo", {"repo_id": str(repo.id), "categories": list(enabled_categories_for(repo))})


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

            # A merged fix's preview and local worktree exist purely to be
            # reviewed before/around the merge -- nobody looks at either
            # again afterward, and every one left behind is real disk
            # (worktree: node_modules, playwright browsers already installed
            # via the sandbox mount) or a live Cloudflare deployment nobody
            # asked to keep running.
            try:
                from app.config import settings
                from app.integrations import cloudflare_client

                deleted = await asyncio.to_thread(
                    cloudflare_client.delete_deployments_for_branch, settings.cloudflare_pages_project, fix.branch_name
                )
                logger.info("deleted %d Cloudflare deployment(s) for merged branch %s", deleted, fix.branch_name)
            except Exception:
                logger.exception("could not delete Cloudflare deployments for merged branch %s", fix.branch_name)

            if issue:
                try:
                    from app.sandbox.worktree import ensure_mirror, remove_worktree, repo_slug

                    mirror = await asyncio.to_thread(ensure_mirror, repo_full_name)
                    worktree_path = (
                        Path(settings.workspace_root) / repo_slug(repo_full_name) / "fixes" / fix.branch_name.split("/")[-1]
                    )
                    await asyncio.to_thread(remove_worktree, mirror, worktree_path)
                    logger.info("removed local worktree for merged branch %s", fix.branch_name)
                except Exception:
                    logger.exception("could not remove local worktree for merged branch %s", fix.branch_name)

            from app.routers.ws import emit_event

            emit_event({
                "type": "run", "kind": "cleanup", "status": "done",
                "message": f"PR #{pr_number} merged — branch, preview, and worktree cleaned up",
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

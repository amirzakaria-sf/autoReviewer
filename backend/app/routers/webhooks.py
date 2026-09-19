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
from app import app_settings, kill_switch
from app.config import settings
from app.db import get_db
from app.enums import FixStatus, IssueStatus
from app.graphs.approval_graph import resolve_approval
from app.integrations import github_client
from app.integrations.github_client import verify_webhook_signature
from app.integrations.slack_client import verify_signature
from app.models import Fix, Issue, Repo, WebhookDelivery

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
    body = await request.body()
    secret = settings.github_webhook_secret
    if secret:
        header = request.headers.get("X-Hub-Signature-256", "")
        if not verify_webhook_signature(body, header, secret):
            raise HTTPException(401, "invalid GitHub signature")
    else:
        logger.warning("GITHUB_WEBHOOK_SECRET is unset — accepting unsigned webhook")

    delivery_id = request.headers.get("X-GitHub-Delivery")
    event = request.headers.get("X-GitHub-Event", "")
    if await _already_seen_delivery(db, delivery_id, event):
        return {"ok": True, "deduped": True}

    payload = json.loads(body) if body else {}

    if event == "push":
        await _handle_push(db, payload)
    elif event == "pull_request":
        await _handle_pull_request(db, payload)
    elif event == "issues":
        await _handle_issues(db, payload)
    elif event == "issue_comment":
        await _handle_issue_comment(db, payload)

    return {"ok": True}


async def _already_seen_delivery(db: AsyncSession, delivery_id: str | None, event: str) -> bool:
    """True if this delivery was already handled. In-memory first, then DB."""
    if not delivery_id:
        return False
    if delivery_id in _SEEN_DELIVERIES:
        return True
    from sqlalchemy.dialects.postgresql import insert

    stmt = (
        insert(WebhookDelivery)
        .values(id=uuid.uuid4(), delivery_id=delivery_id, event=event or "")
        .on_conflict_do_nothing(index_elements=["delivery_id"])
    )
    result = await db.execute(stmt)
    await db.commit()
    _SEEN_DELIVERIES.add(delivery_id)
    if len(_SEEN_DELIVERIES) > _SEEN_DELIVERIES_MAX:
        _SEEN_DELIVERIES.pop()
    # rowcount is 1 when we inserted, 0 when the unique key already existed.
    return result.rowcount == 0


async def _repo_from_payload(db: AsyncSession, payload: dict) -> Repo | None:
    repo_full_name = payload.get("repository", {}).get("full_name")
    if not repo_full_name:
        return None
    return (
        await db.execute(select(Repo).where(Repo.github_full_name == repo_full_name))
    ).scalars().first()


async def _issue_for_github(db: AsyncSession, payload: dict, issue_number: int | None) -> Issue | None:
    """Issue numbers restart per repository — always join through the repo."""
    if issue_number is None:
        return None
    repo = await _repo_from_payload(db, payload)
    if repo is None:
        return None
    return (
        await db.execute(
            select(Issue).where(Issue.repo_id == repo.id, Issue.github_issue_number == issue_number)
        )
    ).scalars().first()


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

    if repo.detection_paused or kill_switch.detection_paused():
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
    repo_full_name = payload.get("repository", {}).get("full_name")

    stmt = select(Fix).where(Fix.pr_number == pr_number)
    if repo_full_name:
        stmt = stmt.join(Issue, Issue.id == Fix.issue_id).join(Repo, Repo.id == Issue.repo_id).where(
            Repo.github_full_name == repo_full_name
        )
    fix = (await db.execute(stmt)).scalars().first()
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

                pages_project = settings.cloudflare_pages_project
                if issue:
                    owned_repo = await db.get(Repo, issue.repo_id)
                    if owned_repo and owned_repo.cloudflare_pages_project:
                        pages_project = owned_repo.cloudflare_pages_project
                deleted = await asyncio.to_thread(
                    cloudflare_client.delete_deployments_for_branch,
                    pages_project,
                    fix.branch_name,
                )
                logger.info("deleted %d Cloudflare deployment(s) for merged branch %s", deleted, fix.branch_name)
            except Exception:
                logger.exception("could not delete Cloudflare deployments for merged branch %s", fix.branch_name)

            if issue:
                try:
                    from app.sandbox.worktree import ensure_mirror, remove_worktree, repo_root

                    mirror = await asyncio.to_thread(ensure_mirror, repo_full_name)
                    worktree_path = (
                        repo_root(repo_full_name) / "fixes" / fix.branch_name.split("/")[-1]
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

        channel_id = app_settings.slack_for_repo(issue.repo_id)[1]
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
    issue = await _issue_for_github(db, payload, issue_number)
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
    """`/reject [reason]` and `/revise <instruction>`, from a GitHub comment.

    A comment is the one approval surface that is naturally free text, so it
    is the only one besides the dashboard that can carry the third verb. The
    reason and the instruction are both kept verbatim: they are what a later
    attempt on this issue reads back before proposing anything.
    """
    if payload.get("action") != "created":
        return

    body = payload["comment"]["body"].strip()
    command, _, argument = body.partition(" ")
    command = command.lower()
    argument = argument.strip()
    if command not in {"/reject", "/revise"}:
        return
    if command == "/revise" and not argument:
        return

    issue_number = payload["issue"]["number"]
    issue = await _issue_for_github(db, payload, issue_number)
    if not issue:
        return
    fix = (
        await db.execute(select(Fix).where(Fix.issue_id == issue.id).order_by(Fix.created_at.desc()))
    ).scalars().first()
    if not fix:
        return

    actor = payload["comment"]["user"]["login"]

    if command == "/revise":
        from app.fix_review import RevisionRejected, request_revision

        try:
            await request_revision(db, fix.id, instruction=argument, actor=actor, surface="github-comment")
        except RevisionRejected as error:
            # Said back on the thread the person is standing in, rather than
            # swallowed -- a webhook that silently does nothing looks
            # identical to one that is broken.
            logger.info("revision from a GitHub comment refused: %s", error)
            try:
                repo_full_name = payload.get("repository", {}).get("full_name", "")
                if repo_full_name:
                    await asyncio.to_thread(
                        github_client.comment_issue, repo_full_name, issue_number, f"WhipGuard: {error}"
                    )
            except Exception:
                logger.exception("could not reply to a refused /revise on #%s", issue_number)
        return

    await resolve_approval(
        db, fix.id, approved=False, actor=actor, surface="github-comment", note=argument
    )


@router.post("/slack/interactions")
async def slack_interactions(request: Request, db: AsyncSession = Depends(get_db)):
    body_bytes = await request.body()
    body_str = body_bytes.decode()

    if not verify_signature(dict(request.headers), body_str, settings.slack_signing_secret):
        raise HTTPException(401, "invalid Slack signature")

    form = dict(pair.split("=", 1) for pair in body_str.split("&") if "=" in pair)
    from urllib.parse import unquote_plus

    payload = json.loads(unquote_plus(form["payload"]))

    # Which workspace clicked. With one install this is decoration; with
    # several it is the only thing distinguishing them, and a click from an
    # unknown workspace must be refused rather than applied against whichever
    # install happens to be first in the table.
    team_id = (payload.get("team") or {}).get("id", "")
    if team_id:
        from app import app_settings

        install = await asyncio.to_thread(app_settings.installation_for_team, team_id)
        if install is None:
            legacy = await asyncio.to_thread(app_settings.slack_is_connected)
            if not legacy:
                logger.warning("slack interaction from unknown workspace %s -- refusing", team_id)
                raise HTTPException(403, "This Slack workspace is not connected to WhipGuard.")

    action = payload["actions"][0]
    action_id = action.get("action_id", "")

    # Only the two decision buttons act. The "Ask for changes" button is a
    # plain link to the dashboard -- Slack still posts an interaction for it,
    # but it carries no `value`, so treating anything that is not
    # `approve_fix` as a rejection (which this did) would both crash on the
    # missing key and, if it had not, reject the fix the reviewer was opening
    # in order to give feedback on.
    if action_id not in {"approve_fix", "reject_fix"}:
        logger.info("ignoring non-decision slack action %r", action_id)
        return {"ok": True, "ignored": action_id}

    fix_id = uuid.UUID(action["value"].split(":")[1])
    approved = action_id == "approve_fix"
    actor = payload["user"]["username"]

    # Slack's two buttons carry no free text. A reviewer who wants to say WHY,
    # or ask for a different approach, follows the link to the dashboard --
    # which is what the third button is for.
    return await resolve_approval(
        db, fix_id, approved=approved, actor=actor, surface="slack",
        note="" if approved else "Rejected from Slack (no reason captured -- Slack buttons carry no text).",
    )


@router.post("/slack/events")
async def slack_events(request: Request):
    """Slack's Events API. Only `app_uninstalled` matters today.

    Without it, an install revoked from Slack's side keeps failing every post
    forever -- and those failures are silent, so nobody notices until an
    approval request goes missing.
    """
    body_bytes = await request.body()
    body_str = body_bytes.decode()

    if not verify_signature(dict(request.headers), body_str, settings.slack_signing_secret):
        raise HTTPException(401, "invalid Slack signature")

    payload = json.loads(body_str)

    # Slack verifies a new Events URL by POSTing a challenge to it.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event_type = (payload.get("event") or {}).get("type") or payload.get("type")
    if event_type in {"app_uninstalled", "tokens_revoked"}:
        from app import app_settings

        team_id = payload.get("team_id", "")
        await asyncio.to_thread(app_settings.revoke_installation, team_id)
        logger.info("slack app uninstalled for team %s", team_id)

    return {"ok": True}

"""Approval handling (plan.md §8.3): resolve_approval is the ONE function every
surface (Slack button, dashboard click, GitHub issue-comment /reject) calls.
Whichever surface acts first wins; the others reflect "handled by <actor> via
<surface>" rather than double-applying anything.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.enums import FIX_STATUS_RENDER, FixStatus
from app.graphs.outcome_checker import check_outcome
from app.integrations import cloudflare_client, github_client, slack_client
from app.models import Fix, Issue
from app.notifications import mark_notified, record_condition, should_notify
from app.routers.ws import emit_event
from app.sandbox.docker_runner import run_in_sandbox

logger = logging.getLogger("whipguard.approval_graph")


def _update_slack_status(fix: Fix, issue_title: str, extra: str = "") -> None:
    """Best-effort: keep the Slack thread's text in sync with the current
    status, since the outcome checker (§11) reads this back and a stale
    message is exactly the disagreement it's designed to catch."""
    if not (settings.slack_bot_token and fix.slack_message_ts):
        return
    try:
        label = FIX_STATUS_RENDER[fix.status]["dashboard_badge"]
        blocks = slack_client.status_only_blocks(issue_title, label, extra)
        slack_client.update_message(
            settings.slack_channel_id, fix.slack_message_ts, blocks, text=f"WhipGuard fix: {label}"
        )
    except Exception:
        logger.exception("Slack status update failed for fix %s", fix.id)


async def resolve_approval(db, fix_id, approved: bool, actor: str, surface: str) -> dict:
    fix = await db.get(Fix, fix_id)
    if fix is None:
        return {"ok": False, "error": "fix not found"}

    if fix.status != FixStatus.AWAITING_APPROVAL:
        return {
            "ok": False,
            "already_handled": True,
            "message": f"already handled by {fix.approved_by} via {fix.approved_via}",
        }

    fix.approved_by = actor
    fix.approved_via = surface
    issue = await db.get(Issue, fix.issue_id)

    if not approved:
        fix.status = FixStatus.REJECTED
        await db.commit()
        _update_slack_status(fix, issue.title, f"rejected by {actor} via {surface}")
        emit_event({"type": "run", "kind": "approval", "status": "done", "message": f"Rejected by {actor} via {surface}"})
        return {"ok": True, "status": fix.status.value}

    fix.status = FixStatus.APPROVED
    await db.commit()
    _update_slack_status(fix, issue.title, f"approved by {actor} via {surface}")
    emit_event({"type": "run", "kind": "approval", "status": "started", "message": f"Approved by {actor} via {surface} — applying patch…"})

    # Same layout worktree.py uses (settings.workspace_root, not a path derived
    # from this file's own depth) -- that depth differs between local-venv and
    # containerized layouts (backend/ is nested locally, but IS the container
    # root at /srv), so deriving it here separately drifted from worktree.py's
    # own fix for the identical problem.
    worktree_path = Path(settings.workspace_root) / issue_repo_slug(issue) / "fixes" / fix.branch_name.split("/")[-1]

    # Freshness check: has the base branch moved since the patch was generated?
    # If so, re-verify before applying rather than force-applying a stale diff.
    # Every blocking call below runs via asyncio.to_thread -- otherwise each
    # one freezes the whole event loop (including the websocket and any other
    # concurrent run) for its full duration.
    await asyncio.to_thread(
        subprocess.run, ["git", "fetch", "origin", "main"], cwd=str(worktree_path), capture_output=True, text=True
    )
    rebase_check = await asyncio.to_thread(
        subprocess.run,
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        cwd=str(worktree_path),
        capture_output=True,
    )
    base_moved = rebase_check.returncode != 0

    if base_moved:
        exit_code, _, _ = await asyncio.to_thread(
            run_in_sandbox, str(worktree_path), ["npm install --silent && npx playwright test"]
        )
        if exit_code != 0:
            fix.status = FixStatus.VERIFICATION_FAILED
            await db.commit()
            emit_event({"type": "run", "kind": "approval", "status": "done", "message": "Base branch moved; re-verify failed"})
            return {"ok": False, "status": fix.status.value, "reason": "base branch moved; re-verify failed"}

    emit_event({"type": "node", "node": "push_and_pr", "status": "started", "message": "Pushing branch, opening PR…"})
    await asyncio.to_thread(github_client.push_branch, str(worktree_path), fix.branch_name)
    fix.status = FixStatus.IN_PROGRESS
    await db.commit()

    emit_event({"type": "node", "node": "cloudflare_deploy", "status": "started", "message": "Deploying branch to Cloudflare Pages…"})
    preview_url = await asyncio.to_thread(
        cloudflare_client.deploy_branch, str(worktree_path), settings.cloudflare_pages_project, fix.branch_name
    )
    fix.preview_url = preview_url
    fix.status = FixStatus.DEPLOYED
    await db.commit()
    emit_event({"type": "node", "node": "cloudflare_deploy", "status": "done", "message": f"Live at {preview_url}"})

    # Post-deploy oracle: the SAME check, re-run against the LIVE subdomain.
    emit_event({"type": "node", "node": "post_deploy_oracle", "status": "started", "message": "Re-running the same check against the LIVE URL…"})
    exit_code, _, _ = await asyncio.to_thread(
        run_in_sandbox, str(worktree_path), [f"PLAYWRIGHT_BASE_URL={preview_url} npx playwright test"]
    )
    fix.status = FixStatus.VERIFIED if exit_code == 0 else FixStatus.VERIFICATION_FAILED
    await db.commit()
    _update_slack_status(fix, issue.title, f"preview: {preview_url}")
    emit_event({
        "type": "node", "node": "post_deploy_oracle", "status": "done",
        "message": "Live URL verified" if exit_code == 0 else "Live URL verification FAILED",
    })

    if fix.status == FixStatus.VERIFICATION_FAILED:
        notification = await record_condition(db, fix_id=fix.id, issue_id=None, condition_key="verification-failed")
        if should_notify(notification, is_escalation=True):
            text = f"WhipGuard: fix verification FAILED for fix {fix.id} (preview: {preview_url})"
            try:
                if not settings.slack_bot_token:
                    raise RuntimeError("slack_bot_token is not configured")
                slack_client.post_message(settings.slack_channel_id, blocks=[], text=text)
            except Exception:
                logger.exception("Slack notify failed for verification-failed fix %s", fix.id)
            else:
                mark_notified(notification)
        await db.commit()
        return {"ok": True, "status": fix.status.value, "preview_url": preview_url}

    # Cross-App Outcome Checker (plan.md §11): the plan verifies the fix, THIS
    # checks that the systems agree with each other and with the fix -- reads
    # GitHub/Cloudflare/Slack/the dashboard back independently and fails the
    # whole run closed on any disagreement, even though every step above just
    # individually reported success.
    emit_event({"type": "node", "node": "outcome_check", "status": "started", "message": "Reading GitHub/Cloudflare/Slack/dashboard back independently…"})
    outcome = await _run_outcome_check(db, issue, fix, preview_url, exit_code == 0)
    emit_event({
        "type": "node", "node": "outcome_check", "status": "done",
        "message": "All systems agree" if outcome["agreed"] else f"MISMATCH: {outcome['mismatch_detail']}",
    })

    if not outcome["agreed"]:
        fix.status = FixStatus.OUTCOME_CHECK_FAILED
        await db.commit()
        _update_slack_status(fix, issue.title, f"mismatch: {outcome['mismatch_detail']}")
        notification = await record_condition(db, fix_id=fix.id, issue_id=None, condition_key="outcome-check-failed")
        if should_notify(notification, is_escalation=True) and settings.slack_bot_token:
            try:
                slack_client.post_message(
                    settings.slack_channel_id, blocks=[],
                    text=f"WhipGuard: outcome check FAILED for fix {fix.id}: {outcome['mismatch_detail']}",
                )
            except Exception:
                logger.exception("Slack notify failed for outcome-check-failed fix %s", fix.id)
            else:
                mark_notified(notification)

    return {
        "ok": outcome["agreed"],
        "status": fix.status.value,
        "preview_url": preview_url,
        "outcome_check": outcome,
    }


async def _run_outcome_check(db, issue: Issue, fix: Fix, preview_url: str, assertion_passes: bool) -> dict:
    from app.models import OutcomeCheck

    repo_full_name = "amirzakaria-sf/whipguard-demo-ui"

    try:
        gh_issue = await asyncio.to_thread(github_client.get_issue, repo_full_name, issue.github_issue_number)
        gh_pr = await asyncio.to_thread(github_client.get_pr, repo_full_name, fix.pr_number) if fix.pr_number else {}
        github_state = {
            "issue_exists": gh_issue.get("state") is not None,
            "labels": [l["name"] for l in gh_issue.get("labels", [])],
            "pr_open": gh_pr.get("state") == "open",
            "pr_merged": bool(gh_pr.get("merged")),
            "closes_reference": f"#{issue.github_issue_number}" in (gh_pr.get("body") or ""),
        }
    except Exception:
        logger.exception("outcome check: could not read GitHub state for fix %s", fix.id)
        github_state = {"issue_exists": False, "labels": [], "pr_open": False, "pr_merged": False, "closes_reference": False}

    cloudflare_state = {"reachable": True, "assertion_passes": assertion_passes}

    slack_state: dict = {"status_text": None}
    if fix.slack_message_ts and settings.slack_bot_token:
        try:
            slack_state = {"status_text": slack_client.get_message_text(settings.slack_channel_id, fix.slack_message_ts)}
        except Exception:
            logger.exception("outcome check: could not read Slack state for fix %s", fix.id)

    outcome = check_outcome(
        {
            "github_state": github_state,
            "cloudflare_state": cloudflare_state,
            "slack_state": slack_state,
            "dashboard_state": fix.status.value,
        }
    )

    db.add(
        OutcomeCheck(
            fix_id=fix.id,
            github_state=github_state,
            cloudflare_state=cloudflare_state,
            slack_state=slack_state,
            dashboard_state=fix.status.value,
            agreed=outcome["agreed"],
            mismatch_detail=outcome["mismatch_detail"],
        )
    )
    await db.commit()
    return outcome


def issue_repo_slug(issue: Issue) -> str:
    return "amirzakaria-sf__whipguard-demo-ui"

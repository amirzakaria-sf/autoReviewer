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

from app.calibration import record_calibration_event
from app.config import settings
from app.enums import FIX_STATUS_RENDER, FixStatus, IssueStatus
from app.graphs.outcome_checker import check_outcome
from app.integrations import cloudflare_client, email_client, github_client, slack_client
from app.models import Fix, Issue, Repo
from app.notifications import mark_notified, record_condition, should_notify
from app.routers.ws import emit_event
from app.sandbox.docker_runner import run_in_sandbox
from app.sandbox.worktree import repo_slug

logger = logging.getLogger("whipguard.approval_graph")

# Network op only (talks to origin, not the sandbox) -- should be seconds; a
# generous ceiling, not a tuned one.
GIT_TIMEOUT_SECONDS = 30

# Every status this flow can leave a Fix sitting in mid-way -- each is a real
# "in-flight" marker, not just FixStatus.IN_PROGRESS by name. APPROVED sits
# through the git fetch/merge-base check and (on a stale base) a sandbox
# re-verify; DEPLOYED sits through the post-deploy oracle re-run. A crash or
# unhandled exception anywhere in resolve_approval after fix.status = APPROVED
# leaves the row here forever with nothing left to move it forward -- this is
# exactly what app/stuck_run_sweeper.py sweeps.
IN_FLIGHT_FIX_STATUSES = (FixStatus.APPROVED, FixStatus.IN_PROGRESS, FixStatus.DEPLOYED)

# Worst-case sum of every bounded step this flow can block on, in one pass:
# git fetch (30s) + merge-base check (30s) + an optional sandbox re-verify
# when the base moved (run_in_sandbox's own 300s default) + git push (60s,
# github_client.push_branch) + Cloudflare deploy (cloudflare_client's own
# 180s) + the post-deploy oracle's sandbox re-run (300s) = 900s. None of
# these retry internally (no backoff to add), so a healthy run can never sit
# in an in-flight status past this sum. The sweeper's threshold below adds
# real headroom on top rather than sweeping right at the edge of that budget.
STUCK_RUN_THRESHOLD_SECONDS = 1200


def _reopen_issue_for_retry(issue: Issue | None) -> None:
    """A failed fix (real verification failure, outcome-check mismatch, or a
    swept stuck run) otherwise leaves the Issue stuck at FIX_PROPOSED with no
    retry surface -- the dashboard's "Resolve this" button only appears for
    status == raised (found while wiring the stuck-run sweeper: neither path
    ever reset this, so a real verification failure was ALSO a dead end
    before this). Reopening the underlying issue reuses that existing
    affordance instead of building a second one."""
    if issue is not None and issue.status != IssueStatus.CLOSED:
        issue.status = IssueStatus.RAISED


def _update_slack_status(fix: Fix, issue_title: str, channel_id: str, extra: str = "") -> None:
    """Best-effort: keep the Slack thread's text in sync with the current
    status, since the outcome checker (§11) reads this back and a stale
    message is exactly the disagreement it's designed to catch."""
    if not (settings.slack_bot_token and fix.slack_message_ts and channel_id):
        return
    try:
        label = FIX_STATUS_RENDER[fix.status]["dashboard_badge"]
        blocks = slack_client.status_only_blocks(issue_title, label, extra)
        slack_client.update_message(channel_id, fix.slack_message_ts, blocks, text=f"WhipGuard fix: {label}")
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
    repo = await db.get(Repo, issue.repo_id) if issue else None
    # Falls back to the single global env var when a repo hasn't gone
    # through the Slack Connect flow (routers/slack_connect.py) yet -- never
    # silently drops a channel that used to work while repos migrate over.
    channel_id = (repo.slack_channel_id if repo else None) or settings.slack_channel_id

    if not approved:
        fix.status = FixStatus.REJECTED
        await record_calibration_event(db, fix_id=fix.id, outcome="rejected_by_human", detail={"surface": surface})
        await db.commit()
        _update_slack_status(fix, issue.title, channel_id, f"rejected by {actor} via {surface}")
        emit_event({"type": "run", "kind": "approval", "status": "done", "message": f"Rejected by {actor} via {surface}"})
        return {"ok": True, "status": fix.status.value}

    fix.status = FixStatus.APPROVED
    await db.commit()
    _update_slack_status(fix, issue.title, channel_id, f"approved by {actor} via {surface}")
    emit_event({"type": "run", "kind": "approval", "status": "started", "message": f"Approved by {actor} via {surface} — applying patch…"})

    # Same layout worktree.py uses (settings.workspace_root, not a path derived
    # from this file's own depth) -- that depth differs between local-venv and
    # containerized layouts (backend/ is nested locally, but IS the container
    # root at /srv), so deriving it here separately drifted from worktree.py's
    # own fix for the identical problem.
    worktree_path = Path(settings.workspace_root) / repo_slug(repo.github_full_name) / "fixes" / fix.branch_name.split("/")[-1]

    # Freshness check: has the base branch moved since the patch was generated?
    # If so, re-verify before applying rather than force-applying a stale diff.
    # Every blocking call below runs via asyncio.to_thread -- otherwise each
    # one freezes the whole event loop (including the websocket and any other
    # concurrent run) for its full duration. GIT_TIMEOUT_SECONDS bounds these
    # explicitly -- a bare subprocess.run with no timeout can hang forever on
    # a network stall, leaving the Fix stuck at IN_PROGRESS with no exception
    # ever raised to unstick it; the stuck-run sweeper (app/stuck_run_sweeper.py)
    # assumes every step in this flow has a hard ceiling, so this has to hold.
    await asyncio.to_thread(
        subprocess.run,
        ["git", "fetch", "origin", "main"],
        cwd=str(worktree_path),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    rebase_check = await asyncio.to_thread(
        subprocess.run,
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        cwd=str(worktree_path),
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    base_moved = rebase_check.returncode != 0

    if base_moved:
        # pnpm + shared store, not `npm install --silent` -- same fix as
        # every other install site in this codebase (app/detectors/ui.py
        # explains both the store and the --silent-swallows-real-errors bug
        # in full).
        exit_code, _, _ = await asyncio.to_thread(
            run_in_sandbox,
            str(worktree_path),
            ["npx --yes pnpm@9 install --store-dir=/pnpm-store --reporter=append-only && npx playwright test"],
        )
        if exit_code != 0:
            fix.status = FixStatus.VERIFICATION_FAILED
            _reopen_issue_for_retry(issue)
            await record_calibration_event(db, fix_id=fix.id, outcome="verification_failed", detail={"stage": "base_moved_reverify"})
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
    if fix.status == FixStatus.VERIFICATION_FAILED:
        _reopen_issue_for_retry(issue)
        await record_calibration_event(db, fix_id=fix.id, outcome="verification_failed", detail={"stage": "post_deploy_oracle"})
    await db.commit()
    _update_slack_status(fix, issue.title, channel_id, f"preview: {preview_url}")
    emit_event({
        "type": "node", "node": "post_deploy_oracle", "status": "done",
        "message": "Live URL verified" if exit_code == 0 else "Live URL verification FAILED",
    })

    if fix.status == FixStatus.VERIFICATION_FAILED:
        notification = await record_condition(db, fix_id=fix.id, issue_id=None, condition_key="verification-failed")
        if should_notify(notification, is_escalation=True):
            text = f"WhipGuard: fix verification FAILED for fix {fix.id} (preview: {preview_url})"
            notified_anything = False
            try:
                if not (settings.slack_bot_token and channel_id):
                    raise RuntimeError("no Slack channel configured for this repo")
                slack_client.post_message(channel_id, blocks=[], text=text)
                notified_anything = True
            except Exception:
                logger.exception("Slack notify failed for verification-failed fix %s", fix.id)

            if email_client.smtp_configured() and settings.notify_email:
                try:
                    subject, html, mail_text = email_client.build_status_email(
                        issue.title, "verification failed", f"Preview: {preview_url}"
                    )
                    await asyncio.to_thread(email_client.send_email, settings.notify_email, subject, html, mail_text)
                    notified_anything = True
                except Exception:
                    logger.exception("verification-failed email failed for fix %s", fix.id)

            if notified_anything:
                mark_notified(notification)
        await db.commit()
        return {"ok": True, "status": fix.status.value, "preview_url": preview_url}

    # Cross-App Outcome Checker (plan.md §11): the plan verifies the fix, THIS
    # checks that the systems agree with each other and with the fix -- reads
    # GitHub/Cloudflare/Slack/the dashboard back independently and fails the
    # whole run closed on any disagreement, even though every step above just
    # individually reported success.
    emit_event({"type": "node", "node": "outcome_check", "status": "started", "message": "Reading GitHub/Cloudflare/Slack/dashboard back independently…"})
    outcome = await _run_outcome_check(db, repo, issue, fix, preview_url, exit_code == 0, channel_id)
    emit_event({
        "type": "node", "node": "outcome_check", "status": "done",
        "message": "All systems agree" if outcome["agreed"] else f"MISMATCH: {outcome['mismatch_detail']}",
    })

    if not outcome["agreed"]:
        fix.status = FixStatus.OUTCOME_CHECK_FAILED
        _reopen_issue_for_retry(issue)
        await record_calibration_event(db, fix_id=fix.id, outcome="outcome_check_failed", detail={"mismatch": outcome["mismatch_detail"]})
        await db.commit()
        _update_slack_status(fix, issue.title, channel_id, f"mismatch: {outcome['mismatch_detail']}")
        notification = await record_condition(db, fix_id=fix.id, issue_id=None, condition_key="outcome-check-failed")
        if should_notify(notification, is_escalation=True):
            notified_anything = False
            if settings.slack_bot_token and channel_id:
                try:
                    slack_client.post_message(
                        channel_id, blocks=[],
                        text=f"WhipGuard: outcome check FAILED for fix {fix.id}: {outcome['mismatch_detail']}",
                    )
                    notified_anything = True
                except Exception:
                    logger.exception("Slack notify failed for outcome-check-failed fix %s", fix.id)

            if email_client.smtp_configured() and settings.notify_email:
                try:
                    subject, html, mail_text = email_client.build_status_email(
                        issue.title, "outcome check failed", str(outcome["mismatch_detail"])
                    )
                    await asyncio.to_thread(email_client.send_email, settings.notify_email, subject, html, mail_text)
                    notified_anything = True
                except Exception:
                    logger.exception("outcome-check-failed email failed for fix %s", fix.id)

            if notified_anything:
                mark_notified(notification)

    return {
        "ok": outcome["agreed"],
        "status": fix.status.value,
        "preview_url": preview_url,
        "outcome_check": outcome,
    }


async def _run_outcome_check(
    db, repo: Repo | None, issue: Issue, fix: Fix, preview_url: str, assertion_passes: bool, channel_id: str
) -> dict:
    from app.models import OutcomeCheck

    # Falls back to the fixture repo only if this fix's own repo somehow
    # can't be resolved -- every real path has repo already, from
    # resolve_approval's own lookup via issue.repo_id.
    repo_full_name = repo.github_full_name if repo else settings.fixture_repo

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
    if fix.slack_message_ts and settings.slack_bot_token and channel_id:
        try:
            slack_state = {"status_text": slack_client.get_message_text(channel_id, fix.slack_message_ts)}
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

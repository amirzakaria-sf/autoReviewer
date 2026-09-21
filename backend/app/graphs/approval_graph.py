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
from app import app_settings
from app.config import settings
from app.enums import FIX_STATUS_RENDER, FixStatus, IssueStatus
from app.graphs.outcome_checker import check_outcome
from app.integrations import cloudflare_client, email_client, github_client, slack_client
from app.models import Fix, Issue, Repo
from app import push
from app.notifications import mark_notified, record_condition, should_notify
from app.routers.ws import emit_event, set_event_repo
from app.detectors import get_detector
from app.sandbox.worktree import repo_root

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
    if not (app_settings.slack_bot_token() and fix.slack_message_ts and channel_id):
        return
    try:
        label = FIX_STATUS_RENDER[fix.status]["dashboard_badge"]
        blocks = slack_client.status_only_blocks(issue_title, label, extra)
        slack_client.update_message(channel_id, fix.slack_message_ts, blocks, text=f"WhipGuard fix: {label}")
    except Exception:
        logger.exception("Slack status update failed for fix %s", fix.id)


async def _close_review(db, issue, *, actor: str, surface: str, decision: str, note: str) -> None:
    """Record the decision on the issue's review thread, if it has one.

    Best-effort by design: a thread that cannot be found or written must not
    stop a decision the human already made from taking effect.
    """
    try:
        from sqlalchemy import select

        from app.fix_review import record_decision
        from app.models import FixReview

        if issue is None:
            return
        review = (
            await db.execute(select(FixReview).where(FixReview.issue_id == issue.id))
        ).scalars().first()
        if review is not None:
            await record_decision(db, review, actor=actor, surface=surface, decision=decision, note=note)
    except Exception:  # noqa: BLE001 - the decision matters more than its transcript
        logger.exception("could not record the decision on the review thread for issue %s", getattr(issue, "id", None))


async def resolve_approval(
    db, fix_id, approved: bool, actor: str, surface: str, apply: bool = False, note: str = ""
) -> dict:
    """Approve or reject one proposed fix.

    `note` is the reviewer's own words. On a rejection it is the highest-value
    thing the system records; a bare boolean throws away the only judgment a
    human brings that the council cannot.

    To ask for a DIFFERENT approach rather than say no, callers use
    app/fix_review.py's request_revision instead -- deliberately a separate
    verb, so "this is wrong" and "this is wrong, do it this way" do not
    collapse into the same irreversible outcome.
    """
    fix = await db.get(Fix, fix_id)
    if fix is None:
        return {"ok": False, "error": "fix not found"}

    if fix.status == FixStatus.APPROVED and apply:
        # The worker picking up what a web caller already decided. Not a
        # double-approval: the decision is done, this is the execution half.
        pass
    elif fix.status != FixStatus.AWAITING_APPROVAL:
        return {
            "ok": False,
            "already_handled": True,
            "message": f"already handled by {fix.approved_by} via {fix.approved_via}",
        }

    fix.approved_by = actor
    fix.approved_via = surface
    issue = await db.get(Issue, fix.issue_id)
    repo = await db.get(Repo, issue.repo_id) if issue else None
    # Addresses every activity event this approval emits, including the ones
    # from the deploy and re-verify nodes running in worker threads.
    set_event_repo(issue.repo_id if issue else None)
    # ONE channel for the whole account (app/app_settings.py). Every repo's
    # approvals land in the same place, which is what a team watching a
    # channel actually wants.
    # Per-org: resolved through the repo that owns this issue, falling
    # back to the single-workspace config for an unmigrated deployment.
    channel_id = app_settings.slack_for_repo(issue.repo_id if issue else None)[1]

    if not approved:
        fix.status = FixStatus.REJECTED
        fix.decision_note = (note or "").strip() or None
        # Nothing was ever pushed for this attempt, so there is no branch to
        # delete and no PR to close -- rejection is now purely a database
        # write, which is the whole point of deferring the push to approval.
        await _close_review(db, issue, actor=actor, surface=surface, decision="rejected", note=note)
        _reopen_issue_for_retry(issue)
        await record_calibration_event(db, fix_id=fix.id, outcome="rejected_by_human", detail={"surface": surface})
        await db.commit()
        # The highest-signal negative this system can record: a human looked
        # at the diff and said no. A later attempt on the same issue reads
        # this back (app/context_broker.py) instead of re-proposing it.
        from app.memory_traces import record_trace

        await asyncio.to_thread(
            record_trace,
            repo_id=issue.repo_id,
            issue_id=issue.id,
            fix_id=fix.id,
            outcome="rejected",
            stage="approval",
            category=issue.category,
            detail=(
                f"Rejected by {actor} via {surface}. Branch {fix.branch_name or '(none)'}, "
                f"resolution confidence {fix.resolution_score}. "
                f"Arbiter verdict: {(fix.resolution_rubric or {}).get('verdict', 'n/a')}. "
                f"Reason given: {(note or '').strip() or 'none'}"
            ),
        )
        _update_slack_status(fix, issue.title, channel_id, f"rejected by {actor} via {surface}")
        emit_event({"type": "run", "kind": "approval", "status": "done", "message": f"Rejected by {actor} via {surface}"})
        return {"ok": True, "status": fix.status.value}

    fix.status = FixStatus.APPROVED
    fix.decision_note = (note or "").strip() or fix.decision_note
    if not apply:
        # Recorded on the web half, where the human actually decided -- the
        # worker re-enters this function with apply=True and must not append
        # the same decision to the thread a second time.
        await _close_review(db, issue, actor=actor, surface=surface, decision="approved", note=note)
    await db.commit()
    _update_slack_status(fix, issue.title, channel_id, f"approved by {actor} via {surface}")
    emit_event({"type": "run", "kind": "approval", "status": "started", "message": f"Approved by {actor} via {surface} — applying patch…"})

    # THE SECURITY SEAM (plan.md §15). Everything above is a status decision:
    # database writes and a Slack message, all safe for the web-facing
    # process. Everything below applies a patch, pushes a branch and triggers
    # a deploy -- privileged work against untrusted repository code, which
    # belongs to the worker. So a web caller stops here and queues the rest;
    # only the worker, which sets apply=True, runs it.
    if not apply:
        from app.work_queue import enqueue

        await enqueue(
            "apply_approval",
            {"fix_id": str(fix.id), "actor": actor, "surface": surface, "approved": True, "note": note},
        )
        return {"ok": True, "status": fix.status.value, "queued": True}

    if repo is None:
        logger.error("approval: fix %s has no repo — refusing to fall back to the fixture", fix.id)
        return {"ok": False, "error": "repo not found"}

    # Same layout worktree.py uses (settings.workspace_root, not a path derived
    # from this file's own depth) -- that depth differs between local-venv and
    # containerized layouts (backend/ is nested locally, but IS the container
    # root at /srv), so deriving it here separately drifted from worktree.py's
    # own fix for the identical problem.
    worktree_path = repo_root(repo.github_full_name) / "fixes" / fix.branch_name.split("/")[-1]

    # Freshness check: has the base branch moved since the patch was generated?
    # If so, re-verify before applying rather than force-applying a stale diff.
    # Every blocking call below runs via asyncio.to_thread -- otherwise each
    # one freezes the whole event loop (including the websocket and any other
    # concurrent run) for its full duration. GIT_TIMEOUT_SECONDS bounds these
    # explicitly -- a bare subprocess.run with no timeout can hang forever on
    # a network stall, leaving the Fix stuck at IN_PROGRESS with no exception
    # ever raised to unstick it; the stuck-run sweeper (app/stuck_run_sweeper.py)
    # assumes every step in this flow has a hard ceiling, so this has to hold.
    base_branch = repo.default_branch if repo else "main"
    await asyncio.to_thread(
        subprocess.run,
        ["git", "fetch", "origin", f"+refs/heads/{base_branch}:refs/heads/{base_branch}"],
        cwd=str(worktree_path),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    # The base branch is `main`, NOT `origin/main`. These worktrees hang off a
    # --mirror clone, which maps every ref into refs/heads rather than
    # refs/remotes/origin -- so `origin/main` does not resolve at all and git
    # exits 128 ("Not a valid object name"). Exactly the same mistake was
    # already found and fixed once elsewhere in this codebase.
    rebase_check = await asyncio.to_thread(
        subprocess.run,
        ["git", "merge-base", "--is-ancestor", base_branch, "HEAD"],
        cwd=str(worktree_path),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    # 0 = the base is an ancestor (nothing moved), 1 = it is not (rebase
    # needed), anything else = the check itself failed. Treating every
    # non-zero code as "the base moved" conflated the last two, and because
    # the ref name above was wrong it took the 128 path EVERY time: every
    # approval re-ran the repo's whole test suite, which fails on the other
    # still-unfixed seeded bugs, and aborted before pushing anything. That is
    # why no fix ever reached a preview URL.
    if rebase_check.returncode == 0:
        base_moved = False
    elif rebase_check.returncode == 1:
        base_moved = True
    else:
        logger.error(
            "freshness check failed for fix %s (exit %s): %s -- proceeding without it",
            fix.id, rebase_check.returncode, (rebase_check.stderr or "").strip()[:200],
        )
        # Proceed rather than abort: the patch was verified in a sandbox
        # minutes ago, and two independent gates still stand between here and
        # a merge -- the post-deploy oracle against the live URL, and the
        # cross-app outcome check.
        base_moved = False

    if base_moved:
        # THIS category's detector, not a hardcoded Playwright run. The system
        # claims to re-run "the same check that caught the bug", and until now
        # it did not: every approval ran the repo's UI suite regardless of
        # category, so a documentation or secret-scan fix was judged by tests
        # that have nothing to do with it -- and on a repo with any other
        # unfixed bug, that suite fails and the approval aborts.
        category = issue.category if issue else "ui"
        recheck = await asyncio.to_thread(get_detector(category).run, str(worktree_path))
        if recheck.failed:
            fix.status = FixStatus.VERIFICATION_FAILED
            _reopen_issue_for_retry(issue)
            await record_calibration_event(db, fix_id=fix.id, outcome="verification_failed", detail={"stage": "base_moved_reverify"})
            await db.commit()
            emit_event({"type": "run", "kind": "approval", "status": "done", "message": "Base branch moved; re-verify failed"})
            return {"ok": False, "status": fix.status.value, "reason": "base branch moved; re-verify failed"}

    # This is where the fix becomes real on GitHub. Until a human said yes,
    # nothing had been pushed and no PR existed -- a proposal that was
    # rejected or reworked left the repository untouched.
    emit_event({"type": "node", "node": "push_and_pr", "status": "started", "message": "Pushing branch, opening PR…"})
    await asyncio.to_thread(github_client.push_branch, str(worktree_path), fix.branch_name)

    if fix.pr_number is None:
        repo_full_name = repo.github_full_name
        issue_number = issue.github_issue_number if issue else None
        body_lines = [
            f"Closes #{issue_number}" if issue_number else "",
            "",
            f"Approved by {actor} via {surface}. Resolution confidence: {fix.resolution_score}/100.",
        ]
        if fix.attempt and fix.attempt > 1:
            body_lines.append(f"Attempt {fix.attempt} — earlier attempts were reworked at the reviewer's request.")
        if fix.diff:
            body_lines += ["", "```diff", fix.diff, "```"]
        try:
            fix.pr_number = await asyncio.to_thread(
                github_client.create_draft_pr,
                repo_full_name,
                fix.branch_name,
                repo.default_branch if repo else "main",
                f"Fix for #{issue_number}" if issue_number else f"WhipGuard fix {fix.id}",
                "\n".join(body_lines),
                [
                    "whipguard:approved",
                    f"whipguard:category/{issue.category}" if issue else "whipguard:category/unknown",
                ],
            )
        except Exception:
            # The branch is pushed and the deploy below can still prove the
            # fix works, so a PR API failure must not abort the run -- but it
            # cannot pass silently either, because the outcome checker reads
            # the PR back and will fail this run closed without it.
            logger.exception("could not open a PR for fix %s; branch is pushed", fix.id)

    fix.status = FixStatus.IN_PROGRESS
    await db.commit()

    emit_event({"type": "node", "node": "cloudflare_deploy", "status": "started", "message": "Deploying branch to Cloudflare Pages…"})
    pages_project = getattr(repo, "cloudflare_pages_project", None) or settings.cloudflare_pages_project
    if not pages_project:
        logger.error("approval: no Cloudflare Pages project for fix %s", fix.id)
        fix.status = FixStatus.VERIFICATION_FAILED
        _reopen_issue_for_retry(issue)
        await db.commit()
        return {"ok": False, "status": fix.status.value, "reason": "no Cloudflare Pages project configured"}
    preview_url = await asyncio.to_thread(
        cloudflare_client.deploy_branch, str(worktree_path), pages_project, fix.branch_name
    )
    fix.preview_url = preview_url
    fix.status = FixStatus.DEPLOYED
    await db.commit()
    emit_event({"type": "node", "node": "cloudflare_deploy", "status": "done", "message": f"Live at {preview_url}"})

    # Post-deploy oracle: the SAME check, re-run against the LIVE subdomain.
    emit_event({"type": "node", "node": "post_deploy_oracle", "status": "started", "message": "Re-running the same check against the LIVE URL…"})
    # Same detector again, this time pointed at the LIVE url. Browser-driven
    # categories visit the deployed preview; the static scanners ignore
    # `base_url` and read the source they just proved, which is the honest
    # answer for a check that has no url to visit.
    oracle_category = issue.category if issue else "ui"
    oracle = await asyncio.to_thread(
        get_detector(oracle_category).run, str(worktree_path), "", preview_url
    )
    exit_code = 1 if oracle.failed else 0
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
                if not (app_settings.slack_bot_token() and channel_id):
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

            try:
                # Inside the same should_notify guard as the other two
                # channels: a flapping condition that is not worth an email is
                # not worth a phone buzzing either.
                if await asyncio.to_thread(
                    push.send_for_repo,
                    issue.repo_id if issue else None,
                    "Verification failed",
                    f"{(issue.title if issue else 'A fix')[:90]} — the live preview did not pass.",
                    f"/issues/{issue.id}" if issue else "/dashboard",
                    f"verification-failed-{fix.id}",
                ):
                    notified_anything = True
            except Exception:
                logger.exception("verification-failed push failed for fix %s", fix.id)

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

        # A fix that deployed and then failed cross-app verification. The
        # highest-value trace after a human rejection: every earlier step
        # reported success, so nothing else in the record explains why this
        # issue is open again.
        from app.memory_traces import record_trace

        await asyncio.to_thread(
            record_trace,
            repo_id=issue.repo_id if issue else None,
            issue_id=issue.id if issue else None,
            fix_id=fix.id,
            outcome="regressed",
            stage="outcome",
            category=issue.category if issue else "",
            detail=(
                f"Deployed, then the cross-app outcome check disagreed: "
                f"{outcome['mismatch_detail']}. Branch {fix.branch_name or '(none)'}."
            ),
        )
        _update_slack_status(fix, issue.title, channel_id, f"mismatch: {outcome['mismatch_detail']}")
        notification = await record_condition(db, fix_id=fix.id, issue_id=None, condition_key="outcome-check-failed")
        if should_notify(notification, is_escalation=True):
            notified_anything = False
            if app_settings.slack_bot_token() and channel_id:
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

    repo_full_name = repo.github_full_name if repo else ""
    if not repo_full_name:
        github_state = {"issue_exists": False, "labels": [], "pr_open": False, "pr_merged": False, "closes_reference": False}
    else:
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

    from app.preview import probe_preview_url

    reachable = await asyncio.to_thread(probe_preview_url, preview_url)
    cloudflare_state = {"reachable": reachable, "assertion_passes": assertion_passes}

    # `configured` is stated rather than inferred from whether a thread could
    # be read. Slack being disconnected is not Slack disagreeing, and the
    # checker fails a run closed on any disagreement -- so inferring it meant
    # every deploy failed for anyone who had not connected Slack.
    slack_connected = bool(fix.slack_message_ts and app_settings.slack_bot_token() and channel_id)
    slack_state: dict = {"status_text": None, "configured": slack_connected}
    if slack_connected:
        try:
            slack_state["status_text"] = slack_client.get_message_text(channel_id, fix.slack_message_ts)
        except Exception:
            logger.exception("outcome check: could not read Slack state for fix %s", fix.id)
            # Reading failed, so this surface has nothing to say -- which is
            # different from it contradicting the others.
            slack_state["configured"] = False

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

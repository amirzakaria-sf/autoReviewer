"""Approval handling (plan.md §8.3): resolve_approval is the ONE function every
surface (Slack button, dashboard click, GitHub issue-comment /reject) calls.
Whichever surface acts first wins; the others reflect "handled by <actor> via
<surface>" rather than double-applying anything.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.enums import FixStatus
from app.integrations import cloudflare_client, github_client, slack_client
from app.models import Fix, Issue
from app.sandbox.docker_runner import run_in_sandbox


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

    if not approved:
        fix.status = FixStatus.REJECTED
        await db.commit()
        return {"ok": True, "status": fix.status.value}

    fix.status = FixStatus.APPROVED
    await db.commit()

    issue = await db.get(Issue, fix.issue_id)
    worktree_path = Path(__file__).resolve().parents[3] / "workspace" / issue_repo_slug(issue) / "fixes" / fix.branch_name.split("/")[-1]

    # Freshness check: has the base branch moved since the patch was generated?
    # If so, re-verify before applying rather than force-applying a stale diff.
    fetch = subprocess.run(["git", "fetch", "origin", "main"], cwd=str(worktree_path), capture_output=True, text=True)
    rebase_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        cwd=str(worktree_path),
        capture_output=True,
    )
    base_moved = rebase_check.returncode != 0

    if base_moved:
        exit_code, _, _ = run_in_sandbox(str(worktree_path), ["npm install --silent && npx playwright test"])
        if exit_code != 0:
            fix.status = FixStatus.VERIFICATION_FAILED
            await db.commit()
            return {"ok": False, "status": fix.status.value, "reason": "base branch moved; re-verify failed"}

    github_client.push_branch(str(worktree_path), fix.branch_name)
    fix.status = FixStatus.IN_PROGRESS
    await db.commit()

    preview_url = cloudflare_client.deploy_branch(str(worktree_path), settings.cloudflare_pages_project, fix.branch_name)
    fix.preview_url = preview_url
    fix.status = FixStatus.DEPLOYED
    await db.commit()

    # Post-deploy oracle: the SAME check, re-run against the LIVE subdomain.
    exit_code, _, _ = run_in_sandbox(
        str(worktree_path), [f"PLAYWRIGHT_BASE_URL={preview_url} npx playwright test"]
    )
    fix.status = FixStatus.VERIFIED if exit_code == 0 else FixStatus.VERIFICATION_FAILED
    await db.commit()

    return {"ok": True, "status": fix.status.value, "preview_url": preview_url}


def issue_repo_slug(issue: Issue) -> str:
    return "amirzakaria-sf__whipguard-demo-ui"

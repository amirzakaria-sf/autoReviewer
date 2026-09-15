"""GitHub client. No merge method exists on this client — not a config flag that
defaults off, an absent capability (plan.md §1's forbidden-action rule).
"""

from __future__ import annotations

import logging
import subprocess

import httpx

from app.config import settings
from app.integrations import github_app_auth

API_BASE = "https://api.github.com"
logger = logging.getLogger("whipguard.github_client")


def _headers() -> dict:
    # Prefers the GitHub App installation token (a real, distinct bot
    # identity -- see github_app_auth.py) whenever an App is configured;
    # falls back to the existing OAuth/PAT bearer token unchanged otherwise,
    # so nothing breaks for a deployment that hasn't generated an App
    # private key yet.
    token = settings.github_token
    if github_app_auth.github_app_configured():
        try:
            token = github_app_auth.get_installation_token()
        except Exception:
            logger.exception("GitHub App token mint failed; falling back to the configured PAT/OAuth token")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def create_issue(repo: str, title: str, body: str, labels: list[str]) -> int:
    resp = httpx.post(
        f"{API_BASE}/repos/{repo}/issues",
        headers=_headers(),
        json={"title": title, "body": body, "labels": labels},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["number"]


def create_draft_pr(repo: str, branch: str, base: str, title: str, body: str, labels: list[str]) -> int:
    resp = httpx.post(
        f"{API_BASE}/repos/{repo}/pulls",
        headers=_headers(),
        json={"title": title, "head": branch, "base": base, "body": body, "draft": True},
        timeout=30,
    )
    resp.raise_for_status()
    pr_number = resp.json()["number"]

    if labels:
        label_resp = httpx.post(
            f"{API_BASE}/repos/{repo}/issues/{pr_number}/labels",
            headers=_headers(),
            json={"labels": labels},
            timeout=30,
        )
        label_resp.raise_for_status()

    return pr_number


def comment_issue(repo: str, issue_number: int, body: str) -> None:
    resp = httpx.post(
        f"{API_BASE}/repos/{repo}/issues/{issue_number}/comments",
        headers=_headers(),
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()


def list_issues_with_label(repo: str, label: str) -> list[dict]:
    resp = httpx.get(
        f"{API_BASE}/repos/{repo}/issues",
        headers=_headers(),
        params={"labels": label, "state": "open"},
        timeout=30,
    )
    resp.raise_for_status()
    # The issues endpoint also returns PRs; filter those out.
    return [item for item in resp.json() if "pull_request" not in item]


def get_issue(repo: str, number: int) -> dict:
    resp = httpx.get(f"{API_BASE}/repos/{repo}/issues/{number}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_pr(repo: str, number: int) -> dict:
    resp = httpx.get(f"{API_BASE}/repos/{repo}/pulls/{number}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def push_branch(worktree_path: str, branch: str) -> None:
    """Push `branch` from a worktree to `origin`. Never force-pushes."""
    subprocess.run(
        ["git", "push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def delete_branch(repo: str, branch: str) -> None:
    """Deletes a head branch from origin. Called only after GitHub itself
    confirms the branch's PR was merged (webhooks.py's pull_request handler)
    -- never speculatively, and never for main/base branches since callers
    only ever pass a Fix.branch_name (always a whipguard/fix-* branch)."""
    resp = httpx.delete(f"{API_BASE}/repos/{repo}/git/refs/heads/{branch}", headers=_headers(), timeout=30)
    if resp.status_code not in (204, 422):
        resp.raise_for_status()

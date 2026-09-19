"""GitHub client. No merge method exists on this client — not a config flag that
defaults off, an absent capability (plan.md §1's forbidden-action rule).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import subprocess

import httpx

from app.config import settings
from app.integrations import github_app_auth

API_BASE = "https://api.github.com"
logger = logging.getLogger("whipguard.github_client")


def verify_webhook_signature(body: bytes, header: str, secret: str) -> bool:
    """GitHub's X-Hub-Signature-256: `sha256=<hex>`."""
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


def _headers(repo: str | None = None) -> dict:
    # Prefers the GitHub App installation token (a real, distinct bot
    # identity -- see github_app_auth.py) whenever an App is configured;
    # falls back to the existing OAuth/PAT bearer token unchanged otherwise,
    # so nothing breaks for a deployment that hasn't generated an App
    # private key yet.
    token = settings.github_token
    if github_app_auth.github_app_configured():
        try:
            installation_id = github_app_auth.installation_id_for_repo(repo) if repo else None
            token = github_app_auth.get_installation_token(installation_id)
        except Exception:
            logger.exception("GitHub App token mint failed; falling back to the configured PAT/OAuth token")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def create_issue(repo: str, title: str, body: str, labels: list[str]) -> int:
    resp = httpx.post(
        f"{API_BASE}/repos/{repo}/issues",
        headers=_headers(repo),
        json={"title": title, "body": body, "labels": labels},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["number"]


def create_draft_pr(repo: str, branch: str, base: str, title: str, body: str, labels: list[str]) -> int:
    resp = httpx.post(
        f"{API_BASE}/repos/{repo}/pulls",
        headers=_headers(repo),
        json={"title": title, "head": branch, "base": base, "body": body, "draft": True},
        timeout=30,
    )
    resp.raise_for_status()
    pr_number = resp.json()["number"]

    if labels:
        label_resp = httpx.post(
            f"{API_BASE}/repos/{repo}/issues/{pr_number}/labels",
            headers=_headers(repo),
            json={"labels": labels},
            timeout=30,
        )
        label_resp.raise_for_status()

    return pr_number


def comment_issue(repo: str, issue_number: int, body: str) -> None:
    resp = httpx.post(
        f"{API_BASE}/repos/{repo}/issues/{issue_number}/comments",
        headers=_headers(repo),
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()


def list_issues_with_label(repo: str, label: str) -> list[dict]:
    resp = httpx.get(
        f"{API_BASE}/repos/{repo}/issues",
        headers=_headers(repo),
        params={"labels": label, "state": "open"},
        timeout=30,
    )
    resp.raise_for_status()
    # The issues endpoint also returns PRs; filter those out.
    return [item for item in resp.json() if "pull_request" not in item]


def get_issue(repo: str, number: int) -> dict:
    resp = httpx.get(f"{API_BASE}/repos/{repo}/issues/{number}", headers=_headers(repo), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_pr(repo: str, number: int) -> dict:
    resp = httpx.get(f"{API_BASE}/repos/{repo}/pulls/{number}", headers=_headers(repo), timeout=30)
    resp.raise_for_status()
    return resp.json()


def push_branch(worktree_path: str, branch: str) -> None:
    """Push `branch` from a worktree to `origin`. Never force-pushes.

    The remote URL is inherited from the mirror this worktree hangs off, and
    carries the credential -- see app/sandbox/worktree.py's
    `authenticated_remote`, which also explains why a malformed one only ever
    fails HERE and never at clone time.
    """
    from app.sandbox.worktree import _git_env

    subprocess.run(
        ["git", "push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=_git_env(),
    )


def delete_branch(repo: str, branch: str) -> None:
    """Deletes a head branch from origin. Called only after GitHub itself
    confirms the branch's PR was merged (webhooks.py's pull_request handler)
    -- never speculatively, and never for main/base branches since callers
    only ever pass a Fix.branch_name (always a whipguard/fix-* branch)."""
    resp = httpx.delete(f"{API_BASE}/repos/{repo}/git/refs/heads/{branch}", headers=_headers(repo), timeout=30)
    if resp.status_code not in (204, 422):
        resp.raise_for_status()


def list_dependabot_alerts(repo: str) -> list[dict]:
    """GitHub Dependabot / Advisory alerts. Empty on 403/404 (no permission
    or not enabled) rather than failing a security scan that still has the
    regex/gitleaks path."""
    resp = httpx.get(
        f"{API_BASE}/repos/{repo}/dependabot/alerts",
        headers=_headers(repo),
        params={"state": "open", "per_page": 20},
        timeout=20,
    )
    if resp.status_code in (401, 403, 404):
        return []
    resp.raise_for_status()
    return resp.json() if isinstance(resp.json(), list) else []

"""Cloudflare Pages client — direct upload deploy, not a git-connected project
(plan.md §6.2), so "deploy" is a step this system runs on its own schedule."""

from __future__ import annotations

import subprocess

import httpx

from app.config import settings

API_BASE = "https://api.cloudflare.com/client/v4"


def deploy_branch(worktree_path: str, project_name: str, branch: str) -> str:
    result = subprocess.run(
        [
            "npx",
            "wrangler",
            "pages",
            "deploy",
            ".",
            "--project-name",
            project_name,
            "--branch",
            branch,
        ],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        env={
            "CLOUDFLARE_API_TOKEN": settings.cloudflare_api_token,
            "CLOUDFLARE_ACCOUNT_ID": settings.cloudflare_account_id,
            "PATH": "/usr/bin:/usr/local/bin",
        },
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wrangler deploy failed: {result.stderr}")

    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("https://") and "pages.dev" in line:
            return line
    raise RuntimeError(f"could not find a preview URL in wrangler output:\n{result.stdout}")


def get_deployment_status(project_name: str) -> dict:
    resp = httpx.get(
        f"{API_BASE}/accounts/{settings.cloudflare_account_id}/pages/projects/{project_name}/deployments",
        headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

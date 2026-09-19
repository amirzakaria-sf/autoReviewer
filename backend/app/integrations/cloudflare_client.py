"""Cloudflare Pages client — direct upload deploy, not a git-connected project
(plan.md §6.2), so "deploy" is a step this system runs on its own schedule."""

from __future__ import annotations

import os
import re
import subprocess

import httpx

from app.config import settings

API_BASE = "https://api.cloudflare.com/client/v4"


def deploy_branch(worktree_path: str, project_name: str, branch: str) -> str:
    result = subprocess.run(
        [
            # Pinned to a major version, and --yes so a first run in a fresh
            # container installs it instead of waiting on a prompt nobody can
            # answer. Unpinned, a wrangler major release would change this
            # command's behaviour on a deploy nobody touched.
            "npx",
            "--yes",
            "wrangler@3",
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
        # The real environment plus the credentials, not a hand-written PATH.
        # A two-entry PATH has to be kept in step with wherever the base image
        # happens to put node, and silently breaks the deploy when it is not.
        env={
            **os.environ,
            "CLOUDFLARE_API_TOKEN": settings.cloudflare_api_token,
            "CLOUDFLARE_ACCOUNT_ID": settings.cloudflare_account_id,
            # wrangler writes caches and telemetry under $HOME; without a
            # writable one it fails on a permissions error unrelated to the
            # deploy itself.
            "HOME": os.environ.get("HOME", "/tmp"),
        },
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wrangler deploy failed: {result.stderr}")

    # wrangler embeds the URL mid-sentence ("Take a peek over at https://...",
    # "Deployment alias URL: https://..."), never on its own line — a line-start
    # match found this at 0/1 real deploys. Prefer the alias URL: it's stable
    # per-branch (matches the branch name), while the bare "Deployment complete"
    # URL is a fresh, ephemeral hash on every single deploy of the same branch.
    urls = re.findall(r"https://\S+\.pages\.dev\S*", result.stdout)
    for line in result.stdout.splitlines():
        if "alias" in line.lower():
            match = re.search(r"https://\S+\.pages\.dev\S*", line)
            if match:
                return match.group(0)
    if urls:
        return urls[-1]
    raise RuntimeError(f"could not find a preview URL in wrangler output:\n{result.stdout}")


def get_deployment_status(project_name: str) -> dict:
    resp = httpx.get(
        f"{API_BASE}/accounts/{settings.cloudflare_account_id}/pages/projects/{project_name}/deployments",
        headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def delete_deployments_for_branch(project_name: str, branch: str) -> int:
    """Deletes every live deployment for one branch -- called once a fix's
    PR merges (webhooks.py), so a preview nobody will ever look at again
    doesn't sit there indefinitely. Cloudflare Pages has no "delete by
    branch" endpoint; this lists deployments and filters on
    deployment_trigger.metadata.branch (verified against this project's
    real deployment list before writing this, not assumed from docs), then
    deletes each matching id with force=true (a deployment can be the
    current alias target for that branch, which DELETE refuses without it).
    Returns how many were actually deleted."""
    resp = httpx.get(
        f"{API_BASE}/accounts/{settings.cloudflare_account_id}/pages/projects/{project_name}/deployments",
        headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
        params={"per_page": 50},
        timeout=15,
    )
    resp.raise_for_status()
    deployments = resp.json().get("result", [])
    matching = [d for d in deployments if d.get("deployment_trigger", {}).get("metadata", {}).get("branch") == branch]

    deleted = 0
    for deployment in matching:
        delete_resp = httpx.delete(
            f"{API_BASE}/accounts/{settings.cloudflare_account_id}/pages/projects/{project_name}/deployments/{deployment['id']}",
            headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
            params={"force": "true"},
            timeout=15,
        )
        delete_resp.raise_for_status()
        if delete_resp.json().get("success"):
            deleted += 1
    return deleted

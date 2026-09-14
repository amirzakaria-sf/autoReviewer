"""GitHub "connection" for the dashboard's profile + repo-picker.

Not OAuth: WhipGuard already holds a server-side PAT (plan.md §6.1 names this
as the Tier-0 shortcut for a GitHub App). Building a second, real OAuth flow
just to re-derive an identity this token already has access to would be
redundant ceremony for a single-operator demo -- this exposes that existing
access as a "Connect to GitHub" / profile / repo-picker UI instead of hiding
it behind a raw env var, which is what made the dashboard feel incomplete.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.models import Repo

router = APIRouter(prefix="/api/github")

API_BASE = "https://api.github.com"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.github_token}", "Accept": "application/vnd.github+json"}


@router.get("/profile")
async def profile():
    if not settings.github_token:
        return {"connected": False}
    resp = httpx.get(f"{API_BASE}/user", headers=_headers(), timeout=15)
    if resp.status_code != 200:
        return {"connected": False}
    data = resp.json()
    return {
        "connected": True,
        "login": data.get("login"),
        "name": data.get("name"),
        "avatar_url": data.get("avatar_url"),
        "html_url": data.get("html_url"),
    }


@router.get("/repos")
async def list_repos():
    if not settings.github_token:
        raise HTTPException(400, "no GitHub token configured")
    resp = httpx.get(
        f"{API_BASE}/user/repos", headers=_headers(), params={"per_page": 50, "sort": "updated"}, timeout=15
    )
    resp.raise_for_status()
    async with async_session() as db:
        connected = {r.github_full_name for r in (await db.execute(select(Repo))).scalars().all()}
    return [
        {
            "full_name": item["full_name"],
            "private": item["private"],
            "default_branch": item["default_branch"],
            "html_url": item["html_url"],
            "connected": item["full_name"] in connected,
        }
        for item in resp.json()
    ]


@router.post("/connect")
async def connect_repo(body: dict):
    full_name = body.get("full_name")
    if not full_name:
        raise HTTPException(400, "full_name is required")

    async with async_session() as db:
        existing = (
            await db.execute(select(Repo).where(Repo.github_full_name == full_name))
        ).scalars().first()
        if existing:
            return {"ok": True, "repo_id": str(existing.id), "already_connected": True}

        repo = Repo(github_full_name=full_name)
        db.add(repo)
        await db.commit()
        return {"ok": True, "repo_id": str(repo.id)}

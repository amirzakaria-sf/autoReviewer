"""GitHub "connection" for the dashboard's profile + repo-picker, plus the
real OAuth App authorization-code flow that produces the server's working
token.

The registered app ("Whip") is a GitHub OAuth App, not a GitHub App -- that
means there is no private key/JWT server-to-server identity available, only
the standard three-legged OAuth exchange: send the operator to GitHub's
consent screen, GitHub redirects back here with a one-time `code`, this
server exchanges it for an access token using github_client_id +
github_client_secret. That token becomes settings.github_token -- every
existing github_client.py call site (issue/PR creation, push) reads that
same field dynamically, so nothing else needed to change.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.deps import current_user_id
from app.models import Repo
from app.security import sign_state, verify_state

router = APIRouter(prefix="/api/github")

API_BASE = "https://api.github.com"
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _persist_github_token(token: str) -> None:
    """Writes the freshly-exchanged token into settings (for this running
    process) AND back into .env on disk (so a container restart doesn't lose
    it and fall back to the empty default -- mirrors exactly what pasting a
    manually-generated PAT into .env did before this flow existed)."""
    settings.github_token = token
    if not _ENV_PATH.exists():
        return
    text = _ENV_PATH.read_text()
    if re.search(r"^GITHUB_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^GITHUB_TOKEN=.*$", f"GITHUB_TOKEN={token}", text, flags=re.MULTILINE)
    else:
        text += f"\nGITHUB_TOKEN={token}\n"
    _ENV_PATH.write_text(text)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.github_token}", "Accept": "application/vnd.github+json"}


@router.get("/oauth/start")
async def oauth_start(request: Request):
    if not settings.github_client_id:
        raise HTTPException(400, "GITHUB_CLIENT_ID is not configured")
    state = sign_state("github_oauth")
    params = httpx.QueryParams(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": "repo",
            "state": state,
        }
    )
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")


@router.get("/oauth/callback")
async def oauth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"/connect?github_error={error}")
    if not state or not verify_state(state, "github_oauth"):
        return RedirectResponse("/connect?github_error=state_mismatch")
    if not code:
        return RedirectResponse("/connect?github_error=missing_code")

    resp = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
            "redirect_uri": settings.github_oauth_redirect_uri,
        },
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        return RedirectResponse(f"/connect?github_error={payload.get('error', 'exchange_failed')}")

    _persist_github_token(token)
    return RedirectResponse("/connect?github_connected=1")


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
async def connect_repo(body: dict, request: Request):
    full_name = body.get("full_name")
    if not full_name:
        raise HTTPException(400, "full_name is required")

    async with async_session() as db:
        existing = (
            await db.execute(select(Repo).where(Repo.github_full_name == full_name))
        ).scalars().first()
        if existing:
            return {"ok": True, "repo_id": str(existing.id), "already_connected": True}

        repo = Repo(github_full_name=full_name, owner_user_id=current_user_id(request))
        db.add(repo)
        await db.commit()
        return {"ok": True, "repo_id": str(repo.id)}

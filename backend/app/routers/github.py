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

import uuid

import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.deps import current_user_id, require_admin, visible_repo_ids
from app.models import Repo, User
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


@router.post("/disconnect")
async def disconnect(_: User = Depends(require_admin)):
    """Clears the server-wide GitHub token (OAuth token or App identity --
    whichever _headers() would have used).

    Platform admin only, because the credential is deployment-wide: any member
    of any organisation could otherwise stop every other organisation's pushes,
    PR comments and branch cleanups with one click. That is not a data leak,
    which is why the tenancy pass did not catch it -- it is the availability
    half of the same boundary.
    """
    _persist_github_token("")
    return {"ok": True}


@router.get("/repos")
async def list_repos(repo_ids: list[uuid.UUID] = Depends(visible_repo_ids)):
    if not settings.github_token:
        raise HTTPException(400, "no GitHub token configured")
    resp = httpx.get(
        f"{API_BASE}/user/repos", headers=_headers(), params={"per_page": 50, "sort": "updated"}, timeout=15
    )
    resp.raise_for_status()
    async with async_session() as db:
        # Scoped: unscoped, this marked a repository "connected" because SOME
        # organisation in the deployment had connected it, which tells the
        # caller something about an organisation they cannot otherwise see.
        # Narrow -- the repository list itself comes from the caller's own
        # GitHub token -- but it is the same boundary, so it reads the same way.
        connected = {
            r.github_full_name
            for r in (await db.execute(select(Repo).where(Repo.id.in_(repo_ids)))).scalars().all()
        } if repo_ids else set()
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
    """Connect a repository to the caller's organization.

    `repos.org_id` is the one column carrying tenancy -- issues, fixes, traces
    and chunks all reach their org through their repo -- and nothing set it
    until now, so every repository connected through the product was orphaned
    from the org that owned it. Assignment routing, per-org Slack and the
    on-disk workspace layout all read it.

    Taken from the connector rather than asked for: one person belongs to one
    organization today, so there is nothing to choose between.
    """
    import asyncio

    from app import orgs

    full_name = body.get("full_name")
    if not full_name:
        raise HTTPException(400, "full_name is required")

    user_id = current_user_id(request)
    memberships = await asyncio.to_thread(orgs.orgs_for_user, user_id)
    if not memberships:
        raise HTTPException(
            409,
            "You are not in an organization yet, and a repository has to belong to one. "
            "Ask a system admin to add you to one first.",
        )
    org_id = body.get("org_id") or memberships[0]["id"]
    if not any(m["id"] == str(org_id) or m["id"] == org_id for m in memberships):
        # Allow the UUID object/string mismatch.
        allowed = {str(m["id"]) for m in memberships}
        if str(org_id) not in allowed:
            raise HTTPException(403, "You are not a member of that organization.")
    org_id = str(org_id)

    async with async_session() as db:
        existing = (
            await db.execute(select(Repo).where(Repo.github_full_name == full_name))
        ).scalars().first()
        if existing:
            # An older repo connected before tenancy was wired has no org at
            # all. Adopt it rather than leaving it permanently invisible to
            # every org-scoped query.
            if existing.org_id is None:
                await asyncio.to_thread(orgs.assign_repo, existing.id, org_id)
            elif str(existing.org_id) != org_id:
                # Another organisation owns it. Never reassign -- that would
                # hand them this organisation's issues, fixes and findings --
                # and never return its id either.
                #
                # This does disclose that the repository is connected somewhere
                # in the deployment. `repos.github_full_name` is UNIQUE, so the
                # alternative is an integrity error the caller cannot act on,
                # and the caller already has GitHub access to the repository in
                # question. A named error they can take to an admin is the
                # better of the two.
                raise HTTPException(
                    409,
                    "That repository is already connected to a different organization. "
                    "A platform admin has to move it before you can connect it here.",
                )
            return {"ok": True, "repo_id": str(existing.id), "already_connected": True}

        repo = Repo(github_full_name=full_name, owner_user_id=user_id, org_id=uuid.UUID(org_id))
        db.add(repo)
        await db.commit()
        return {"ok": True, "repo_id": str(repo.id)}

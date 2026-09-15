"""Per-repo Slack channel selection via Slack's own OAuth consent screen --
requesting the `incoming-webhook` scope makes Slack show a native channel
picker, so nobody ever hand-types a channel ID (plan.md §7.1's "notification
routing: Slack channel" setting, done the same way GitHub's own OAuth
connect works in routers/github.py).

This does NOT create a second bot identity or a per-repo token: WhipGuard
still sends every message with its one existing workspace bot token
(settings.slack_bot_token, kept in sync below since a reinstall CAN rotate
it). The OAuth round-trip here is purely a UI mechanism for picking a
channel -- Repo.slack_channel_id is the only thing that actually varies per
repo.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.models import Repo
from app.security import sign_state, verify_state

router = APIRouter(prefix="/api/slack")

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _persist_slack_token(token: str) -> None:
    """Mirrors github.py's _persist_github_token: a Slack app reinstall (or
    a fresh scope grant, like incoming-webhook here) can hand back a NEW bot
    token for the same workspace install -- if we kept using the old static
    .env value, every future chat.postMessage call would start failing with
    invalid_auth the moment that happens."""
    settings.slack_bot_token = token
    if not _ENV_PATH.exists():
        return
    text = _ENV_PATH.read_text()
    if re.search(r"^SLACK_BOT_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^SLACK_BOT_TOKEN=.*$", f"SLACK_BOT_TOKEN={token}", text, flags=re.MULTILINE)
    else:
        text += f"\nSLACK_BOT_TOKEN={token}\n"
    _ENV_PATH.write_text(text)


@router.get("/oauth/start")
async def oauth_start(repo_id: uuid.UUID):
    if not settings.slack_client_id:
        raise HTTPException(400, "SLACK_CLIENT_ID is not configured")

    async with async_session() as db:
        repo = await db.get(Repo, repo_id)
        if not repo:
            raise HTTPException(404, "repo not found")

    state = sign_state("slack_oauth", extra={"repo_id": str(repo_id)})
    params = httpx.QueryParams(
        {
            # chat:write(.public) are already granted on this app's existing
            # install; requesting them again alongside incoming-webhook is
            # what makes Slack treat this as "add a scope" rather than
            # silently dropping the ones already held.
            "scope": "chat:write,chat:write.public,incoming-webhook",
            "client_id": settings.slack_client_id,
            "redirect_uri": settings.slack_oauth_redirect_uri,
            "state": state,
        }
    )
    return RedirectResponse(f"https://slack.com/oauth/v2/authorize?{params}")


@router.get("/oauth/callback")
async def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"/connect?slack_error={error}")

    decoded_state = verify_state(state, "slack_oauth") if state else None
    if not decoded_state:
        return RedirectResponse("/connect?slack_error=state_mismatch")
    if not code:
        return RedirectResponse("/connect?slack_error=missing_code")

    repo_id = decoded_state.get("repo_id")

    resp = httpx.post(
        "https://slack.com/api/oauth.v2.access",
        data={
            "client_id": settings.slack_client_id,
            "client_secret": settings.slack_client_secret,
            "code": code,
            "redirect_uri": settings.slack_oauth_redirect_uri,
        },
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()

    if not payload.get("ok"):
        return RedirectResponse(f"/connect?slack_error={payload.get('error', 'exchange_failed')}")

    webhook = payload.get("incoming_webhook") or {}
    channel_id = webhook.get("channel_id")
    channel_name = webhook.get("channel")
    access_token = payload.get("access_token")

    if not channel_id:
        return RedirectResponse("/connect?slack_error=no_channel_selected")

    if access_token:
        _persist_slack_token(access_token)

    async with async_session() as db:
        repo = await db.get(Repo, uuid.UUID(repo_id)) if repo_id else None
        if repo:
            repo.slack_channel_id = channel_id
            repo.slack_channel_name = channel_name
            await db.commit()
            return RedirectResponse(f"/repos/{repo.id}/settings?slack_connected=1")

    return RedirectResponse("/connect?slack_error=repo_not_found")


@router.post("/disconnect")
async def disconnect(repo_id: uuid.UUID):
    """Clears just this repo's channel selection -- the workspace bot token
    itself stays put (it isn't per-repo, and other repos may still use it)."""
    async with async_session() as db:
        repo = await db.get(Repo, repo_id)
        if not repo:
            raise HTTPException(404, "repo not found")
        repo.slack_channel_id = None
        repo.slack_channel_name = None
        await db.commit()
    return {"ok": True}

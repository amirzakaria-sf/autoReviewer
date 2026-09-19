"""One Slack connection for the whole account.

ADMIN-ONLY, because this is not a personal preference: the channel chosen
here is where every user's approval requests land. Left open to any signed-in
member, one member could quietly redirect the whole team's notifications to a
channel only they watch, or disconnect Slack entirely -- and since every
failure in this integration is silent, nobody would notice until an approval
request went missing.

Connect once, pick one channel, and every repo's notifications go there.
This replaced a per-repo design where each repository had its own channel:
that made the user repeat an OAuth round-trip for every repo they connected,
stored a channel on each `Repo` row, and delivered no benefit -- the
approvals all land in the same place anyway, and a team watching one channel
is the normal case rather than the exception.

The `incoming-webhook` scope is requested purely because it makes Slack show
its own native channel-picker on the consent screen, so nobody has to
hand-type a channel ID. WhipGuard does not use the webhook URL: every
message is sent with the workspace bot token via `chat.postMessage`, which
is what lets it later UPDATE a message in place (the approve/reject buttons
change state after a decision).

The chosen channel and the bot token are stored in the database
(app/app_settings.py), not in `.env` -- the process that handles this
callback is not the process that sends the messages.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app import app_settings
from app.config import settings
from app.deps import require_admin
from app.models import User
from app.security import sign_state, verify_state

router = APIRouter(prefix="/api/slack")
logger = logging.getLogger("whipguard.slack_connect")

# chat:write(.public) are what actually sends and updates messages;
# incoming-webhook is requested only for its channel-picker consent screen.
_SCOPES = "chat:write,chat:write.public,incoming-webhook"


@router.get("/oauth/start")
async def oauth_start(admin: User = Depends(require_admin)):
    if not settings.slack_client_id:
        raise HTTPException(400, "SLACK_CLIENT_ID is not configured")

    params = httpx.QueryParams(
        {
            "scope": _SCOPES,
            "client_id": settings.slack_client_id,
            "redirect_uri": settings.slack_oauth_redirect_uri,
            "state": sign_state("slack_oauth"),
        }
    )
    return RedirectResponse(f"https://slack.com/oauth/v2/authorize?{params}")


@router.get("/oauth/callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    admin: User = Depends(require_admin),
):
    if error:
        return RedirectResponse(f"/profile?slack_error={error}")
    if not state or not verify_state(state, "slack_oauth"):
        return RedirectResponse("/profile?slack_error=state_mismatch")
    if not code:
        return RedirectResponse("/profile?slack_error=missing_code")

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
        return RedirectResponse(f"/profile?slack_error={payload.get('error', 'exchange_failed')}")

    webhook = payload.get("incoming_webhook") or {}
    channel_id = webhook.get("channel_id")
    if not channel_id:
        return RedirectResponse("/profile?slack_error=no_channel_selected")

    team = payload.get("team") or {}
    channel_name = (webhook.get("channel") or "").lstrip("#")

    # Recorded per ORGANIZATION and keyed by Slack's team_id. That key is what
    # makes an inbound button click resolvable later: the interaction payload
    # carries the team, which identifies both the install and the token to
    # answer with. Without it a second workspace connecting would overwrite
    # the first and silently redirect their approvals.
    if team.get("id") and payload.get("access_token"):
        from app.orgs import orgs_for_user

        memberships = await asyncio.to_thread(orgs_for_user, admin.id)
        if memberships:
            await asyncio.to_thread(
                app_settings.save_installation,
                org_id=memberships[0]["id"],
                team_id=team["id"],
                team_name=team.get("name", ""),
                bot_token=payload["access_token"],
                channel_id=channel_id,
                channel_name=channel_name,
                installed_by=admin.id,
            )

    # The single-workspace settings stay in step so a deployment mid-migration
    # keeps working from either path.
    app_settings.set_setting(app_settings.SLACK_CHANNEL_ID, channel_id)
    app_settings.set_setting(app_settings.SLACK_CHANNEL_NAME, channel_name)
    if payload.get("access_token"):
        app_settings.set_setting(app_settings.SLACK_BOT_TOKEN, payload["access_token"])

    logger.info("slack connected: team=%s channel=%s", team.get("name"), webhook.get("channel"))
    return RedirectResponse("/profile?slack_connected=1")


@router.get("/status")
async def slack_status():
    return app_settings.slack_status()


@router.post("/disconnect")
async def disconnect(admin: User = Depends(require_admin)):
    """Clears the stored channel and token. The Slack app itself stays
    installed in the workspace -- removing it is done from Slack, not from
    here, and doing it silently on the user's behalf would be surprising."""
    app_settings.disconnect_slack()
    return {"ok": True}


@router.post("/test")
async def send_test_message(admin: User = Depends(require_admin)):
    """Posts a real message to the connected channel.

    Exists because every failure mode in this integration is SILENT: a
    missing channel, a revoked token and a bot that was never invited all
    end with `post_message` being skipped or refused, and nothing in the
    dashboard distinguishes them from "no bugs found yet". This turns that
    into an answer.
    """
    status = app_settings.slack_status()
    if not status["has_token"]:
        raise HTTPException(400, "No Slack bot token -- connect Slack first.")
    if not status["channel_id"]:
        raise HTTPException(400, "No Slack channel selected -- connect Slack first.")

    from app.integrations import slack_client

    try:
        slack_client.post_message(
            status["channel_id"],
            blocks=[],
            text="WhipGuard is connected. Approval requests for every connected repo will arrive here.",
        )
    except Exception as error:  # noqa: BLE001 - the whole point is to surface it
        raise HTTPException(502, f"Slack rejected the message: {error}") from None

    return {"ok": True, "channel": status["channel_name"] or status["channel_id"]}

"""Slack client: Block Kit messages, signature-verified (plan.md §6.3, §13).

An unverified webhook accepting "approved" from anyone who can guess a URL would
make the whole approval gate decorative — verify_signature is called before any
interaction payload is trusted, in the webhook route, not left as an assumption.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx

from app.config import settings

API_BASE = "https://slack.com/api"
MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5


def post_message(channel: str, blocks: list[dict], text: str) -> str:
    resp = httpx.post(
        f"{API_BASE}/chat.postMessage",
        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        json={"channel": channel, "blocks": blocks, "text": text},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack post_message failed: {data.get('error')}")
    return data["ts"]


def update_message(channel: str, ts: str, blocks: list[dict], text: str) -> None:
    resp = httpx.post(
        f"{API_BASE}/chat.update",
        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        json={"channel": channel, "ts": ts, "blocks": blocks, "text": text},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack update_message failed: {data.get('error')}")


def get_message_text(channel: str, ts: str) -> str | None:
    """Used by the outcome checker to read back the thread's current state."""
    resp = httpx.get(
        f"{API_BASE}/conversations.history",
        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        params={"channel": channel, "latest": ts, "inclusive": "true", "limit": 1},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    messages = data.get("messages", [])
    return messages[0]["text"] if messages else None


def verify_signature(headers: dict, body: str, signing_secret: str) -> bool:
    timestamp = headers.get("X-Slack-Request-Timestamp")
    signature = headers.get("X-Slack-Signature")
    if not timestamp or not signature:
        return False

    if abs(time.time() - float(timestamp)) > MAX_TIMESTAMP_SKEW_SECONDS:
        return False

    basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), basestring.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed, signature)

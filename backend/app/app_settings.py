"""Account-level settings both processes read, with env-var fallback.

Slack is configured ONCE for the account: one workspace connection, one
channel, and every repo's notifications go there. That was a deliberate
simplification of an earlier per-repo design -- connecting a channel for
each repo separately is work nobody wants to repeat, and the approvals all
land in the same place anyway.

Sync and async variants exist because the callers genuinely differ: the
OAuth routes are async FastAPI handlers, while the council graph nodes that
actually post to Slack are sync functions running in worker threads.
"""

from __future__ import annotations

import logging

import psycopg

from app.config import settings
from app.retrieval import _sync_dsn

logger = logging.getLogger("whipguard.app_settings")

SLACK_CHANNEL_ID = "slack_channel_id"
SLACK_CHANNEL_NAME = "slack_channel_name"
SLACK_BOT_TOKEN = "slack_bot_token"


def get_setting(key: str, default: str = "") -> str:
    """Sync read. Returns `default` on any failure -- a settings lookup must
    never take down a council run."""
    try:
        with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
            row = cur.fetchone()
        return (row[0] if row else "") or default
    except Exception as error:  # noqa: BLE001
        logger.warning("could not read setting %s: %s", key, error)
        return default


def set_setting(key: str, value: str) -> None:
    try:
        with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (key, value),
            )
            conn.commit()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not write setting %s: %s", key, error)


def slack_bot_token() -> str:
    """The stored token wins over the .env one: a reinstall or a fresh scope
    grant hands back a NEW token for the same workspace, and continuing to
    use the old static value would fail every post with invalid_auth."""
    return get_setting(SLACK_BOT_TOKEN) or settings.slack_bot_token


def slack_channel_id() -> str:
    """The single channel every repo's notifications go to.

    Falls back to the SLACK_CHANNEL_ID env var so an existing deployment that
    set one by hand keeps working without reconnecting.
    """
    return get_setting(SLACK_CHANNEL_ID) or settings.slack_channel_id


def slack_is_connected() -> bool:
    return bool(slack_bot_token() and slack_channel_id())


def slack_status() -> dict:
    """What the UI shows: connected or not, and to which channel."""
    return {
        "connected": slack_is_connected(),
        "channel_id": slack_channel_id(),
        "channel_name": get_setting(SLACK_CHANNEL_NAME),
        # Distinguishes "never set up" from "set up but the channel is gone",
        # which otherwise look identical in the UI.
        "has_token": bool(slack_bot_token()),
    }


def disconnect_slack() -> None:
    for key in (SLACK_CHANNEL_ID, SLACK_CHANNEL_NAME, SLACK_BOT_TOKEN):
        set_setting(key, "")

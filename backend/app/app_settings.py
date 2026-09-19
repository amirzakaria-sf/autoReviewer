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

from app import sync_db
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
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
            row = cur.fetchone()
        return (row[0] if row else "") or default
    except Exception as error:  # noqa: BLE001
        logger.warning("could not read setting %s: %s", key, error)
        return default


def set_setting(key: str, value: str) -> None:
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
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


# --- per-organization Slack installs -----------------------------------------
#
# One Slack app, installed separately into each customer's workspace. The
# install is keyed by Slack's own `team_id`, which is what makes an inbound
# button click resolvable: the interaction payload carries the team, the team
# identifies the install, and the install identifies both the org and the
# token to answer with. Without that key there is no way to tell whose
# workspace a click came from.


def save_installation(
    *, org_id, team_id: str, team_name: str, bot_token: str, channel_id: str, channel_name: str, installed_by=None
) -> None:
    """Record or refresh one workspace install.

    Conflicts on `team_id` rather than on org: a workspace reinstalling
    issues a NEW bot token for the same team, and keeping the old row would
    leave every later post failing with invalid_auth.
    """
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO slack_installations
                    (id, org_id, team_id, team_name, bot_token, channel_id, channel_name,
                     installed_by, installed_at, revoked_at)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, now(), NULL)
                ON CONFLICT ON CONSTRAINT uq_slack_install DO UPDATE SET
                    org_id = EXCLUDED.org_id,
                    team_name = EXCLUDED.team_name,
                    bot_token = EXCLUDED.bot_token,
                    channel_id = EXCLUDED.channel_id,
                    channel_name = EXCLUDED.channel_name,
                    installed_at = now(),
                    revoked_at = NULL
                """,
                (
                    str(org_id), team_id, team_name, bot_token, channel_id, channel_name,
                    str(installed_by) if installed_by else None,
                ),
            )
            conn.commit()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not save slack installation for team %s: %s", team_id, error)


def installation_for_org(org_id) -> dict | None:
    if not org_id:
        return None
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT team_id, team_name, bot_token, channel_id, channel_name "
                "FROM slack_installations WHERE org_id = %s AND revoked_at IS NULL "
                "ORDER BY installed_at DESC LIMIT 1",
                (str(org_id),),
            )
            row = cur.fetchone()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not read slack installation for org %s: %s", org_id, error)
        return None
    if not row:
        return None
    return {
        "team_id": row[0], "team_name": row[1], "bot_token": row[2],
        "channel_id": row[3], "channel_name": row[4],
    }


def installation_for_team(team_id: str) -> dict | None:
    """Resolve an inbound interaction to the workspace that sent it."""
    if not team_id:
        return None
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT org_id, team_name, bot_token, channel_id, channel_name "
                "FROM slack_installations WHERE team_id = %s AND revoked_at IS NULL",
                (team_id,),
            )
            row = cur.fetchone()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not resolve slack team %s: %s", team_id, error)
        return None
    if not row:
        return None
    return {
        "org_id": str(row[0]), "team_name": row[1], "bot_token": row[2],
        "channel_id": row[3], "channel_name": row[4],
    }


def revoke_installation(team_id: str) -> None:
    """Handle Slack's `app_uninstalled` event.

    Without this a revoked install keeps failing every post, silently --
    which is indistinguishable from "no bugs found" in a dashboard that only
    shows successes.
    """
    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE slack_installations SET revoked_at = now() WHERE team_id = %s AND revoked_at IS NULL",
                (team_id,),
            )
            conn.commit()
        logger.info("slack installation revoked for team %s", team_id)
    except Exception as error:  # noqa: BLE001
        logger.warning("could not revoke slack team %s: %s", team_id, error)


def slack_for_repo(repo_id) -> tuple[str, str]:
    """`(bot_token, channel_id)` for whichever org owns this repo.

    Falls back to the single-workspace configuration so a deployment that
    never migrated keeps working untouched -- the same reason the env var is
    still honoured below it.
    """
    try:
        from app.orgs import org_for_repo

        install = installation_for_org(org_for_repo(repo_id))
        if install and install["bot_token"] and install["channel_id"]:
            return install["bot_token"], install["channel_id"]
    except Exception as error:  # noqa: BLE001
        logger.warning("per-org slack lookup failed for repo %s: %s", repo_id, error)
    return slack_bot_token(), slack_channel_id()

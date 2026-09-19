"""Slack is configured once for the account: one workspace, one channel,
every repo. These assert the resolution rules, because every failure mode in
this integration is otherwise SILENT -- a missing channel just means
post_message is skipped, which looks identical to "no bugs found".
"""

from __future__ import annotations

import pytest

from app import app_settings
from app.config import settings


@pytest.fixture(autouse=True)
def _clean_settings():
    def wipe():
        for key in (app_settings.SLACK_CHANNEL_ID, app_settings.SLACK_CHANNEL_NAME, app_settings.SLACK_BOT_TOKEN):
            app_settings.set_setting(key, "")

    wipe()
    yield
    wipe()


def test_a_stored_channel_is_used():
    app_settings.set_setting(app_settings.SLACK_CHANNEL_ID, "C_STORED")
    assert app_settings.slack_channel_id() == "C_STORED"


def test_the_env_var_still_works_for_a_deployment_that_never_reconnected():
    """An existing install that set SLACK_CHANNEL_ID by hand must keep
    working rather than going silent the moment this shipped."""
    original = settings.slack_channel_id
    settings.slack_channel_id = "C_FROM_ENV"
    try:
        assert app_settings.slack_channel_id() == "C_FROM_ENV"
    finally:
        settings.slack_channel_id = original


def test_a_stored_token_beats_the_env_one():
    """A reinstall hands back a NEW token for the same workspace; continuing
    to use the .env value would fail every post with invalid_auth."""
    original = settings.slack_bot_token
    settings.slack_bot_token = "xoxb-stale-from-env"
    app_settings.set_setting(app_settings.SLACK_BOT_TOKEN, "xoxb-fresh-from-oauth")
    try:
        assert app_settings.slack_bot_token() == "xoxb-fresh-from-oauth"
    finally:
        settings.slack_bot_token = original


def test_a_token_without_a_channel_is_not_connected():
    """The distinction that used to be invisible: the app was installed, so
    everything looked fine, but no channel meant every message was silently
    dropped."""
    original = settings.slack_channel_id
    settings.slack_channel_id = ""
    app_settings.set_setting(app_settings.SLACK_BOT_TOKEN, "xoxb-token")
    try:
        status = app_settings.slack_status()
        assert status["has_token"] is True
        assert status["connected"] is False
    finally:
        settings.slack_channel_id = original


def test_disconnect_clears_everything():
    app_settings.set_setting(app_settings.SLACK_CHANNEL_ID, "C1")
    app_settings.set_setting(app_settings.SLACK_BOT_TOKEN, "xoxb-1")
    app_settings.disconnect_slack()
    original_channel, original_token = settings.slack_channel_id, settings.slack_bot_token
    settings.slack_channel_id, settings.slack_bot_token = "", ""
    try:
        assert app_settings.slack_is_connected() is False
    finally:
        settings.slack_channel_id, settings.slack_bot_token = original_channel, original_token


# --- multi-workspace installs -------------------------------------------------

import uuid as _uuid

import psycopg

from app.retrieval import _sync_dsn


def _sql(query: str, params: tuple | None = None) -> list:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        # `params=None`, not an empty tuple: psycopg only scans for
        # placeholders when parameters are supplied, and a bare LIKE 'x%'
        # otherwise fails as a malformed placeholder.
        cur.execute(query, params) if params else cur.execute(query)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


@pytest.fixture
def two_orgs():
    """Two customers, two Slack workspaces. The case the single-token design
    could not express at all."""
    _sql("DELETE FROM slack_installations WHERE team_id LIKE 'T_PYTEST%'")
    _sql("DELETE FROM organizations WHERE slug LIKE 'pytest-slack-%'")
    made = []
    for index in ("a", "b"):
        org_id = _uuid.uuid4()
        _sql(
            "INSERT INTO organizations (id, name, slug, created_at) VALUES (%s, %s, %s, now())",
            (str(org_id), f"Org {index.upper()}", f"pytest-slack-{index}"),
        )
        made.append(str(org_id))
    yield made
    _sql("DELETE FROM slack_installations WHERE team_id LIKE 'T_PYTEST%'")
    _sql("DELETE FROM organizations WHERE slug LIKE 'pytest-slack-%'")


def test_two_workspaces_keep_their_own_tokens_and_channels(two_orgs):
    """Before this, a second org connecting overwrote the first org's token
    and silently redirected their approval requests."""
    org_a, org_b = two_orgs
    app_settings.save_installation(
        org_id=org_a, team_id="T_PYTEST_A", team_name="Alpha",
        bot_token="xoxb-alpha", channel_id="C_ALPHA", channel_name="alerts",
    )
    app_settings.save_installation(
        org_id=org_b, team_id="T_PYTEST_B", team_name="Beta",
        bot_token="xoxb-beta", channel_id="C_BETA", channel_name="bugs",
    )

    assert app_settings.installation_for_org(org_a)["bot_token"] == "xoxb-alpha"
    assert app_settings.installation_for_org(org_b)["channel_id"] == "C_BETA"


def test_an_inbound_click_resolves_to_the_workspace_that_sent_it(two_orgs):
    """`team_id` on the interaction payload is the ONLY thing identifying
    whose workspace a button click came from."""
    org_a, _ = two_orgs
    app_settings.save_installation(
        org_id=org_a, team_id="T_PYTEST_A", team_name="Alpha",
        bot_token="xoxb-alpha", channel_id="C_ALPHA", channel_name="alerts",
    )

    resolved = app_settings.installation_for_team("T_PYTEST_A")
    assert resolved["org_id"] == org_a
    assert resolved["bot_token"] == "xoxb-alpha"
    assert app_settings.installation_for_team("T_PYTEST_UNKNOWN") is None


def test_reinstalling_replaces_the_token_rather_than_duplicating_the_row(two_orgs):
    """A workspace reinstalling issues a NEW bot token for the same team.
    Keeping the old row would fail every later post with invalid_auth."""
    org_a, _ = two_orgs
    for token in ("xoxb-first", "xoxb-second"):
        app_settings.save_installation(
            org_id=org_a, team_id="T_PYTEST_A", team_name="Alpha",
            bot_token=token, channel_id="C_ALPHA", channel_name="alerts",
        )

    rows = _sql("SELECT bot_token FROM slack_installations WHERE team_id = 'T_PYTEST_A'")
    assert len(rows) == 1
    assert rows[0][0] == "xoxb-second"


def test_an_uninstalled_workspace_stops_resolving(two_orgs):
    """Slack's app_uninstalled event. Without handling it, a revoked install
    keeps failing every post silently."""
    org_a, _ = two_orgs
    app_settings.save_installation(
        org_id=org_a, team_id="T_PYTEST_A", team_name="Alpha",
        bot_token="xoxb-alpha", channel_id="C_ALPHA", channel_name="alerts",
    )
    app_settings.revoke_installation("T_PYTEST_A")

    assert app_settings.installation_for_team("T_PYTEST_A") is None
    assert app_settings.installation_for_org(org_a) is None

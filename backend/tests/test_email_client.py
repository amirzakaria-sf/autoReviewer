"""Unit tests for the magic-link token contract (plan.md §6.4): a valid
token round-trips, a tampered signature is rejected, an expired token is
rejected, and a malformed token never raises."""

from __future__ import annotations

import time
from unittest.mock import patch

from app.config import settings
from app.integrations.email_client import sign_action_token, verify_action_token


def test_valid_token_round_trips():
    with patch.object(settings, "session_secret", "test-secret"):
        token = sign_action_token("11111111-1111-1111-1111-111111111111", "approve")
        decoded = verify_action_token(token)

    assert decoded == {"fix_id": "11111111-1111-1111-1111-111111111111", "action": "approve"}


def test_tampered_signature_is_rejected():
    with patch.object(settings, "session_secret", "test-secret"):
        token = sign_action_token("11111111-1111-1111-1111-111111111111", "reject")
        payload_b64, _, signature = token.partition(".")
        tampered = f"{payload_b64}.{'0' * len(signature)}"

        assert verify_action_token(tampered) is None


def test_expired_token_is_rejected():
    with patch.object(settings, "session_secret", "test-secret"), patch(
        "app.integrations.email_client.ACTION_TOKEN_TTL_SECONDS", -1
    ):
        token = sign_action_token("11111111-1111-1111-1111-111111111111", "approve")

    with patch.object(settings, "session_secret", "test-secret"):
        assert verify_action_token(token) is None


def test_malformed_token_never_raises():
    assert verify_action_token("not-a-real-token") is None
    assert verify_action_token("") is None
    assert verify_action_token("a.b.c") is None


def test_signed_with_different_secret_is_rejected():
    """A token signed under a stale/rotated session_secret must not verify --
    otherwise rotating settings.session_secret (which also signs the login
    session cookie) would silently fail to invalidate outstanding email
    action links."""
    with patch.object(settings, "session_secret", "secret-a"):
        token = sign_action_token("11111111-1111-1111-1111-111111111111", "approve")

    with patch.object(settings, "session_secret", "secret-b"):
        assert verify_action_token(token) is None

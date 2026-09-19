"""Unit tests for the GitHub App JWT -> installation-token exchange
(app/integrations/github_app_auth.py). Uses a throwaway RSA key generated
in-process -- proves the actual RS256 signing/verification and the
mint-then-cache-then-refresh logic work, without needing a real GitHub App."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.integrations import github_app_auth


def _throwaway_private_key_pem() -> tuple[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, key.public_key()


def _reset_cache():
    github_app_auth._cached_token = None
    github_app_auth._cached_expires_at = 0.0
    github_app_auth._cached.clear()
    github_app_auth._discovered_id = None


def test_github_app_configured_requires_both_id_and_key():
    with patch.object(settings, "github_app_id", ""), patch.object(settings, "github_app_private_key", ""):
        assert github_app_auth.github_app_configured() is False
    with patch.object(settings, "github_app_id", "12345"), patch.object(settings, "github_app_private_key", ""):
        assert github_app_auth.github_app_configured() is False
    with patch.object(settings, "github_app_id", "12345"), patch.object(settings, "github_app_private_key", "pem"):
        assert github_app_auth.github_app_configured() is True


def test_app_jwt_is_correctly_signed_and_claims_the_app_id():
    pem, public_key = _throwaway_private_key_pem()
    with patch.object(settings, "github_app_id", "999888"), patch.object(settings, "github_app_private_key", pem):
        token = github_app_auth._app_jwt()

    decoded = jwt.decode(token, public_key, algorithms=["RS256"])
    assert decoded["iss"] == "999888"
    assert decoded["exp"] - decoded["iat"] <= 10 * 60  # GitHub's own hard ceiling


def test_get_installation_token_discovers_installation_and_caches_result():
    pem, _ = _throwaway_private_key_pem()
    _reset_cache()

    mock_installations_resp = MagicMock()
    mock_installations_resp.json.return_value = [{"id": 42}]
    mock_installations_resp.raise_for_status.return_value = None

    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {
        "token": "ghs_faketoken123",
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
    }
    mock_token_resp.raise_for_status.return_value = None

    with (
        patch.object(settings, "github_app_id", "999888"),
        patch.object(settings, "github_app_private_key", pem),
        patch.object(settings, "github_app_installation_id", ""),
        patch("app.integrations.github_app_auth.httpx.get", return_value=mock_installations_resp) as mock_get,
        patch("app.integrations.github_app_auth.httpx.post", return_value=mock_token_resp) as mock_post,
    ):
        token = github_app_auth.get_installation_token()
        assert token == "ghs_faketoken123"
        mock_get.assert_called_once()  # installation auto-discovery
        assert mock_post.call_args.args[0] == "https://api.github.com/app/installations/42/access_tokens"
        assert mock_post.call_args.kwargs["headers"]["Authorization"].startswith("Bearer ")

        # Second call within the token's lifetime must NOT re-mint -- no new
        # HTTP calls at all.
        token_again = github_app_auth.get_installation_token()
        assert token_again == "ghs_faketoken123"
        mock_get.assert_called_once()
        mock_post.assert_called_once()

    _reset_cache()


def test_get_installation_token_refreshes_once_expired():
    pem, _ = _throwaway_private_key_pem()
    _reset_cache()

    def _resp(token: str, expires_in: int):
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.json.return_value = {"token": token, "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + expires_in))}
        return r

    install_resp = MagicMock()
    install_resp.raise_for_status.return_value = None
    install_resp.json.return_value = [{"id": 42}]

    with (
        patch.object(settings, "github_app_id", "999888"),
        patch.object(settings, "github_app_private_key", pem),
        patch.object(settings, "github_app_installation_id", "42"),
        patch("app.integrations.github_app_auth.httpx.get", return_value=install_resp),
        patch(
            "app.integrations.github_app_auth.httpx.post",
            side_effect=[_resp("first-token", -10), _resp("second-token", 3600)],
        ) as mock_post,
    ):
        first = github_app_auth.get_installation_token()
        assert first == "first-token"

        second = github_app_auth.get_installation_token()
        assert second == "second-token"
        assert mock_post.call_count == 2

    _reset_cache()

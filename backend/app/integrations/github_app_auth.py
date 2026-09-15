"""Real GitHub App server-to-server auth (JWT -> installation access token)
-- the piece the OAuth App flow (routers/github.py's /oauth/start,
/oauth/callback) genuinely cannot provide: an OAuth token acts as the human
who authorized it, never as a distinct bot identity. This is inactive until
GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY are both set (see
`github_app_configured()`) -- until then github_client.py keeps using the
existing PAT/OAuth bearer token, unchanged. Needs a private key generated on
the App's own settings page (Settings -> Developer settings -> GitHub Apps ->
<app> -> Private keys -> Generate a private key) -- a client secret, which is
an OAuth App concept, cannot substitute for this.

Flow (https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app):
1. Sign a short-lived JWT (RS256, <=10 min) with the App's private key,
   claiming this App's identity (`iss`).
2. Exchange that JWT for an installation access token, scoped to one
   installation (one org/user that installed the App) -- this token acts AS
   THE APP, not as any human, and is what every GitHub write should use once
   configured.
3. Installation tokens expire in ~1h; cached here and refreshed a minute
   before expiry rather than on every call.
"""

from __future__ import annotations

import threading
import time

import httpx
import jwt

from app.config import settings

API_BASE = "https://api.github.com"
_JWT_TTL_SECONDS = 9 * 60  # under GitHub's 10-minute ceiling, with margin for clock skew
_TOKEN_REFRESH_MARGIN_SECONDS = 60

_lock = threading.Lock()
_cached_token: str | None = None
_cached_expires_at: float = 0.0


def github_app_configured() -> bool:
    return bool(settings.github_app_id and settings.github_app_private_key)


def _app_jwt() -> str:
    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + _JWT_TTL_SECONDS, "iss": settings.github_app_id}
    return jwt.encode(payload, settings.github_app_private_key, algorithm="RS256")


def _discover_installation_id(app_jwt: str) -> str:
    """Auto-discovers the installation if GITHUB_APP_INSTALLATION_ID wasn't
    set explicitly -- convenient for a single-org deployment like this one,
    where there is exactly one installation to find."""
    resp = httpx.get(
        f"{API_BASE}/app/installations",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    resp.raise_for_status()
    installations = resp.json()
    if not installations:
        raise RuntimeError("GitHub App has no installations -- install it on the target org/repo first")
    return str(installations[0]["id"])


def get_installation_token() -> str:
    """Returns a valid installation access token, minting or refreshing one
    as needed. Thread-safe (github_client.py's callers run in worker
    threads via asyncio.to_thread) -- a lock around the mint/refresh, not
    around every read, since re-checking the cached token inside the lock
    is what actually prevents a refresh stampede."""
    global _cached_token, _cached_expires_at

    with _lock:
        if _cached_token and time.time() < _cached_expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
            return _cached_token

        app_jwt = _app_jwt()
        installation_id = settings.github_app_installation_id or _discover_installation_id(app_jwt)

        resp = httpx.post(
            f"{API_BASE}/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        _cached_token = data["token"]
        # "2026-01-01T12:00:00Z" -> epoch seconds, without pulling in a full
        # ISO-8601 parsing dependency for one fixed, always-UTC-Z format.
        expires_struct = time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
        _cached_expires_at = time.mktime(expires_struct) - time.timezone

        return _cached_token

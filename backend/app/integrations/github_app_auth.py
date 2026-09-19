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

import logging
import threading
import time

import httpx
import jwt

from app.config import settings

API_BASE = "https://api.github.com"
_JWT_TTL_SECONDS = 9 * 60  # under GitHub's 10-minute ceiling, with margin for clock skew
_TOKEN_REFRESH_MARGIN_SECONDS = 60
logger = logging.getLogger("whipguard.github_app_auth")

_lock = threading.Lock()
# installation_id -> (token, expires_at epoch). One cache per installation so
# org A cannot reuse org B's token after a mint for B.
_cached: dict[str, tuple[str, float]] = {}
_discovered_id: str | None = None

# Back-compat aliases the existing tests reset.
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


def _resolve_installation_id(app_jwt: str, explicit: str | None) -> str:
    global _discovered_id
    if explicit:
        return str(explicit)
    if settings.github_app_installation_id:
        return str(settings.github_app_installation_id)
    if _discovered_id:
        return _discovered_id
    _discovered_id = _discover_installation_id(app_jwt)
    return _discovered_id


def installation_id_for_repo(repo_full_name: str | None) -> str | None:
    """The GitHub App installation that owns this repo, if any.

    Reads organizations.github_app_installation_id through the repo's org.
    Falls back to the process-wide GITHUB_APP_INSTALLATION_ID. Never raises:
    a missing org must not take down a GitHub write.
    """
    if repo_full_name:
        try:
            from app import sync_db

            with sync_db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT o.github_app_installation_id FROM repos r "
                    "JOIN organizations o ON o.id = r.org_id "
                    "WHERE r.github_full_name = %s",
                    (repo_full_name,),
                )
                row = cur.fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            logger.warning("could not resolve GitHub App installation for %s", repo_full_name)
    return settings.github_app_installation_id or None


def get_installation_token(installation_id: str | None = None) -> str:
    """Returns a valid installation access token, minting or refreshing one
    as needed. Thread-safe (github_client.py's callers run in worker
    threads via asyncio.to_thread) -- a lock around the mint/refresh, not
    around every read, since re-checking the cached token inside the lock
    is what actually prevents a refresh stampede.

    `installation_id` selects which install to mint for. Empty uses the
    process-wide setting, then auto-discovery (single-install deployments).
    """
    global _cached_token, _cached_expires_at

    with _lock:
        app_jwt = _app_jwt()
        resolved = _resolve_installation_id(app_jwt, installation_id)
        cached = _cached.get(resolved)
        if cached and time.time() < cached[1] - _TOKEN_REFRESH_MARGIN_SECONDS:
            return cached[0]

        resp = httpx.post(
            f"{API_BASE}/app/installations/{resolved}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        token = data["token"]
        expires_struct = time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
        expires_at = time.mktime(expires_struct) - time.timezone
        _cached[resolved] = (token, expires_at)
        _cached_token = token
        _cached_expires_at = expires_at
        return token

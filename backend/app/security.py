"""Password hashing and the access-token half of the auth pair.

Access token: stateless, short-lived (15 min), HMAC-signed JSON -- cheap to
verify on every request with no DB round-trip. Refresh token: the stateful,
revocable half (app/models.py's RefreshToken), minted and rotated in
routers/auth.py. Splitting the two is the actual "proper refresh token flow"
ask -- a short-lived access token limits the blast radius of a stolen
cookie, and a DB-backed, single-use, rotated refresh token is what makes
logout and theft-detection real rather than just "wait for the JWT to
expire."
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

import bcrypt

from app.config import settings

ACCESS_TOKEN_TTL_SECONDS = 15 * 60
REFRESH_TOKEN_TTL_SECONDS = 14 * 24 * 3600


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def _signing_key() -> bytes:
    return settings.session_secret.encode("utf-8")


def issue_access_token(user_id: uuid.UUID, role: str) -> str:
    payload = {"sub": str(user_id), "role": role, "exp": int(time.time()) + ACCESS_TOKEN_TTL_SECONDS}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    signature = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def decode_access_token(token: str) -> dict | None:
    """Returns {"sub", "role"} for a validly-signed, unexpired token; None
    for anything else (tampered, malformed, expired) -- the caller (main.py's
    require_session) turns None into a 401 that tells the frontend to try a
    silent refresh before giving up."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None

    if not isinstance(payload, dict) or "sub" not in payload:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None

    return {"sub": payload["sub"], "role": payload.get("role", "member")}


def sign_state(purpose: str, ttl_seconds: int = 300, extra: dict | None = None) -> str:
    """A stateless CSRF-state token: signed purpose+nonce+expiry (+ an
    optional small payload, e.g. which repo a Slack OAuth round-trip is
    for), verified by re-checking the signature -- no server-side session
    storage needed (this app dropped Starlette's SessionMiddleware entirely
    in favor of the access/refresh cookie pair; a one-shot OAuth `state`
    param doesn't need a session, just something unforgeable and
    short-lived)."""
    payload = {"purpose": purpose, "nonce": secrets.token_urlsafe(12), "exp": int(time.time()) + ttl_seconds}
    if extra:
        payload.update(extra)
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    signature = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_state(token: str, purpose: str) -> dict | None:
    """Returns the full decoded payload (including any `extra` sign_state
    was given) on success, so a caller can pull e.g. `repo_id` back out --
    None for anything invalid. Truthy/falsy on its own still works for a
    caller (like github.py's) that only cares whether it's valid."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if payload.get("purpose") != purpose or int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


INVITE_TOKEN_TTL_SECONDS = 7 * 24 * 3600


def sign_invite_token(access_request_id: uuid.UUID) -> str:
    """Mailed to an approved AccessRequest's own email address (never shown
    in the admin UI) -- carries only the request id, not a copy of
    name/email, so redemption always reads the current row rather than a
    snapshot from decision time."""
    payload = {"access_request_id": str(access_request_id), "exp": int(time.time()) + INVITE_TOKEN_TTL_SECONDS}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    signature = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def decode_invite_token(token: str) -> uuid.UUID | None:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if not isinstance(payload, dict) or "access_request_id" not in payload:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    try:
        return uuid.UUID(payload["access_request_id"])
    except ValueError:
        return None


def new_refresh_secret() -> str:
    """The RAW secret that goes in the cookie -- never stored anywhere.
    Only its hash (below) is persisted, the same reason passwords are
    hashed rather than stored: a DB read can't hand an attacker a live
    session."""
    return secrets.token_urlsafe(48)


def hash_refresh_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

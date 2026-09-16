"""Real multi-user auth: email+password signup (admin-approved), and a
proper access+refresh token pair -- a short-lived signed access token plus a
stateful, rotated, revocable refresh token (app/security.py explains why
that split, not a bare long-lived JWT). Replaces the single shared
admin_password + Starlette SessionMiddleware cookie this app started with.

Cookie shape: `access_token` (path "/", short TTL, sent on every request)
and `refresh_token` (path "/api/auth" ONLY -- the browser never sends it to
an ordinary API call, just to /refresh and /logout, so a route that leaks
request headers elsewhere can't leak the long-lived credential).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.enums import AccessRequestStatus, UserRole, UserStatus
from app.integrations import email_client
from app.models import AccessRequest, RefreshToken, User
from app.security import (
    ACCESS_TOKEN_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    decode_access_token,
    decode_invite_token,
    hash_password,
    hash_refresh_secret,
    issue_access_token,
    new_refresh_secret,
    sign_invite_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth")
logger = logging.getLogger("whipguard.auth")

_ACCESS_COOKIE = "access_token"
_REFRESH_COOKIE = "refresh_token"

# How long an ALREADY-ROTATED refresh token stays acceptable, and only to
# prove "you are the legitimate holder, here is a fresh access token".
#
# Rotation-on-every-use plus "a second use is theft" is the textbook shape,
# and taken literally it logs real users out constantly: the dashboard fires
# several requests at once, they all read the same cookie before any
# Set-Cookie lands, the first rotates it, and every sibling then presents a
# token the server has just marked revoked. That is a race, not a thief, and
# treating it as theft revoked the whole family -- the actual mechanism
# behind "every redeploy logs my users out" (a redeploy is simply the moment
# a page reloads and fires its whole request burst at once).
#
# Inside this window a reused token is honoured WITHOUT rotating again and
# WITHOUT reissuing a refresh cookie: the browser already holds the
# successor that the winning request set, so the loser just needs an access
# token. Outside the window -- or if the successor is itself already dead --
# it is still treated as theft and still kills the family.
REFRESH_REUSE_GRACE_SECONDS = 60


def _cookies_secure() -> bool:
    return not settings.session_secret.startswith("change-me")  # true in any real deployment


def _set_auth_cookies(response: Response, access_token: str, refresh_secret: str) -> None:
    secure = _cookies_secure()
    response.set_cookie(
        _ACCESS_COOKIE, access_token, max_age=ACCESS_TOKEN_TTL_SECONDS,
        httponly=True, samesite="lax", secure=secure, path="/",
    )
    response.set_cookie(
        _REFRESH_COOKIE, refresh_secret, max_age=REFRESH_TOKEN_TTL_SECONDS,
        httponly=True, samesite="lax", secure=secure, path="/api/auth",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(_ACCESS_COOKIE, path="/")
    response.delete_cookie(_REFRESH_COOKIE, path="/api/auth")


async def _issue_session(db, response: Response, user: User, user_agent: str | None) -> None:
    access = issue_access_token(user.id, user.role.value)
    raw_refresh = new_refresh_secret()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_secret(raw_refresh),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
            user_agent=(user_agent or "")[:255],
        )
    )
    await db.commit()
    _set_auth_cookies(response, access, raw_refresh)


@router.post("/request-access")
async def request_access(request: Request, response: Response):
    """Collects name/email/reason -- creates NO account. The very first
    request on a completely fresh deployment (zero Users AND zero prior
    AccessRequests) is auto-approved and completed immediately, purely so a
    brand-new deployment isn't stuck waiting on an admin who doesn't exist
    yet; every request after that genuinely waits for a human decision."""
    body = await request.json()
    name = str(body.get("name", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    reason = str(body.get("reason", "")).strip()
    if not name or not email or "@" not in email or not reason:
        raise HTTPException(400, "name, a valid email, and a reason for access are required")

    async with async_session() as db:
        existing_user = (await db.execute(select(User).where(User.email == email))).scalars().first()
        if existing_user:
            raise HTTPException(409, "an account with that email already exists")

        existing_pending = (
            await db.execute(
                select(AccessRequest).where(AccessRequest.email == email, AccessRequest.status == AccessRequestStatus.PENDING)
            )
        ).scalars().first()
        if existing_pending:
            return {"ok": True, "status": "pending"}

        is_first_ever = (await db.execute(select(User.id).limit(1))).scalars().first() is None and (
            await db.execute(select(AccessRequest.id).limit(1))
        ).scalars().first() is None

        access_request = AccessRequest(name=name, email=email, reason=reason)
        db.add(access_request)
        await db.flush()

        if is_first_ever:
            # No admin exists yet to decide this -- auto-approve, and hand
            # the invite token straight back in the response (there is no
            # admin inbox to mail it to either) so the frontend can go
            # straight to the same "set your password" page a normal
            # approval's email links to. complete_invite() below is what
            # actually decides ADMIN vs MEMBER, by checking whether any User
            # exists at THAT moment -- this path never creates a User itself.
            access_request.status = AccessRequestStatus.APPROVED
            access_request.decided_at = datetime.now(timezone.utc)
            await db.commit()
            invite_token = sign_invite_token(access_request.id)
            return {"ok": True, "status": "approved", "invite_token": invite_token}

        await db.commit()

        if email_client.smtp_configured() and settings.notify_email:
            try:
                subject = "WhipGuard: new access request"
                html = (
                    f"<p><b>{name}</b> ({email}) requested access to WhipGuard.</p>"
                    f"<p><b>Reason:</b> {reason}</p>"
                    f'<p><a href="https://whip-guard.zakarias.in/admin/users">Review in the admin dashboard</a></p>'
                )
                text = f"{name} ({email}) requested access.\nReason: {reason}\nReview: https://whip-guard.zakarias.in/admin/users"
                await email_client.send_email(settings.notify_email, subject, html, text)
            except Exception:
                logger.exception("failed to notify admin of new access request %s", email)

        return {"ok": True, "status": "pending"}


@router.get("/invite")
async def check_invite(token: str):
    """Backs the 'set your password' page -- returns the name/email to show
    (never a password field pre-filled, obviously), or a clear reason it
    can't be used."""
    access_request_id = decode_invite_token(token)
    if not access_request_id:
        raise HTTPException(400, "this invite link is invalid or has expired")

    async with async_session() as db:
        access_request = await db.get(AccessRequest, access_request_id)
        if not access_request or access_request.status != AccessRequestStatus.APPROVED:
            raise HTTPException(400, "this invite link is no longer valid")
        if access_request.invite_consumed_at:
            raise HTTPException(409, "this invite has already been used -- sign in instead")
        return {"name": access_request.name, "email": access_request.email}


@router.post("/complete-invite")
async def complete_invite(request: Request, response: Response):
    body = await request.json()
    token = str(body.get("token", ""))
    password = str(body.get("password", ""))
    if len(password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")

    access_request_id = decode_invite_token(token)
    if not access_request_id:
        raise HTTPException(400, "this invite link is invalid or has expired")

    async with async_session() as db:
        access_request = await db.get(AccessRequest, access_request_id)
        if not access_request or access_request.status != AccessRequestStatus.APPROVED:
            raise HTTPException(400, "this invite link is no longer valid")
        if access_request.invite_consumed_at:
            raise HTTPException(409, "this invite has already been used -- sign in instead")

        existing_user = (await db.execute(select(User).where(User.email == access_request.email))).scalars().first()
        if existing_user:
            access_request.invite_consumed_at = datetime.now(timezone.utc)
            await db.commit()
            raise HTTPException(409, "an account with that email already exists -- sign in instead")

        # The only place "is this the first account ever" gets decided for
        # real (main.py's own startup bootstrap is a separate safety net for
        # a deployment nobody ever visited the frontend of) -- whoever
        # completes the very first invite becomes admin, same as the old
        # signup flow's is_first_user check did, just moved to the point an
        # account is actually created instead of when it's requested.
        is_first_user = (await db.execute(select(User.id).limit(1))).scalars().first() is None

        name_parts = access_request.name.strip().split(maxsplit=1)
        first_name = name_parts[0] if name_parts else None
        last_name = name_parts[1] if len(name_parts) > 1 else None

        user = User(
            email=access_request.email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN if is_first_user else UserRole.MEMBER,
            status=UserStatus.ACTIVE,
            approved_by=access_request.decided_by,
            first_name=first_name,
            last_name=last_name,
        )
        db.add(user)
        access_request.invite_consumed_at = datetime.now(timezone.utc)
        await db.flush()
        await _issue_session(db, response, user, request.headers.get("user-agent"))
        return {"ok": True, "role": user.role.value}


@router.post("/login")
async def login(request: Request, response: Response):
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    async with async_session() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalars().first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(401, "incorrect email or password")
        if user.status != UserStatus.ACTIVE:
            raise HTTPException(403, "this account has been deactivated")

        user.last_login_at = datetime.now(timezone.utc)
        await _issue_session(db, response, user, request.headers.get("user-agent"))
        return {"ok": True, "role": user.role.value}


class RefreshRejected(Exception):
    """The presented refresh token cannot be honoured. `clear_cookies` is
    False only for a transient rejection, where wiping the browser's cookies
    would turn a recoverable moment into a forced re-login."""

    def __init__(self, detail: str, *, clear_cookies: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.clear_cookies = clear_cookies


async def _consume_refresh(db, raw: str, *, user_agent: str, rotate: bool) -> tuple[User, str | None]:
    """Validate a refresh secret and return (user, new_raw_secret_or_None).

    `rotate=False` is for read-only callers (/session): they need to know who
    the holder is and hand back a fresh access token, and rotating there
    would make an ordinary page load race with every other in-flight request
    for no security gain -- rotation is what POST /refresh is for.

    Raises RefreshRejected; never returns a partially-valid result.
    """
    now = datetime.now(timezone.utc)
    existing = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_secret(raw)))
    ).scalars().first()

    if existing is None:
        raise RefreshRejected("invalid refresh token")

    if existing.revoked_at is not None:
        successor = await db.get(RefreshToken, existing.replaced_by_id) if existing.replaced_by_id else None
        within_grace = (now - existing.revoked_at.replace(tzinfo=timezone.utc)).total_seconds() <= REFRESH_REUSE_GRACE_SECONDS
        successor_alive = (
            successor is not None
            and successor.revoked_at is None
            and successor.expires_at.replace(tzinfo=timezone.utc) > now
        )
        if within_grace and successor_alive:
            user = await db.get(User, existing.user_id)
            if not user or user.status != UserStatus.ACTIVE:
                raise RefreshRejected("account no longer active")
            logger.info("refresh race absorbed for user %s -- successor still live, not rotating", user.id)
            return user, None

        await db.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.user_id == existing.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        logger.warning("refresh token reuse detected for user %s -- full session revoked", existing.user_id)
        raise RefreshRejected("session revoked -- please log in again")

    if existing.expires_at.replace(tzinfo=timezone.utc) < now:
        raise RefreshRejected("refresh token expired -- please log in again")

    user = await db.get(User, existing.user_id)
    if not user or user.status != UserStatus.ACTIVE:
        raise RefreshRejected("account no longer active")

    if not rotate:
        return user, None

    new_raw = new_refresh_secret()
    new_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_secret(new_raw),
        expires_at=now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        user_agent=user_agent[:255],
    )
    db.add(new_token)
    await db.flush()
    existing.revoked_at = now
    existing.replaced_by_id = new_token.id
    await db.commit()
    return user, new_raw


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    """Rotates the refresh token, subject to REFRESH_REUSE_GRACE_SECONDS."""
    raw = request.cookies.get(_REFRESH_COOKIE)
    if not raw:
        raise HTTPException(401, "no refresh token")

    async with async_session() as db:
        try:
            user, new_raw = await _consume_refresh(
                db, raw, user_agent=request.headers.get("user-agent") or "", rotate=True
            )
        except RefreshRejected as rejected:
            if rejected.clear_cookies:
                _clear_auth_cookies(response)
            raise HTTPException(401, rejected.detail) from None

    access = issue_access_token(user.id, user.role.value)
    if new_raw:
        _set_auth_cookies(response, access, new_raw)
    else:
        # Grace path: the browser already holds a live successor cookie that
        # the winning request set. Touching the refresh cookie here would
        # overwrite a good value with a stale one.
        response.set_cookie(
            _ACCESS_COOKIE, access, max_age=ACCESS_TOKEN_TTL_SECONDS,
            httponly=True, samesite="lax", secure=_cookies_secure(), path="/",
        )
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    raw = request.cookies.get(_REFRESH_COOKIE)
    if raw:
        async with async_session() as db:
            token_hash = hash_refresh_secret(raw)
            existing = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))).scalars().first()
            if existing and existing.revoked_at is None:
                existing.revoked_at = datetime.now(timezone.utc)
                await db.commit()
    _clear_auth_cookies(response)
    return {"ok": True}


def _session_payload(user: User) -> dict:
    return {
        "authenticated": True,
        "email": user.email,
        "role": user.role.value,
        "onboarding_completed": user.onboarding_completed_at is not None,
    }


@router.get("/session")
async def session_status(request: Request, response: Response):
    """Who the caller is -- falling back to the refresh cookie when the
    access token has merely expired.

    Without that fallback this endpoint reported `authenticated: false` for a
    user whose 14-day refresh token was perfectly valid, purely because their
    15-minute access token had aged out. It answers 200 either way, so the
    client's own 401-triggered refresh never fired for it, and the gate that
    asks this question first concluded the user was logged out. Net effect:
    a forced re-login every 15 idle minutes. The refresh token is the source
    of truth for "is this session alive"; the access token is just the fast
    path.
    """
    access = request.cookies.get(_ACCESS_COOKIE)
    decoded = decode_access_token(access) if access else None

    async with async_session() as db:
        if decoded:
            user = await db.get(User, uuid.UUID(decoded["sub"]))
            if user and user.status == UserStatus.ACTIVE:
                return _session_payload(user)

        raw = request.cookies.get(_REFRESH_COOKIE)
        if not raw:
            return {"authenticated": False}

        try:
            # rotate=False: an ordinary page load must not consume the
            # rotation slot that POST /refresh owns, or every concurrent
            # request on that page would be racing it.
            user, _ = await _consume_refresh(
                db, raw, user_agent=request.headers.get("user-agent") or "", rotate=False
            )
        except RefreshRejected:
            _clear_auth_cookies(response)
            return {"authenticated": False}

    response.set_cookie(
        _ACCESS_COOKIE, issue_access_token(user.id, user.role.value), max_age=ACCESS_TOKEN_TTL_SECONDS,
        httponly=True, samesite="lax", secure=_cookies_secure(), path="/",
    )
    return _session_payload(user)

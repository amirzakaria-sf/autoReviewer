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


def _set_auth_cookies(response: Response, access_token: str, refresh_secret: str) -> None:
    secure = not settings.session_secret.startswith("change-me")  # true in any real deployment
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

        user = User(
            email=access_request.email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN if is_first_user else UserRole.MEMBER,
            status=UserStatus.ACTIVE,
            approved_by=access_request.decided_by,
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


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    """Rotates the refresh token on every use. A presented token that is
    already revoked (i.e. was already rotated once before) means the SAME
    secret was used twice -- proof of a copied/stolen cookie, not a race --
    so that whole session is killed: every refresh token for this user is
    revoked, forcing a real re-login everywhere."""
    raw = request.cookies.get(_REFRESH_COOKIE)
    if not raw:
        raise HTTPException(401, "no refresh token")

    token_hash = hash_refresh_secret(raw)

    async with async_session() as db:
        existing = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))).scalars().first()

        if existing is None:
            _clear_auth_cookies(response)
            raise HTTPException(401, "invalid refresh token")

        if existing.revoked_at is not None:
            await db.execute(
                RefreshToken.__table__.update()
                .where(RefreshToken.user_id == existing.user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await db.commit()
            _clear_auth_cookies(response)
            logger.warning("refresh token reuse detected for user %s -- full session revoked", existing.user_id)
            raise HTTPException(401, "session revoked -- please log in again")

        if existing.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            _clear_auth_cookies(response)
            raise HTTPException(401, "refresh token expired -- please log in again")

        user = await db.get(User, existing.user_id)
        if not user or user.status != UserStatus.ACTIVE:
            _clear_auth_cookies(response)
            raise HTTPException(401, "account no longer active")

        new_raw = new_refresh_secret()
        new_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_secret(new_raw),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
        db.add(new_token)
        await db.flush()
        existing.revoked_at = datetime.now(timezone.utc)
        existing.replaced_by_id = new_token.id
        await db.commit()

        access = issue_access_token(user.id, user.role.value)
        _set_auth_cookies(response, access, new_raw)
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


@router.get("/session")
async def session_status(request: Request):
    access = request.cookies.get(_ACCESS_COOKIE)
    decoded = decode_access_token(access) if access else None
    if not decoded:
        return {"authenticated": False}

    async with async_session() as db:
        user = await db.get(User, uuid.UUID(decoded["sub"]))
        if not user or user.status != UserStatus.ACTIVE:
            return {"authenticated": False}
        return {"authenticated": True, "email": user.email, "role": user.role.value}

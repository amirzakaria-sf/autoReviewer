"""The contract the operator console (console.zakarias.in) uses to read and manage WhipGuard.

`/api/console/v1/*` -- additive; nothing that existed before reads or writes through here. Authenticated only by
`X-Console-Token` == `CONSOLE_API_TOKEN` (>= 32 chars, constant-time compare). Unset, short or wrong is a **404** for
every caller, so an unconfigured deploy exposes no route, and a session cookie is no substitute (main.py lists the prefix
as public only because this guard is the credential).

What the console can do: read a summary, token usage, users and the access-request queue; approve / reject / revoke a
request (same `access_decisions` code and mails as the in-app admin); suspend / restore a user.
What it cannot do: create users, read secrets, change roles, or touch repositories. Administrators cannot be suspended or
revoked this way, so a console mistake never locks everyone out of the product.

WhipGuard has no per-user token ledger (`council_runs` is per role and model, not per person) and no per-user allowance, so
usage is reported per role and the quota route answers 409 instead of pretending.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import access_decisions
from app.config import settings
from app.db import get_db
from app.enums import AccessRequestStatus, UserRole, UserStatus
from app.models import AccessRequest, Fix, Issue, Organization, RefreshToken, Repo, User

MIN_TOKEN_LENGTH = 32


def token_ok(presented: str | None, expected: str | None) -> bool:
    want = expected or ""
    if len(want) < MIN_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(
        hashlib.sha256((presented or "").encode()).digest(), hashlib.sha256(want.encode()).digest()
    )


async def console_guard(request: Request) -> None:
    if not token_ok(request.headers.get("x-console-token"), settings.console_api_token):
        raise HTTPException(404, "Not found.")


router = APIRouter(prefix="/api/console/v1", dependencies=[Depends(console_guard)])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _name(user: User) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part) or user.email.split("@")[0]


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    users = dict((await db.execute(select(User.status, func.count()).group_by(User.status))).all())
    pending = (
        await db.execute(select(func.count()).select_from(AccessRequest).where(AccessRequest.status == AccessRequestStatus.PENDING))
    ).scalar_one()
    usage = (
        await db.execute(
            text(
                "SELECT count(*) AS calls, coalesce(sum(input_tokens + output_tokens), 0) AS tokens "
                "FROM council_runs WHERE created_at >= :since"
            ),
            {"since": since},
        )
    ).mappings().first()
    count = lambda model: select(func.count()).select_from(model)  # noqa: E731
    return {
        "product": "whipguard",
        "users_total": sum(users.values()),
        "users_active": users.get(UserStatus.ACTIVE, 0),
        "users_suspended": users.get(UserStatus.DEACTIVATED, 0),
        "pending_requests": pending,
        "tokens_24h": int(usage["tokens"] or 0),
        "calls_24h": int(usage["calls"] or 0),
        "repos_total": (await db.execute(count(Repo))).scalar_one(),
        "issues_total": (await db.execute(count(Issue))).scalar_one(),
        "fixes_total": (await db.execute(count(Fix))).scalar_one(),
        "organizations": (await db.execute(count(Organization))).scalar_one(),
    }


@router.get("/usage")
async def usage(days: int = 7, db: AsyncSession = Depends(get_db)):
    days = max(1, min(int(days), 120))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            text(
                """
                SELECT date_trunc('day', created_at)::date AS day, role, model,
                       count(*) AS calls,
                       coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(cached_input_tokens), 0) AS cached_input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens
                FROM council_runs WHERE created_at >= :since
                GROUP BY 1, role, model
                """
            ),
            {"since": since},
        )
    ).mappings().all()
    return {
        "product": "whipguard",
        "window_days": days,
        # Per role, not per person: council_runs carries no user. The console shows these as "role: <name>".
        "rollups": [
            {
                "day": r["day"].isoformat(),
                "subjectKind": "role",
                "subjectId": f"role:{r['role']}",
                "subjectLabel": f"role: {r['role']}",
                "model": r["model"],
                "calls": int(r["calls"]),
                "inputTokens": int(r["input_tokens"]),
                "cachedTokens": int(r["cached_input_tokens"]),
                "outputTokens": int(r["output_tokens"]),
                "costUsd": 0.0,  # no price table here, and a guessed dollar figure is worse than none
            }
            for r in rows
        ],
    }


@router.get("/users")
async def users(q: str = "", db: AsyncSession = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc()).limit(200)
    q = q.strip()[:100]
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.email.ilike(like) | User.first_name.ilike(like) | User.last_name.ilike(like))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "name": _name(u),
                "status": "active" if u.status == UserStatus.ACTIVE else "suspended",
                "role": u.role.value,
                "tokens_today": None,
                "quota_daily_tokens": None,
                "created_at": _iso(u.created_at),
                "last_login": _iso(u.last_login_at),
            }
            for u in rows
        ],
        "quota_supported": False,
        "quota_label": "",
    }


async def _user(db: AsyncSession, uid: str) -> User:
    try:
        user = await db.get(User, uuid.UUID(uid))
    except ValueError:
        user = None
    if user is None:
        raise HTTPException(404, "Not found.")
    return user


async def _revoke_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    tokens = (
        await db.execute(select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)))
    ).scalars().all()
    for token in tokens:
        token.revoked_at = now


@router.post("/users/{uid}/suspend")
async def suspend(uid: str, db: AsyncSession = Depends(get_db)):
    user = await _user(db, uid)
    if user.role == UserRole.ADMIN:
        raise HTTPException(409, "Administrators cannot be suspended from the console.")
    user.status = UserStatus.DEACTIVATED
    await _revoke_sessions(db, user.id)
    await db.commit()
    return {"id": str(user.id), "status": "suspended"}


@router.post("/users/{uid}/restore")
async def restore(uid: str, db: AsyncSession = Depends(get_db)):
    user = await _user(db, uid)
    user.status = UserStatus.ACTIVE
    await db.commit()
    return {"id": str(user.id), "status": "active"}


@router.post("/users/{uid}/quota")
async def quota(uid: str, db: AsyncSession = Depends(get_db)):
    await _user(db, uid)
    raise HTTPException(409, "WhipGuard has no per-user token allowance, so there is nothing to set.")


def _request_status(row: AccessRequest, user: User | None) -> str:
    if row.status == AccessRequestStatus.APPROVED:
        if row.invite_consumed_at is not None:
            return "revoked" if user is not None and user.status != UserStatus.ACTIVE else "active"
        return "approved"
    return row.status.value


@router.get("/access-requests")
async def access_requests(status: str = "all", db: AsyncSession = Depends(get_db)):
    stmt = select(AccessRequest).order_by(AccessRequest.created_at.desc()).limit(500)
    if status.lower() in ("pending", "approved", "rejected"):
        stmt = stmt.where(AccessRequest.status == AccessRequestStatus(status.lower()))
    rows = (await db.execute(stmt)).scalars().all()
    emails = {r.email for r in rows}
    owners = {
        u.email: u for u in (await db.execute(select(User).where(User.email.in_(emails)))).scalars().all()
    } if emails else {}
    return {
        "items": [
            {
                "id": str(r.id),
                "email": r.email,
                "name": r.name,
                "company": "",
                "role_title": "",
                "use_case": r.reason,
                "status": _request_status(r, owners.get(r.email)),
                "reject_reason": r.decision_reason or "",
                "created_at": _iso(r.created_at),
                "reviewed_at": _iso(r.decided_at),
            }
            for r in rows
        ]
    }


async def _request(db: AsyncSession, rid: str) -> AccessRequest:
    try:
        row = await db.get(AccessRequest, uuid.UUID(rid))
    except ValueError:
        row = None
    if row is None:
        raise HTTPException(404, "Not found.")
    return row


async def _default_org_id(db: AsyncSession) -> str:
    """An approved account in no organization sees nothing, so the console puts the person in the deployment's only
    organization. With none or several there is nothing safe to guess: the request is approved without one, exactly as
    the first account on a deployment is, and an admin can add them from WhipGuard."""
    orgs = (await db.execute(select(Organization.id).limit(2))).scalars().all()
    return str(orgs[0]) if len(orgs) == 1 else ""


@router.post("/access-requests/{rid}/approve")
async def approve(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    row = await _request(db, rid)
    if row.status == AccessRequestStatus.APPROVED:
        return {"id": str(row.id), "status": _request_status(row, None), "activated": row.invite_consumed_at is not None}
    try:
        body = await request.json()
    except Exception:
        body = {}
    org_id = str((body or {}).get("org_id", "") or "") or await _default_org_id(db)
    await access_decisions.approve(db, row, decided_by=None, org_id=org_id)
    return {"id": str(row.id), "status": "approved", "activated": False, "org_id": org_id or None}


@router.post("/access-requests/{rid}/reject")
async def reject(rid: str, request: Request, db: AsyncSession = Depends(get_db)):
    row = await _request(db, rid)
    if row.status == AccessRequestStatus.REJECTED:
        return {"id": str(row.id), "status": "rejected"}
    if row.invite_consumed_at is not None:
        raise HTTPException(409, "This person already has an account; revoke instead.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    await access_decisions.reject(db, row, decided_by=None, reason=str((body or {}).get("reason", ""))[:500])
    return {"id": str(row.id), "status": "rejected"}


@router.post("/access-requests/{rid}/revoke")
async def revoke(rid: str, db: AsyncSession = Depends(get_db)):
    row = await _request(db, rid)
    if row.invite_consumed_at is not None:
        user = (await db.execute(select(User).where(User.email == row.email))).scalars().first()
        if user is None:
            raise HTTPException(409, "Nothing to revoke.")
        if user.role == UserRole.ADMIN:
            raise HTTPException(409, "Administrators cannot be revoked from the console.")
        user.status = UserStatus.DEACTIVATED
        await _revoke_sessions(db, user.id)
        await db.commit()
        return {"id": str(row.id), "status": "revoked"}
    if row.status == AccessRequestStatus.APPROVED:
        # An unused invitation: close it, so the emailed link can no longer be used to sign up.
        row.status = AccessRequestStatus.REJECTED
        row.decision_reason = "Access revoked"
        row.decided_at = datetime.now(timezone.utc)
        await db.commit()
        return {"id": str(row.id), "status": "rejected"}
    raise HTTPException(409, "Nothing to revoke.")

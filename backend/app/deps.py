"""FastAPI dependencies reading the (user_id, role) main.py's middleware
already decoded from the access-token cookie onto request.state -- these
never touch the DB just to check identity, only role-gating does."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.enums import UserStatus
from app.models import User


def current_user_id(request: Request) -> uuid.UUID:
    return uuid.UUID(request.state.user_id)


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, current_user_id(request))
    if not user or user.status != UserStatus.ACTIVE or user.role.value != "admin":
        raise HTTPException(403, "admin access required")
    return user


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, current_user_id(request))
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(401, "not authenticated")
    return user


async def visible_repo_ids(request: Request) -> list[uuid.UUID]:
    """Every repository the caller is allowed to see: their organization's.

    `repos.org_id` is the one column carrying tenancy -- issues, fixes,
    traces and chunks all reach their org THROUGH their repo -- but nothing
    read it. Every product endpoint listed every repository and every issue in
    the deployment, so a member of one organization could read another's
    findings, which carry file paths, code excerpts and leaked-credential
    detail.

    Invisible while there was one org. Found the moment a second one existed:
    a developer invited to a brand-new org with no repositories at all opened
    the dashboard and saw twenty findings belonging to somebody else.

    Returns a list rather than an org id because that is what every caller
    needs -- issues and fixes are scoped by repo, and building the same join
    at each call site is how one of them ends up missing it.

    A system admin is NOT exempt. They administer the deployment and see
    aggregate counts under /api/admin; that is a different thing from reading
    another organization's code.
    """
    import asyncio

    from app import orgs

    user_id = current_user_id(request)
    memberships = await asyncio.to_thread(orgs.orgs_for_user, user_id)
    if not memberships:
        # Not an error: a brand-new account genuinely owns nothing yet, and
        # every list endpoint correctly returns empty rather than refusing.
        return []
    return await asyncio.to_thread(orgs.repo_ids_for_org, memberships[0]["id"])

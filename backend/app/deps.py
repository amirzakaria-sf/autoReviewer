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

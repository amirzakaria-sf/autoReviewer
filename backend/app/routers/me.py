"""Self-service profile: the authenticated user's own name/mobile number,
password change, and the first-login onboarding-modal gate (routers/auth.py
never sets these -- a fresh account starts with only what the access
request + invite flow collected, i.e. email and a best-effort name split).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user
from app.models import User
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/me")


def _profile_dict(user: User) -> dict:
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "mobile_number": user.mobile_number,
        "role": user.role.value,
        "onboarding_completed": user.onboarding_completed_at is not None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("")
async def get_me(user: User = Depends(current_user)):
    return _profile_dict(user)


@router.patch("")
async def update_me(body: dict, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if "first_name" in body:
        user.first_name = (str(body["first_name"]).strip() or None)
    if "last_name" in body:
        user.last_name = (str(body["last_name"]).strip() or None)
    if "mobile_number" in body:
        user.mobile_number = (str(body["mobile_number"]).strip() or None)
    await db.commit()
    return {"ok": True, "profile": _profile_dict(user)}


@router.post("/password")
async def change_password(body: dict, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    current_password = str(body.get("current_password", ""))
    new_password = str(body.get("new_password", ""))
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(401, "current password is incorrect")
    if len(new_password) < 8:
        raise HTTPException(400, "new password must be at least 8 characters")
    user.password_hash = hash_password(new_password)
    await db.commit()
    return {"ok": True}


@router.post("/onboarding/complete")
async def complete_onboarding(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Called either when the user connects something from the first-login
    modal, or explicitly skips it -- either way, the modal never shows
    again for this account."""
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True}

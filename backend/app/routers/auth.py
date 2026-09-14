"""App-level session login.

Replaces the earlier nginx HTTP Basic Auth stopgap (the browser's native
credential popup, ahead of and unrelated to the app itself) with a real
signed-cookie session and a dashboard login page. Single shared password --
there's one operator for this demo, not a multi-user account system; a
GitHub-identity "profile" is layered on top (see routers/github.py) using the
server's own stored token, not a second login system.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config import settings

router = APIRouter(prefix="/api/auth")


@router.post("/login")
async def login(request: Request):
    body = await request.json()
    if body.get("password") != settings.admin_password:
        raise HTTPException(401, "incorrect password")
    request.session["authenticated"] = True
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/session")
async def session_status(request: Request):
    return {"authenticated": bool(request.session.get("authenticated"))}

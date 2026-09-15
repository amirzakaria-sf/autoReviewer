"""GET /api/email/action -- the landing endpoint for the magic links built by
email_client.build_fix_proposed_email. Deliberately unauthenticated (main.py
lists this prefix in _PUBLIC_PATHS): it's opened straight from an inbox with
no session cookie, and the signed/expiring token IS the credential, exactly
as email_client.verify_action_token documents.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.graphs.approval_graph import resolve_approval
from app.integrations.email_client import verify_action_token

router = APIRouter(prefix="/api/email")


def _page(title: str, message: str) -> HTMLResponse:
    return HTMLResponse(f"""
    <!doctype html><html><head><meta charset="utf-8">
    <title>{title} — WhipGuard</title>
    <style>
      body {{ font-family: -apple-system, sans-serif; background:#0b0d12; color:#e6e8ee;
              display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
      .card {{ max-width:420px; padding:32px; border:1px solid #232734; border-radius:12px; background:#12151d; text-align:center; }}
      a {{ color:#7db4ff; }}
    </style></head>
    <body><div class="card"><h2>{title}</h2><p>{message}</p>
    <p><a href="https://whip-guard.zakarias.in/dashboard">Open the dashboard</a></p></div></body></html>
    """)


@router.get("/action")
async def email_action(token: str, db: AsyncSession = Depends(get_db)):
    decoded = verify_action_token(token)
    if not decoded:
        return _page("Link expired or invalid", "This action link is no longer valid. Open the dashboard to act on this fix directly.")

    try:
        fix_id = uuid.UUID(decoded["fix_id"])
    except ValueError:
        return _page("Link expired or invalid", "This action link is malformed.")

    result = await resolve_approval(
        db, fix_id, approved=(decoded["action"] == "approve"), actor="email", surface="email"
    )

    if not result.get("ok") and result.get("error") == "fix not found":
        return _page("Fix not found", "This fix no longer exists.")
    if result.get("already_handled"):
        return _page("Already handled", result.get("message", "This fix was already acted on."))

    verb = "approved" if decoded["action"] == "approve" else "rejected"
    return _page(f"Fix {verb}", f"Recorded. Status is now: {result.get('status', 'updated')}.")

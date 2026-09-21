"""Web Push registration.

The public VAPID key is served from here rather than only inlined into the
bundle at build time. Next inlines `NEXT_PUBLIC_*` during `next build`, so a
key added to `.env` after an image was built is simply absent from the running
JavaScript -- and the symptom is "push silently does nothing", with a checkout
that looks correct. A sibling project spent a debugging session on exactly
that, and concluded the key was missing by grepping a stale `.next/` directory.
Reading it from the API at runtime removes the whole class of problem.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import push
from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.models import User

router = APIRouter(prefix="/api/push")


def _subscription_from(body: dict) -> tuple[str, str, str]:
    """A browser's `PushSubscription.toJSON()`, validated.

    Accepts the browser's own shape rather than a hand-rolled one: the client
    passes `subscription.toJSON()` through verbatim, so there is no place for
    the two to disagree about field names.
    """
    endpoint = str(body.get("endpoint") or "").strip()
    keys = body.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        raise HTTPException(400, "that is not a valid push subscription")
    if len(endpoint) > 2000:
        raise HTTPException(400, "push endpoint is implausibly long")
    return endpoint, p256dh, auth


@router.get("/status")
async def push_status(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    devices = (
        await db.execute(
            text(
                "SELECT count(*), coalesce(max(created_at)::text, '') "
                "FROM push_subscriptions WHERE user_id = :uid"
            ),
            {"uid": str(user.id)},
        )
    ).first()
    return {
        "configured": push.configured(),
        "public_key": settings.vapid_public_key,
        "devices": int(devices[0] or 0) if devices else 0,
        "newest_device_at": (devices[1] or None) if devices else None,
    }


@router.post("/subscribe")
async def subscribe(
    body: dict,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register this device, or re-bind it to the current account.

    Upserting on `endpoint` is the whole mechanism. A push subscription belongs
    to the origin and the service worker, not to a session -- it survives
    logout and account switches -- so a device that subscribed as one person
    would otherwise keep delivering to that person forever, while the settings
    toggle reported everything was fine because it only reads browser state.

    The client calls this on every authenticated session, not just when the
    toggle is flipped, which self-heals an already-wrong device with no
    migration and nothing for anyone to click.
    """
    if not push.configured():
        raise HTTPException(503, "push notifications are not configured on this deployment")

    endpoint, p256dh, auth = _subscription_from(body)
    await db.execute(
        text(
            """
            INSERT INTO push_subscriptions
                (id, user_id, endpoint, p256dh, auth, provider, user_agent, created_at)
            VALUES (gen_random_uuid(), :uid, :endpoint, :p256dh, :auth, :provider, :ua, now())
            ON CONFLICT (endpoint) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth,
                provider = EXCLUDED.provider,
                user_agent = EXCLUDED.user_agent
            """
        ),
        {
            "uid": str(user.id),
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "provider": push.provider_for(endpoint),
            "ua": (request.headers.get("user-agent") or "")[:255],
        },
    )
    await db.commit()
    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(body: dict, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Drop the SERVER row for one device.

    Scoped to the caller: an endpoint is a bearer-ish secret, but it also
    appears in logs and in the other account's row, so "anyone holding the
    string may delete it" is not a boundary worth having.
    """
    endpoint = str(body.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(400, "endpoint is required")
    result = await db.execute(
        text("DELETE FROM push_subscriptions WHERE endpoint = :endpoint AND user_id = :uid"),
        {"endpoint": endpoint, "uid": str(user.id)},
    )
    await db.commit()
    return {"ok": True, "removed": result.rowcount or 0}


@router.post("/test")
async def send_test(user: User = Depends(current_user)):
    """Send this account's devices a real notification.

    Worth an endpoint of its own: a toggle that flips to "on" proves the
    browser granted permission and nothing else -- not that the key is right,
    not that the service worker renders, not that the row is bound to the
    right person. This is the only thing in the product that proves the whole
    path, and it is one tap from the settings screen.
    """
    if not push.configured():
        raise HTTPException(503, "push notifications are not configured on this deployment")

    import asyncio

    delivered = await asyncio.to_thread(
        push.send_to_user,
        user.id,
        "WhipGuard",
        "Notifications are working. This is what a finding will look like.",
        "/dashboard",
        "whipguard-test",
    )
    return {"ok": delivered > 0, "delivered": delivered}

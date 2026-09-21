"""Web Push delivery.

Sync and psycopg-based, like `app/council_runs.py` and for the same reason:
the callers are graph nodes and worker handlers running off the event loop,
which never carry an AsyncSession.

Three rules here are not style choices. Each one cost a sibling project real,
silent downtime, and the failure mode in every case was the same -- push kept
returning success while nothing arrived:

1. **The `sub` claim must be a `mailto:` or `https:` URI.** Apple rejects
   anything else with 403 `BadJwtToken`. A configured address that already
   carries the scheme must not be prefixed again.
2. **Never reuse a claims dict.** `pywebpush` MUTATES what it is handed,
   injecting `aud` and `exp`. A shared dict carries the first push service's
   audience and a stale expiry into every later send.
3. **Log the response BODY on failure.** The status code says "rejected"; the
   body says `BadJwtToken` or `MismatchSenderId` or `UNREGISTERED`. Dropping
   it makes the next failure undiagnosable, which is how one goes unexamined
   for weeks.

A 201 from a push service proves TRANSPORT, never delivery. It means the token
was accepted, not that a notification was rendered.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger("whipguard.push")


def configured() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def provider_for(endpoint: str) -> str:
    """Which push service this endpoint belongs to. Stored per subscription so
    "which platform is failing" is a query, not a string-parse over the table."""
    try:
        host = urlparse(endpoint).hostname or ""
    except ValueError:
        return ""
    if "apple" in host:
        return "apple"
    if "google" in host or "fcm" in host:
        return "fcm"
    if "mozilla" in host:
        return "mozilla"
    if "windows" in host or "microsoft" in host:
        return "wns"
    return host[:64]


def _sub_claim() -> str:
    contact = (settings.vapid_admin_email or "").strip()
    if not contact:
        return "mailto:whipguard@whip-guard.zakarias.in"
    if contact.startswith(("mailto:", "https://")):
        return contact
    return f"mailto:{contact}"


def send_to_user(user_id, title: str, body: str, url: str = "/dashboard", tag: str = "") -> int:
    """Push to every device this person has registered. Returns how many were
    accepted. Never raises -- a notification is an enrichment, and a council
    run must not fail because a phone is unreachable."""
    if not configured():
        return 0
    if not user_id:
        return 0

    from app import sync_db

    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = %s",
                (str(user_id),),
            )
            rows = cur.fetchall()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not read push subscriptions for %s: %s", user_id, error)
        return 0

    if not rows:
        return 0

    payload = json.dumps(
        {"title": title, "body": body, "url": url, "icon": "/icon-192.png", "tag": tag or ""}
    )

    delivered = 0
    expired: list[str] = []
    for subscription_id, endpoint, p256dh, auth in rows:
        if _send_one(endpoint, p256dh, auth, payload, expired, subscription_id):
            delivered += 1

    if expired:
        _forget(expired)
    return delivered


def _send_one(endpoint: str, p256dh: str, auth: str, payload: str, expired: list, subscription_id) -> bool:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            # A fresh dict per call. pywebpush mutates this.
            vapid_claims={"sub": _sub_claim()},
            ttl=600,
        )
        return True
    except WebPushException as error:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status in (404, 410):
            # The browser revoked this subscription or it expired. Drop the
            # row so we stop paying for it; the person's other devices are
            # untouched, and the next app open re-registers this one.
            expired.append(subscription_id)
            return False
        detail = ""
        try:
            detail = (response.text or "")[:300] if response is not None else ""
        except Exception:  # noqa: BLE001
            detail = "<unreadable body>"
        logger.error(
            "web push failed provider=%s status=%s body=%s error=%s",
            provider_for(endpoint), status, detail, error,
        )
        return False
    except Exception as error:  # noqa: BLE001
        logger.error("web push raised for provider=%s: %s", provider_for(endpoint), error)
        return False


def _forget(subscription_ids: list) -> None:
    from app import sync_db

    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM push_subscriptions WHERE id = ANY(%s)",
                ([str(sid) for sid in subscription_ids],),
            )
            conn.commit()
        logger.info("removed %d expired push subscription(s)", len(subscription_ids))
    except Exception as error:  # noqa: BLE001
        logger.warning("could not remove expired push subscriptions: %s", error)


def send_to_org(org_id, title: str, body: str, url: str = "/dashboard", tag: str = "") -> int:
    """Push to everyone in an organization.

    This is the shape the councils actually need: a fix is proposed for a
    REPOSITORY, which belongs to an org, not to whoever happened to trigger
    the scan. Routing it to one person would mean the only human who ever
    hears about a finding is the one who was already watching.
    """
    if not configured() or not org_id:
        return 0

    from app import sync_db

    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT u.id
                FROM users u
                JOIN org_members m ON m.user_id = u.id
                WHERE m.org_id = %s AND u.status = 'ACTIVE'
                """,
                (str(org_id),),
            )
            user_ids = [row[0] for row in cur.fetchall()]
    except Exception as error:  # noqa: BLE001
        logger.warning("could not resolve org members for push: %s", error)
        return 0

    return sum(send_to_user(user_id, title, body, url, tag) for user_id in user_ids)


def send_for_repo(repo_id, title: str, body: str, url: str = "/dashboard", tag: str = "") -> int:
    """Everything reaches its org through its repo -- `repos.org_id` is the one
    column carrying tenancy, so this is the only correct way to go from a
    council event to an audience."""
    if not configured() or not repo_id:
        return 0

    from app import sync_db

    try:
        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT org_id FROM repos WHERE id = %s", (str(repo_id),))
            row = cur.fetchone()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not resolve repo org for push: %s", error)
        return 0

    if not row or not row[0]:
        return 0
    return send_to_org(row[0], title, body, url, tag)

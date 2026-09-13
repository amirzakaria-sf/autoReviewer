"""Notification dedupe (plan.md §6.4): one row per problem, keyed by identity,
never one row per detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


async def record_condition(db: AsyncSession, fix_id, issue_id, condition_key: str) -> Notification:
    stmt = select(Notification).where(
        Notification.fix_id == fix_id,
        Notification.issue_id == issue_id,
        Notification.condition_key == condition_key,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing:
        existing.occurrence_count += 1
        existing.last_seen_at = now
        return existing

    notification = Notification(
        fix_id=fix_id,
        issue_id=issue_id,
        condition_key=condition_key,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(notification)
    await db.flush()
    return notification


def should_notify(
    notification: Notification,
    cooldown_seconds: int = 300,
    is_escalation: bool = False,
    min_occurrences: int = 2,
) -> bool:
    """An escalation bypasses cooldown unconditionally. A brand new condition
    (first sighting) never notifies on its own — it needs `min_occurrences`
    consecutive sightings first. A still-firing condition inside its cooldown
    window does not re-notify."""
    if is_escalation:
        return True

    if notification.occurrence_count < min_occurrences:
        return False

    if notification.notified_at is None:
        return True

    now = datetime.now(timezone.utc)
    notified_at = notification.notified_at
    if notified_at.tzinfo is None:
        notified_at = notified_at.replace(tzinfo=timezone.utc)

    return (now - notified_at) >= timedelta(seconds=cooldown_seconds)


def mark_notified(notification: Notification) -> None:
    notification.notified_at = datetime.now(timezone.utc)
    notification.notify_count += 1

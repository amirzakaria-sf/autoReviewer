import pytest

from app.notifications import mark_notified, should_notify
from app.models import Notification


def _make_notification(occurrence_count=1, notified_at=None):
    return Notification(
        fix_id=None,
        issue_id=None,
        condition_key="playwright-flake",
        occurrence_count=occurrence_count,
        notified_at=notified_at,
        notify_count=0,
    )


def test_first_sighting_does_not_notify():
    n = _make_notification(occurrence_count=1)
    assert should_notify(n) is False


def test_second_consecutive_sighting_notifies():
    n = _make_notification(occurrence_count=2)
    assert should_notify(n) is True


def test_still_firing_condition_inside_cooldown_does_not_renotify():
    n = _make_notification(occurrence_count=5)
    mark_notified(n)  # sets notified_at = now
    assert should_notify(n, cooldown_seconds=300) is False


def test_escalation_bypasses_cooldown_unconditionally():
    n = _make_notification(occurrence_count=1)
    mark_notified(n)
    assert should_notify(n, cooldown_seconds=300, is_escalation=True) is True


def test_mark_notified_increments_notify_count():
    n = _make_notification(occurrence_count=2)
    assert n.notify_count == 0
    mark_notified(n)
    assert n.notify_count == 1
    assert n.notified_at is not None

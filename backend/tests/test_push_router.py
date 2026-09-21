"""Who a push subscription belongs to.

`app/push.py` is tested for DELIVERY. This file is about OWNERSHIP, which is
where the interesting bug lives: a subscription belongs to the origin and the
service worker, not to a login session, so it survives logout and account
switches. Record the owner once at subscribe time and a device that subscribed
as one person delivers to that person forever — while the settings toggle
reads "enabled", because all it can see is browser state.

The upsert on `endpoint` is what fixes that, and it is only a fix if
re-subscribing actually transfers the row. That is the first test here.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, text

from app.config import settings
from app.db import async_session
from app.enums import UserRole, UserStatus
from app.models import PushSubscription, User
from app.routers import push as push_router


class _Request:
    headers = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"}


def _subscription(endpoint: str) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": "p256dh-value", "auth": "auth-value"}}


@pytest.fixture
async def two_people():
    async with async_session() as db:
        alice = User(
            email=f"alice-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x", role=UserRole.MEMBER, status=UserStatus.ACTIVE,
        )
        bob = User(
            email=f"bob-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x", role=UserRole.MEMBER, status=UserStatus.ACTIVE,
        )
        db.add_all([alice, bob])
        await db.commit()
        ids = (alice, bob)
        yield ids

    async with async_session() as db:
        await db.execute(delete(PushSubscription).where(PushSubscription.user_id.in_([alice.id, bob.id])))
        await db.execute(delete(User).where(User.id.in_([alice.id, bob.id])))
        await db.commit()


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", "B" + "x" * 86)
    monkeypatch.setattr(settings, "vapid_private_key", "y" * 43)


async def _owner_of(db, endpoint: str):
    return (
        await db.execute(
            text("SELECT user_id FROM push_subscriptions WHERE endpoint = :e"), {"e": endpoint}
        )
    ).scalar()


# --- the re-bind ------------------------------------------------------------


async def test_re_subscribing_transfers_the_device_to_the_current_account(two_people):
    """The whole fix. One phone, two accounts: without this the device keeps
    delivering to whoever subscribed first, and nothing anywhere says so."""
    alice, bob = two_people
    endpoint = f"https://web.push.apple.com/{uuid.uuid4()}"

    async with async_session() as db:
        await push_router.subscribe(_subscription(endpoint), _Request(), user=alice, db=db)
        assert str(await _owner_of(db, endpoint)) == str(alice.id)

        # Bob signs in on the same phone. The auth gate re-POSTs the same
        # subscription; it must move, not duplicate.
        await push_router.subscribe(_subscription(endpoint), _Request(), user=bob, db=db)
        assert str(await _owner_of(db, endpoint)) == str(bob.id)

        count = (
            await db.execute(
                text("SELECT count(*) FROM push_subscriptions WHERE endpoint = :e"), {"e": endpoint}
            )
        ).scalar()
        assert count == 1, "the endpoint is unique -- a re-bind must move the row, not add one"


async def test_two_devices_for_one_person_both_survive(two_people):
    """A re-bind must not be a reset: a laptop and a phone are two rows."""
    alice, _ = two_people
    phone = f"https://web.push.apple.com/{uuid.uuid4()}"
    laptop = f"https://fcm.googleapis.com/{uuid.uuid4()}"

    async with async_session() as db:
        await push_router.subscribe(_subscription(phone), _Request(), user=alice, db=db)
        await push_router.subscribe(_subscription(laptop), _Request(), user=alice, db=db)
        status = await push_router.push_status(user=alice, db=db)

    assert status["devices"] == 2


async def test_the_push_service_is_recorded_per_device(two_people):
    """Which platform is failing should be a query, not a string-parse over
    every row at 3am."""
    alice, _ = two_people
    endpoint = f"https://web.push.apple.com/{uuid.uuid4()}"

    async with async_session() as db:
        await push_router.subscribe(_subscription(endpoint), _Request(), user=alice, db=db)
        provider = (
            await db.execute(
                text("SELECT provider FROM push_subscriptions WHERE endpoint = :e"), {"e": endpoint}
            )
        ).scalar()

    assert provider == "apple"


# --- unsubscribe is scoped --------------------------------------------------


async def test_unsubscribing_cannot_remove_someone_elses_device(two_people):
    """An endpoint is a long opaque string, but it also appears in logs and in
    the other account's row. "Anyone holding it may delete it" is not a
    boundary worth having."""
    alice, bob = two_people
    endpoint = f"https://web.push.apple.com/{uuid.uuid4()}"

    async with async_session() as db:
        await push_router.subscribe(_subscription(endpoint), _Request(), user=alice, db=db)
        result = await push_router.unsubscribe({"endpoint": endpoint}, user=bob, db=db)
        assert result["removed"] == 0
        assert str(await _owner_of(db, endpoint)) == str(alice.id)

        result = await push_router.unsubscribe({"endpoint": endpoint}, user=alice, db=db)
        assert result["removed"] == 1
        assert await _owner_of(db, endpoint) is None


# --- input the browser did not produce --------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"endpoint": "https://web.push.apple.com/x"},                      # no keys
        {"endpoint": "ftp://elsewhere/x", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https://x/y", "keys": {"p256dh": "", "auth": "b"}},
    ],
)
async def test_a_malformed_subscription_is_refused(two_people, body):
    alice, _ = two_people
    async with async_session() as db:
        with pytest.raises(HTTPException) as caught:
            await push_router.subscribe(body, _Request(), user=alice, db=db)
    assert caught.value.status_code == 400


async def test_an_implausibly_long_endpoint_is_refused(two_people):
    alice, _ = two_people
    body = _subscription("https://web.push.apple.com/" + "a" * 2100)
    async with async_session() as db:
        with pytest.raises(HTTPException) as caught:
            await push_router.subscribe(body, _Request(), user=alice, db=db)
    assert caught.value.status_code == 400


# --- deployment state -------------------------------------------------------


async def test_an_unconfigured_deployment_refuses_rather_than_storing_a_dead_row(two_people, monkeypatch):
    alice, _ = two_people
    monkeypatch.setattr(settings, "vapid_private_key", "")
    async with async_session() as db:
        with pytest.raises(HTTPException) as caught:
            await push_router.subscribe(
                _subscription(f"https://web.push.apple.com/{uuid.uuid4()}"), _Request(), user=alice, db=db
            )
    assert caught.value.status_code == 503


async def test_status_serves_the_public_key_so_the_client_never_reads_a_stale_bundle(two_people):
    """NEXT_PUBLIC_* is inlined at `next build`, so a key added to .env after
    an image was built is absent from the running JavaScript -- and the only
    symptom is that push quietly does nothing."""
    alice, _ = two_people
    async with async_session() as db:
        status = await push_router.push_status(user=alice, db=db)

    assert status["configured"] is True
    assert status["public_key"] == settings.vapid_public_key
    assert status["devices"] == 0

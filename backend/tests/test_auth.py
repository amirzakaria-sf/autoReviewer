"""Real Postgres integration tests (this project's established pattern --
see test_poller.py) for the request-access -> admin-decides -> invite ->
complete-invite flow, and the access/refresh auth pair on top of it.

Calling the route functions directly (not through TestClient) -- see the
previous version of this file's own note: TestClient runs the ASGI app on a
separate anyio portal thread with its own event loop, which collides with
this app's single module-level asyncpg engine exactly the way conftest.py's
_dispose_db_engine_after_test fixture documents for cross-event-loop reuse.
"""

from __future__ import annotations

import uuid
from http.cookies import SimpleCookie

from fastapi import Response
from sqlalchemy import delete, select

from app.db import async_session
from app.enums import AccessRequestStatus, UserStatus
from app.models import AccessRequest, RefreshToken, User
from app.routers.admin import approve_access_request, reject_access_request
from app.routers.auth import complete_invite, login, logout, refresh, request_access


class _FakeRequest:
    def __init__(self, json_body=None, cookies=None):
        self._json_body = json_body or {}
        self.cookies = cookies or {}
        self.headers = {"user-agent": "pytest"}

    async def json(self):
        return self._json_body


def _cookies_from_response(response: Response) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for name, value in response.raw_headers:
        if name.decode().lower() != "set-cookie":
            continue
        jar = SimpleCookie()
        jar.load(value.decode())
        for key, morsel in jar.items():
            cookies[key] = morsel.value
    return cookies


async def _cleanup(*emails: str) -> None:
    async with async_session() as db:
        for email in emails:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            if user:
                await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
                await db.execute(delete(User).where(User.id == user.id))
            await db.execute(delete(AccessRequest).where(AccessRequest.email == email))
        await db.commit()


async def _any_user_exists() -> bool:
    async with async_session() as db:
        return (await db.execute(select(User.id).limit(1))).scalars().first() is not None


async def test_request_access_creates_no_account():
    email = "auth-test-request@example.com"
    await _cleanup(email)
    try:
        result = await request_access(
            _FakeRequest({"name": "Ada Lovelace", "email": email, "reason": "need to review my team's repo"}), Response()
        )
        assert result["ok"] is True

        async with async_session() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            req = (await db.execute(select(AccessRequest).where(AccessRequest.email == email))).scalars().first()

        assert user is None  # the entire point of this flow
        assert req is not None
        assert req.name == "Ada Lovelace"
        assert req.reason == "need to review my team's repo"
        # First-ever request on an empty DB auto-approves; anything else
        # (there's a pre-existing admin in this live DB from earlier
        # sessions) stays pending for a human to decide.
        assert req.status in (AccessRequestStatus.PENDING, AccessRequestStatus.APPROVED)
    finally:
        await _cleanup(email)


async def test_duplicate_pending_request_is_idempotent_not_a_second_row():
    email = "auth-test-dup@example.com"
    await _cleanup(email)
    try:
        await request_access(_FakeRequest({"name": "A", "email": email, "reason": "r"}), Response())
        await request_access(_FakeRequest({"name": "A", "email": email, "reason": "r again"}), Response())

        async with async_session() as db:
            rows = (await db.execute(select(AccessRequest).where(AccessRequest.email == email))).scalars().all()
        pending_or_approved = [r for r in rows if r.status != AccessRequestStatus.REJECTED]
        assert len(pending_or_approved) == 1
    finally:
        await _cleanup(email)


async def test_approving_then_completing_invite_creates_exactly_one_active_user():
    email = "auth-test-invite@example.com"
    await _cleanup(email)
    try:
        async with async_session() as db:
            req = AccessRequest(name="Grace Hopper", email=email, reason="need it", status=AccessRequestStatus.PENDING)
            db.add(req)
            await db.commit()
            await db.refresh(req)

        async with async_session() as db:
            admin_row = (await db.execute(select(User).limit(1))).scalars().first()
            admin_id = admin_row.id if admin_row else uuid.uuid4()
            fake_admin = type("Admin", (), {"id": admin_id})()
            approve_result = await approve_access_request(req.id, admin=fake_admin, db=db)

        assert approve_result["request"]["status"] == "approved"

        # Recover the invite token the same way the email would have --
        # sign a fresh one for the now-approved request, matching exactly
        # what approve_access_request itself does internally.
        from app.security import sign_invite_token

        token = sign_invite_token(req.id)

        complete_result = await complete_invite(
            _FakeRequest({"token": token, "password": "correct horse battery"}), Response()
        )
        assert complete_result["ok"] is True

        async with async_session() as db:
            users = (await db.execute(select(User).where(User.email == email))).scalars().all()
            refreshed_req = await db.get(AccessRequest, req.id)

        assert len(users) == 1
        assert users[0].status == UserStatus.ACTIVE
        assert refreshed_req.invite_consumed_at is not None

        # Single-use: completing again with the same token must fail, and
        # must NOT create a second account.
        try:
            await complete_invite(_FakeRequest({"token": token, "password": "another password entirely"}), Response())
            assert False, "expected HTTPException on reuse"
        except Exception as exc:
            assert getattr(exc, "status_code", None) in (409, 400)

        async with async_session() as db:
            users_after = (await db.execute(select(User).where(User.email == email))).scalars().all()
        assert len(users_after) == 1
    finally:
        await _cleanup(email)


async def test_rejected_request_never_creates_an_account():
    email = "auth-test-rejected@example.com"
    await _cleanup(email)
    try:
        async with async_session() as db:
            req = AccessRequest(name="Nope Person", email=email, reason="reason", status=AccessRequestStatus.PENDING)
            db.add(req)
            await db.commit()
            await db.refresh(req)

        async with async_session() as db:
            admin_row = (await db.execute(select(User).limit(1))).scalars().first()
            fake_admin = type("Admin", (), {"id": admin_row.id if admin_row else uuid.uuid4()})()
            result = await reject_access_request(req.id, {"reason": "not a fit"}, admin=fake_admin, db=db)

        assert result["request"]["status"] == "rejected"
        assert result["request"]["decision_reason"] == "not a fit"

        async with async_session() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
        assert user is None
    finally:
        await _cleanup(email)


async def _signup_and_activate(email: str) -> None:
    """Test helper: goes through the real request-access -> approve ->
    complete-invite chain (not a shortcut) so refresh/logout tests exercise
    the actual flow end to end."""
    async with async_session() as db:
        req = AccessRequest(name="Flow Test", email=email, reason="reason", status=AccessRequestStatus.PENDING)
        db.add(req)
        await db.commit()
        await db.refresh(req)

    async with async_session() as db:
        admin_row = (await db.execute(select(User).limit(1))).scalars().first()
        fake_admin = type("Admin", (), {"id": admin_row.id if admin_row else uuid.uuid4()})()
        await approve_access_request(req.id, admin=fake_admin, db=db)

    from app.security import sign_invite_token

    token = sign_invite_token(req.id)
    await complete_invite(_FakeRequest({"token": token, "password": "correct horse battery"}), Response())


async def test_refresh_rotates_the_token():
    email = "auth-test-rotation@example.com"
    await _cleanup(email)
    try:
        await _signup_and_activate(email)

        login_resp = Response()
        await login(_FakeRequest({"email": email, "password": "correct horse battery"}), login_resp)
        old_cookies = _cookies_from_response(login_resp)
        assert "refresh_token" in old_cookies

        first_refresh_resp = Response()
        await refresh(_FakeRequest(cookies={"refresh_token": old_cookies["refresh_token"]}), first_refresh_resp)
        new_cookies = _cookies_from_response(first_refresh_resp)
        assert new_cookies["refresh_token"] != old_cookies["refresh_token"]
    finally:
        await _cleanup(email)


async def test_concurrent_reuse_inside_the_grace_window_keeps_the_session_alive():
    """The bug this window exists for: a page load fires several requests at
    once, they all carry the same refresh cookie, the first rotates it, and
    the rest arrive holding a token the server just revoked. Treating that as
    theft logged real users out on every redeploy."""
    email = "auth-test-refresh-race@example.com"
    await _cleanup(email)
    try:
        await _signup_and_activate(email)

        login_resp = Response()
        await login(_FakeRequest({"email": email, "password": "correct horse battery"}), login_resp)
        original = _cookies_from_response(login_resp)["refresh_token"]

        winner_resp = Response()
        await refresh(_FakeRequest(cookies={"refresh_token": original}), winner_resp)
        successor = _cookies_from_response(winner_resp)["refresh_token"]

        # The loser of the race, still holding the pre-rotation cookie.
        loser_resp = Response()
        result = await refresh(_FakeRequest(cookies={"refresh_token": original}), loser_resp)
        assert result == {"ok": True}

        loser_cookies = _cookies_from_response(loser_resp)
        assert "access_token" in loser_cookies, "the loser still needs a usable access token"
        assert "refresh_token" not in loser_cookies, "must not overwrite the live successor cookie"

        # The successor the winner set is untouched and still works.
        third_resp = Response()
        await refresh(_FakeRequest(cookies={"refresh_token": successor}), third_resp)
        assert "refresh_token" in _cookies_from_response(third_resp)
    finally:
        await _cleanup(email)


async def test_reuse_after_the_grace_window_still_revokes_the_whole_session():
    """Real theft detection is kept: the grace window narrows it to seconds,
    it does not remove it."""
    email = "auth-test-theft@example.com"
    await _cleanup(email)
    try:
        await _signup_and_activate(email)

        login_resp = Response()
        await login(_FakeRequest({"email": email, "password": "correct horse battery"}), login_resp)
        stolen = _cookies_from_response(login_resp)["refresh_token"]

        await refresh(_FakeRequest(cookies={"refresh_token": stolen}), Response())

        # Age the rotation well past the grace window.
        from datetime import datetime, timedelta, timezone

        from app.routers.auth import REFRESH_REUSE_GRACE_SECONDS
        from app.security import hash_refresh_secret

        async with async_session() as db:
            row = (
                await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_secret(stolen)))
            ).scalars().first()
            row.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=REFRESH_REUSE_GRACE_SECONDS + 5)
            await db.commit()

        try:
            await refresh(_FakeRequest(cookies={"refresh_token": stolen}), Response())
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 401

        async with async_session() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            tokens = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
        assert len(tokens) >= 2
        assert all(t.revoked_at is not None for t in tokens), "the whole family must die, not just the reused token"
    finally:
        await _cleanup(email)


async def test_session_survives_an_expired_access_token():
    """A 15-minute access token aging out must not read as 'logged out' while
    a 14-day refresh token is still valid -- this endpoint answers 200 either
    way, so nothing else in the client would ever have recovered it."""
    from app.routers.auth import session_status

    email = "auth-test-session-recovery@example.com"
    await _cleanup(email)
    try:
        await _signup_and_activate(email)

        login_resp = Response()
        await login(_FakeRequest({"email": email, "password": "correct horse battery"}), login_resp)
        cookies = _cookies_from_response(login_resp)

        response = Response()
        payload = await session_status(
            _FakeRequest(cookies={"access_token": "expired.deadbeef", "refresh_token": cookies["refresh_token"]}),
            response,
        )
        assert payload["authenticated"] is True
        assert payload["email"] == email
        assert "access_token" in _cookies_from_response(response), "a fresh access token should be handed back"

        # No refresh cookie at all is still a real 'not logged in'.
        anon = await session_status(_FakeRequest(cookies={"access_token": "expired.deadbeef"}), Response())
        assert anon == {"authenticated": False}
    finally:
        await _cleanup(email)


async def test_logout_revokes_the_refresh_token():
    email = "auth-test-logout@example.com"
    await _cleanup(email)
    try:
        await _signup_and_activate(email)

        login_resp = Response()
        await login(_FakeRequest({"email": email, "password": "correct horse battery"}), login_resp)
        cookies = _cookies_from_response(login_resp)

        await logout(_FakeRequest(cookies={"refresh_token": cookies["refresh_token"]}), Response())

        try:
            await refresh(_FakeRequest(cookies={"refresh_token": cookies["refresh_token"]}), Response())
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 401
    finally:
        await _cleanup(email)


async def test_deactivated_user_cannot_log_in():
    email = "auth-test-deactivated@example.com"
    await _cleanup(email)
    try:
        await _signup_and_activate(email)
        async with async_session() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            user.status = UserStatus.DEACTIVATED
            await db.commit()

        try:
            await login(_FakeRequest({"email": email, "password": "correct horse battery"}), Response())
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403
    finally:
        await _cleanup(email)

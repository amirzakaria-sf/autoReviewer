"""The operator-console contract (/api/console/v1): closed unless a >=32 character token matches, and every mutation
goes through the same code (and mails) as the in-app admin."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session
from app.enums import AccessRequestStatus, UserRole, UserStatus
from app.models import AccessRequest, RefreshToken, User
from app.routers import console_api as c

TOKEN = "t" * 40


class _Req:
    def __init__(self, body=None, token=None):
        self._body = body
        self.headers = {"x-console-token": token} if token is not None else {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


async def _cleanup(*emails: str) -> None:
    async with async_session() as db:
        for email in emails:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            if user:
                await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
                await db.execute(delete(User).where(User.id == user.id))
            await db.execute(delete(AccessRequest).where(AccessRequest.email == email))
        await db.commit()


async def _make_user(email: str, role=UserRole.MEMBER) -> uuid.UUID:
    async with async_session() as db:
        user = User(email=email, password_hash="x", role=role, status=UserStatus.ACTIVE, first_name="Zed", last_name="Q")
        db.add(user)
        await db.commit()
        return user.id


# -- the guard ------------------------------------------------------------------------------------------------------

def test_token_rules():
    assert c.token_ok(TOKEN, TOKEN)
    assert not c.token_ok("x" * 40, TOKEN)
    assert not c.token_ok("", "")
    assert not c.token_ok(None, None)
    assert not c.token_ok("short", "short")
    assert not c.token_ok(None, "a" * 31)
    assert c.token_ok("a" * 32, "a" * 32)


async def test_guard_answers_404_for_missing_wrong_or_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "console_api_token", TOKEN)
    await c.console_guard(_Req(token=TOKEN))  # no raise
    for req in (_Req(), _Req(token="x" * 40)):
        with pytest.raises(HTTPException) as e:
            await c.console_guard(req)
        assert e.value.status_code == 404
    monkeypatch.setattr(settings, "console_api_token", "")
    with pytest.raises(HTTPException) as e:
        await c.console_guard(_Req(token=""))
    assert e.value.status_code == 404


def test_the_prefix_is_public_to_the_session_middleware_and_every_route_is_guarded():
    from app.main import _PUBLIC_PATHS

    assert "/api/console/" in _PUBLIC_PATHS
    assert any(d.dependency is c.console_guard for d in c.router.dependencies)  # router-level: covers every route
    assert len(c.router.routes) >= 10


# -- reads ----------------------------------------------------------------------------------------------------------

async def test_summary_usage_and_users_have_the_shapes_the_console_reads():
    email = "console-user@example.com"
    await _cleanup(email)
    try:
        await _make_user(email)
        async with async_session() as db:
            s = await c.summary(db)
            u = await c.usage(days=999, db=db)
            users = await c.users(q="console-user", db=db)
        assert s["product"] == "whipguard" and s["users_total"] >= 2 and "pending_requests" in s
        assert u["window_days"] == 120 and isinstance(u["rollups"], list)
        row = users["items"][0]
        assert (row["email"], row["name"], row["status"], row["role"]) == (email, "Zed Q", "active", "member")
        assert users["quota_supported"] is False
    finally:
        await _cleanup(email)


# -- users ----------------------------------------------------------------------------------------------------------

async def test_suspend_blocks_the_account_and_revokes_sessions_and_restore_undoes_it():
    email = "console-suspend@example.com"
    await _cleanup(email)
    try:
        uid = await _make_user(email)
        from datetime import datetime, timedelta, timezone

        async with async_session() as db:
            db.add(RefreshToken(user_id=uid, token_hash="h-" + uuid.uuid4().hex, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
            await db.commit()
            assert (await c.suspend(str(uid), db))["status"] == "suspended"
        async with async_session() as db:
            user = await db.get(User, uid)
            tokens = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == uid))).scalars().all()
            assert user.status == UserStatus.DEACTIVATED
            assert tokens and all(t.revoked_at is not None for t in tokens)
            listed = (await c.users(q="console-suspend", db=db))["items"][0]
            assert listed["status"] == "suspended"
            assert (await c.restore(str(uid), db))["status"] == "active"
        async with async_session() as db:
            assert (await db.get(User, uid)).status == UserStatus.ACTIVE
    finally:
        await _cleanup(email)


async def test_an_administrator_cannot_be_suspended_and_quota_is_refused_honestly():
    email = "console-admin@example.com"
    await _cleanup(email)
    try:
        uid = await _make_user(email, UserRole.ADMIN)
        async with async_session() as db:
            with pytest.raises(HTTPException) as e:
                await c.suspend(str(uid), db)
            assert e.value.status_code == 409
            assert (await db.get(User, uid)).status == UserStatus.ACTIVE
            with pytest.raises(HTTPException) as q:
                await c.quota(str(uid), db)
            assert q.value.status_code == 409
            for bad in ("nope", str(uuid.uuid4())):
                with pytest.raises(HTTPException) as nf:
                    await c.suspend(bad, db)
                assert nf.value.status_code == 404
    finally:
        await _cleanup(email)


# -- requests -------------------------------------------------------------------------------------------------------

async def _pending(email: str) -> uuid.UUID:
    async with async_session() as db:
        row = AccessRequest(name="N", email=email, reason="because")
        db.add(row)
        await db.commit()
        return row.id


async def test_approve_mails_the_invite_once_and_reject_mails_the_decline(mail_outbox):
    a, b = "console-approve@example.com", "console-reject@example.com"
    await _cleanup(a, b)
    try:
        ra, rb = await _pending(a), await _pending(b)
        async with async_session() as db:
            out = await c.approve(str(ra), _Req({}), db)
            assert out["status"] == "approved" and out["activated"] is False
            again = await c.approve(str(ra), _Req({}), db)
            assert again["status"] == "approved"
            rej = await c.reject(str(rb), _Req({"reason": "not now"}), db)
            assert rej["status"] == "rejected"
        to_a = [m for m in mail_outbox if m["to"] == a]
        to_b = [m for m in mail_outbox if m["to"] == b]
        assert len(to_a) == 1 and to_a[0]["subject"].startswith("You're in")
        assert len(to_b) == 1 and "not approved" in to_b[0]["text"] and "not now" in to_b[0]["text"]
        async with async_session() as db:
            items = {i["email"]: i["status"] for i in (await c.access_requests("all", db))["items"]}
            assert items[a] == "approved" and items[b] == "rejected"
            pending_only = (await c.access_requests("pending", db))["items"]
            assert a not in {i["email"] for i in pending_only}
    finally:
        await _cleanup(a, b)


async def test_revoke_closes_an_unused_invite_and_suspends_a_used_one(mail_outbox):
    unused, used = "console-unused@example.com", "console-used@example.com"
    await _cleanup(unused, used)
    try:
        r1, r2 = await _pending(unused), await _pending(used)
        async with async_session() as db:
            await c.approve(str(r1), _Req({}), db)
            await c.approve(str(r2), _Req({}), db)
            assert (await c.revoke(str(r1), db))["status"] == "rejected"
            row = await db.get(AccessRequest, r1)
            assert row.status == AccessRequestStatus.REJECTED
        # simulate the invitee finishing signup
        from datetime import datetime, timezone

        uid = await _make_user(used)
        async with async_session() as db:
            row = await db.get(AccessRequest, r2)
            row.invite_consumed_at = datetime.now(timezone.utc)
            await db.commit()
            items = {i["email"]: i["status"] for i in (await c.access_requests("all", db))["items"]}
            assert items[used] == "active"
            with pytest.raises(HTTPException) as cant:
                await c.reject(str(r2), _Req({}), db)
            assert cant.value.status_code == 409
            assert (await c.revoke(str(r2), db))["status"] == "revoked"
            assert (await db.get(User, uid)).status == UserStatus.DEACTIVATED
            items = {i["email"]: i["status"] for i in (await c.access_requests("all", db))["items"]}
            assert items[used] == "revoked"
    finally:
        await _cleanup(unused, used)


# -- CONSOLE_MANAGED --------------------------------------------------------------------------------------------------

async def test_admin_overview_tells_the_dashboard_whether_the_console_owns_requests(monkeypatch):
    from app.routers import admin

    monkeypatch.setattr(settings, "console_managed", False)
    async with async_session() as db:
        off = await admin.admin_overview(db)
    assert off["console_managed"] is False  # default: the in-app admin looks exactly as before

    monkeypatch.setattr(settings, "console_managed", True)
    monkeypatch.setattr(settings, "console_public_url", "https://console.example.test/")
    async with async_session() as db:
        on = await admin.admin_overview(db)
    assert on["console_managed"] is True
    assert on["console_url"] == "https://console.example.test"
    # Nothing else about the overview changes with the flag, so the stat cards and kill switch keep working.
    assert {"users", "access_requests", "kill_switch"} <= set(on)


# --- console §6.12: llm, llm/probe, capabilities ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_reports_each_roles_model_and_env(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "azure_fast_deployment", "nvidia/nemotron-3-ultra-550b-a55b")
    body = await c.llm_config()
    fast = next(r for r in body["roles"] if r["key"] == "fast")
    assert body["provider"] == "nvidia" and fast == {"key": "fast", "env": "NVIDIA_FAST_MODEL", "model": "nvidia/nemotron-3-ultra-550b-a55b"}
    assert "llm.probe" in body["capabilities"]
    monkeypatch.setattr(settings, "llm_provider", "bedrock")
    assert (await c.llm_config())["roles"][0]["env"] == "LLM_FAST_MODEL"


@pytest.mark.asyncio
async def test_probe_goes_through_complete_turn_and_classifies(monkeypatch):
    from app import azure_client

    calls = []
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "azure_worker_deployment", "nvidia/x")
    monkeypatch.setattr(azure_client, "complete_turn", lambda **kw: calls.append(kw) or azure_client.ModelTurn(content="ok"))
    body = await c.llm_probe(_Req({"role": "worker"}))
    assert body["ok"] and calls[0]["deployment"] == "nvidia/x" and calls[0]["role"] == "console_probe"

    def refuse(**kw):
        raise azure_client.ProviderError("misconfigured", "nvidia", "401 bad key")
    monkeypatch.setattr(azure_client, "complete_turn", refuse)
    body = await c.llm_probe(_Req({}))
    assert not body["ok"] and body["error_class"] == "misconfigured" and body["role"] == "fast"


@pytest.mark.asyncio
async def test_probe_tests_only_the_live_configuration(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    for body in ({"provider": "azure"}, {"role": "worker", "model": "other/model"}, {"role": "nope"}):
        with pytest.raises(HTTPException) as e:
            await c.llm_probe(_Req(body))
        assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_summary_advertises_capabilities():
    async with async_session() as db:
        body = await c.summary(db)
    assert "llm.probe" in body["capabilities"]

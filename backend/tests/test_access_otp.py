"""The accept-invite email-code step, the lifecycle mails around it, and the escaping of what they carry.

Same pattern as test_auth.py: real Postgres, route functions called directly (a TestClient's separate event loop
collides with the module-level asyncpg engine).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import delete, select

from app import access_otp
from app.config import settings
from app.db import async_session
from app.enums import AccessRequestStatus
from app.models import AccessRequest, RefreshToken, User
from app.routers.admin import approve_access_request, reject_access_request
from app.routers.auth import (
    check_invite,
    complete_invite,
    invite_send_code,
    invite_verify_code,
    request_access,
)
from app.security import sign_invite_token


class _Req:
    def __init__(self, body=None, headers=None):
        self._body = body or {}
        self.cookies = {}
        self.headers = {"user-agent": "pytest", **(headers or {})}

    async def json(self):
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


async def _approved(email: str, name: str = "Grace Hopper") -> tuple[AccessRequest, str]:
    async with async_session() as db:
        row = AccessRequest(name=name, email=email, reason="needs it", status=AccessRequestStatus.APPROVED)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row, sign_invite_token(row.id)


def _code_from(outbox: list[dict]) -> str:
    match = re.search(r"\b(\d{6})\b", outbox[-1]["text"])
    assert match, outbox[-1]["text"]
    return match.group(1)


# -- pure rules ---------------------------------------------------------------------------------------------------

def test_code_is_six_digits_and_only_a_row_bound_hash_is_stored():
    row = AccessRequest(name="n", email="e@x.invalid", reason="r")
    import uuid

    row.id = uuid.uuid4()
    code = access_otp.issue(row)
    assert re.fullmatch(r"\d{6}", code)
    assert code not in (row.otp_hash or "")
    other = AccessRequest(name="n", email="e@x.invalid", reason="r")
    other.id = uuid.uuid4()
    other.otp_hash, other.otp_expires_at, other.otp_attempts = row.otp_hash, row.otp_expires_at, 0
    assert access_otp.check(other, code) == "wrong"  # one row's code never verifies against another


def test_expiry_attempt_cap_single_use_and_resend_cooldown():
    import uuid

    now = datetime.now(timezone.utc)
    row = AccessRequest(name="n", email="e@x.invalid", reason="r")
    row.id = uuid.uuid4()
    code = access_otp.issue(row, now)
    assert access_otp.seconds_until_resend(row, now + timedelta(seconds=10)) > 0
    assert access_otp.seconds_until_resend(row, now + timedelta(seconds=61)) == 0
    assert access_otp.check(row, code, now + timedelta(minutes=11)) == "expired"

    code = access_otp.issue(row, now)
    wrong = "000000" if code != "000000" else "111111"
    results = [access_otp.check(row, wrong, now) for _ in range(access_otp.OTP_MAX_ATTEMPTS)]
    assert results[:-1] == ["wrong"] * (access_otp.OTP_MAX_ATTEMPTS - 1) and results[-1] == "locked"
    assert access_otp.check(row, code, now) in ("none", "locked")  # even the right code cannot win after the cap

    code = access_otp.issue(row, now)
    assert access_otp.check(row, code, now) == "ok"
    assert access_otp.check(row, code, now) == "none"  # single use
    assert access_otp.is_verified(row, now)
    assert not access_otp.is_verified(row, now + access_otp.VERIFIED_TTL + timedelta(seconds=1))


def test_required_only_when_enabled_mail_works_and_not_the_first_account(mail_outbox, monkeypatch):
    assert access_otp.otp_required(is_first_user=False) is True
    assert access_otp.otp_required(is_first_user=True) is False
    monkeypatch.setattr(settings, "invite_require_otp", False)
    assert access_otp.otp_required(is_first_user=False) is False
    monkeypatch.setattr(settings, "invite_require_otp", True)
    monkeypatch.setattr(settings, "smtp_host", "")
    assert access_otp.otp_required(is_first_user=False) is False


# -- the flow -----------------------------------------------------------------------------------------------------

async def test_password_cannot_be_set_until_the_emailed_code_is_verified(mail_outbox):
    email = "otp-flow@example.com"
    await _cleanup(email)
    try:
        _row, token = await _approved(email)
        info = await check_invite(token)
        assert info["otp_required"] is True and info["otp_verified"] is False

        with pytest.raises(HTTPException) as blocked:
            await complete_invite(_Req({"token": token, "password": "correct horse battery"}), Response())
        assert blocked.value.status_code == 403

        sent = await invite_send_code(_Req({"token": token}))
        assert sent["sent_to"].endswith("@example.com") and sent["sent_to"].startswith("o***")
        assert mail_outbox[-1]["to"] == email  # to the row's own address, never one the caller supplies
        code = _code_from(mail_outbox)

        with pytest.raises(HTTPException) as again:
            await invite_send_code(_Req({"token": token}))
        assert again.value.status_code == 429

        wrong = "000000" if code != "000000" else "111111"
        with pytest.raises(HTTPException) as bad:
            await invite_verify_code(_Req({"token": token, "code": wrong}))
        assert bad.value.status_code == 400
        assert (await invite_verify_code(_Req({"token": token, "code": code})))["ok"] is True
        assert (await check_invite(token))["otp_verified"] is True

        response = Response()
        done = await complete_invite(_Req({"token": token, "password": "correct horse battery"}), response)
        assert done["ok"] is True
        assert [m["subject"] for m in mail_outbox if m["to"] == email][-1] == "Welcome to WhipGuard"
        async with async_session() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
        assert user is not None and user.status.value == "active"
    finally:
        await _cleanup(email)


async def test_without_smtp_the_invite_still_works_as_before():
    email = "otp-nosmtp@example.com"  # autouse fixture blanks SMTP
    await _cleanup(email)
    try:
        _row, token = await _approved(email)
        assert (await check_invite(token))["otp_required"] is False
        done = await complete_invite(_Req({"token": token, "password": "correct horse battery"}), Response())
        assert done["ok"] is True
    finally:
        await _cleanup(email)


async def test_a_consumed_or_garbage_token_cannot_ask_for_a_code(mail_outbox):
    with pytest.raises(HTTPException) as garbage:
        await invite_send_code(_Req({"token": "nope"}))
    assert garbage.value.status_code == 400
    assert mail_outbox == []


# -- lifecycle mails -----------------------------------------------------------------------------------------------

async def test_request_sends_a_receipt_and_an_escaped_operator_notice(mail_outbox, monkeypatch):
    email = "otp-request@example.com"
    monkeypatch.setattr(settings, "notify_email", "ops@example.com")
    await _cleanup(email)
    try:
        # a pre-existing user means this is not the auto-approved first-ever request
        await request_access(_Req({"name": "<script>x</script>", "email": email, "reason": "<b>why</b>"}), Response())
        by_to = {m["to"]: m for m in mail_outbox}
        assert by_to[email]["subject"] == "We received your WhipGuard access request"
        assert by_to["ops@example.com"]["subject"].startswith("WhipGuard: new access request")
        for m in mail_outbox:
            assert "<script>x</script>" not in m["html"] and "<b>why</b>" not in m["html"]
            assert m["html"].lstrip().lower().startswith("<!doctype html>") and m["text"]
        # a duplicate pending request does not mail again
        count = len(mail_outbox)
        await request_access(_Req({"name": "n", "email": email, "reason": "again"}), Response())
        assert len(mail_outbox) == count
    finally:
        await _cleanup(email)


async def test_receipt_can_be_switched_off(mail_outbox, monkeypatch):
    email = "otp-quiet@example.com"
    monkeypatch.setattr(settings, "access_lifecycle_emails", False)
    monkeypatch.setattr(settings, "notify_email", "")
    await _cleanup(email)
    try:
        await request_access(_Req({"name": "n", "email": email, "reason": "r"}), Response())
        assert mail_outbox == []
    finally:
        await _cleanup(email)


async def test_decline_mail_escapes_the_reason(mail_outbox):
    email = "otp-decline@example.com"
    await _cleanup(email)
    try:
        async with async_session() as db:
            row = AccessRequest(name="N", email=email, reason="r")
            db.add(row)
            await db.commit()
            await db.refresh(row)
            admin = (await db.execute(select(User).limit(1))).scalars().first()
            await reject_access_request(row.id, {"reason": "<img src=x onerror=1>"}, admin, db)
        sent = [m for m in mail_outbox if m["to"] == email][-1]
        assert "<img src=x" not in sent["html"] and "&lt;img" in sent["html"]
    finally:
        await _cleanup(email)


def test_every_template_renders_complete_html_with_a_text_part():
    from app.integrations import email_client as c

    mails = [
        c.build_otp_email("A", "123456"), c.build_request_received_email("A", "r"),
        c.build_admin_new_request_email("A", "a@x.invalid", "r"), c.build_invite_email("A", "tok"),
        c.build_access_declined_email("A", "no"), c.build_welcome_email("A"),
        c.build_status_email("t", "Label", "d"), c.build_fix_proposed_email("t", "bug", 90, "https://x", "id"),
        c.build_org_invite_email("A", "Org", "Boss", "https://x/join"),
    ]
    for subject, html, text in mails:
        assert subject and text
        assert "<html" in html and "</html>" in html and "WHIPGUARD" in html

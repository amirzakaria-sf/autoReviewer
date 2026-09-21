"""Web Push, and the four ways it fails silently.

Every rule tested here has a real incident behind it in the sibling project
this was ported from, and they share one property: push kept reporting success
while nothing arrived. A 201 from a push service proves the token was
accepted, never that a notification was rendered — so "it didn't throw" is not
evidence of anything, and neither is a toggle that reads "enabled".
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app import push
from app.config import settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", "B" + "x" * 86)
    monkeypatch.setattr(settings, "vapid_private_key", "y" * 43)
    monkeypatch.setattr(settings, "vapid_admin_email", "noreply@example.com")


def _rows(*endpoints):
    return [(uuid.uuid4(), endpoint, "p256dh-value", "auth-value") for endpoint in endpoints]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Conn:
    def __init__(self, rows):
        self.cur = _Cursor(rows)

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _db(monkeypatch, rows):
    conn = _Conn(rows)
    monkeypatch.setattr("app.sync_db.connection", lambda: conn)
    return conn


def _webpush_error(status, body=""):
    from pywebpush import WebPushException

    response = MagicMock()
    response.status_code = status
    response.text = body
    return WebPushException("rejected", response=response)


# --- the VAPID subject claim -------------------------------------------------


def test_a_bare_email_becomes_a_mailto_uri():
    """Apple rejects anything that is not mailto: or https: with 403
    BadJwtToken, and says nothing else about why."""
    assert push._sub_claim() == "mailto:noreply@example.com"


def test_an_address_that_already_has_a_scheme_is_not_prefixed_twice(monkeypatch):
    """`mailto:mailto:...` is rejected outright, and the only symptom is that
    pushes never arrive. One character of .env away at all times."""
    monkeypatch.setattr(settings, "vapid_admin_email", "mailto:ops@example.com")
    assert push._sub_claim() == "mailto:ops@example.com"

    monkeypatch.setattr(settings, "vapid_admin_email", "https://example.com/contact")
    assert push._sub_claim() == "https://example.com/contact"


def test_a_missing_contact_still_produces_a_valid_claim(monkeypatch):
    monkeypatch.setattr(settings, "vapid_admin_email", "")
    assert push._sub_claim().startswith("mailto:")


# --- delivery ----------------------------------------------------------------


def test_each_send_gets_its_own_claims_dict(monkeypatch):
    """pywebpush MUTATES the claims it is handed, injecting `aud` and `exp`.
    A shared dict carries the first push service's audience and a stale expiry
    into every later send — so the second device silently stops working."""
    _db(monkeypatch, _rows("https://web.push.apple.com/a", "https://fcm.googleapis.com/b"))
    seen = []

    def fake_webpush(**kwargs):
        claims = kwargs["vapid_claims"]
        seen.append(id(claims))
        claims["aud"] = "https://web.push.apple.com"  # what the real library does

    with patch("pywebpush.webpush", side_effect=fake_webpush):
        delivered = push.send_to_user(uuid.uuid4(), "t", "b")

    assert delivered == 2
    assert len(set(seen)) == 2, "the two sends shared one claims dict"


def test_the_payload_carries_what_the_service_worker_reads(monkeypatch):
    _db(monkeypatch, _rows("https://web.push.apple.com/a"))
    captured = {}

    with patch("pywebpush.webpush", side_effect=lambda **kw: captured.update(kw)):
        push.send_to_user(uuid.uuid4(), "Fix ready", "body text", "/issues/1", "fix-proposed-9")

    payload = json.loads(captured["data"])
    assert payload["title"] == "Fix ready"
    assert payload["url"] == "/issues/1"
    assert payload["tag"] == "fix-proposed-9"
    assert payload["icon"] == "/icon-192.png"


def test_a_410_removes_the_subscription(monkeypatch):
    """The browser revoked it. Keeping the row means paying for a dead
    endpoint on every notification, forever."""
    conn = _db(monkeypatch, _rows("https://web.push.apple.com/gone"))

    with patch("pywebpush.webpush", side_effect=_webpush_error(410)):
        assert push.send_to_user(uuid.uuid4(), "t", "b") == 0

    deletes = [sql for sql, _ in conn.cur.executed if "DELETE" in sql]
    assert deletes, "an expired subscription must be forgotten"


def test_a_403_keeps_the_subscription_and_logs_the_body(monkeypatch, caplog):
    """403 is a signing problem, not a dead device. Deleting the row would
    destroy the evidence AND the subscription. The body is where Apple puts
    `BadJwtToken`; dropping it makes the next failure undiagnosable."""
    conn = _db(monkeypatch, _rows("https://web.push.apple.com/x"))

    with patch("pywebpush.webpush", side_effect=_webpush_error(403, "BadJwtToken")):
        assert push.send_to_user(uuid.uuid4(), "t", "b") == 0

    assert not [sql for sql, _ in conn.cur.executed if "DELETE" in sql]
    assert "BadJwtToken" in caplog.text
    assert "status=403" in caplog.text


def test_one_dead_device_does_not_stop_the_others(monkeypatch):
    _db(monkeypatch, _rows("https://web.push.apple.com/dead", "https://fcm.googleapis.com/alive"))
    outcomes = [_webpush_error(410), None]

    def fake_webpush(**_kwargs):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    with patch("pywebpush.webpush", side_effect=fake_webpush):
        assert push.send_to_user(uuid.uuid4(), "t", "b") == 1


def test_an_unconfigured_deployment_sends_nothing_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    with patch("pywebpush.webpush") as sender:
        assert push.send_to_user(uuid.uuid4(), "t", "b") == 0
    sender.assert_not_called()


def test_a_database_failure_degrades_rather_than_failing_the_council_run(monkeypatch):
    def explode():
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.sync_db.connection", explode)
    assert push.send_to_user(uuid.uuid4(), "t", "b") == 0


# --- audience ----------------------------------------------------------------


def test_a_repo_resolves_to_its_organization(monkeypatch):
    """A finding belongs to a repository, which belongs to an org. Routing to
    whoever triggered the scan would mean the only person who ever hears about
    a defect is the one already watching."""
    org_id = uuid.uuid4()
    monkeypatch.setattr("app.sync_db.connection", lambda: _Conn([(org_id,)]))
    with patch.object(push, "send_to_org", return_value=3) as to_org:
        assert push.send_for_repo(uuid.uuid4(), "t", "b") == 3
    assert to_org.call_args.args[0] == org_id


def test_a_repo_with_no_organization_pushes_to_nobody(monkeypatch):
    monkeypatch.setattr("app.sync_db.connection", lambda: _Conn([(None,)]))
    with patch.object(push, "send_to_org") as to_org:
        assert push.send_for_repo(uuid.uuid4(), "t", "b") == 0
    to_org.assert_not_called()


# --- provider identification -------------------------------------------------


def test_the_provider_is_recorded_so_a_platform_failure_is_a_query():
    assert push.provider_for("https://web.push.apple.com/abc") == "apple"
    assert push.provider_for("https://fcm.googleapis.com/abc") == "fcm"
    assert push.provider_for("https://updates.push.services.mozilla.com/x") == "mozilla"
    assert push.provider_for("not a url at all") == ""

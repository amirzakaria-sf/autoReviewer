import hashlib
import hmac
import time

from app.integrations.slack_client import verify_signature

SIGNING_SECRET = "test-signing-secret"


def _sign(timestamp: str, body: str, secret: str = SIGNING_SECRET) -> str:
    basestring = f"v0:{timestamp}:{body}"
    return "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_verifies_true():
    timestamp = str(int(time.time()))
    body = "payload=%7B%22type%22%3A%22block_actions%22%7D"
    signature = _sign(timestamp, body)

    headers = {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}
    assert verify_signature(headers, body, SIGNING_SECRET) is True


def test_tampered_body_verifies_false():
    timestamp = str(int(time.time()))
    body = "payload=original"
    signature = _sign(timestamp, body)

    tampered_headers = {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}
    assert verify_signature(tampered_headers, "payload=tampered", SIGNING_SECRET) is False


def test_stale_timestamp_verifies_false_even_with_correct_signature():
    stale_timestamp = str(int(time.time()) - 600)  # 10 minutes old
    body = "payload=whatever"
    signature = _sign(stale_timestamp, body)

    headers = {"X-Slack-Request-Timestamp": stale_timestamp, "X-Slack-Signature": signature}
    assert verify_signature(headers, body, SIGNING_SECRET) is False


def test_missing_headers_verify_false():
    assert verify_signature({}, "body", SIGNING_SECRET) is False

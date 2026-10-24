"""Email-ownership code for the accept-invite page.

The invite link already proves the invitee can read their inbox; the code is the second, deliberate step
Whip and Lumen have (verify the address, then choose a password), so all three products onboard the same way and a
forwarded or prefetched link alone is not enough to take the account.

Rules (each one tested in tests/test_access_otp.py):
  * 6 digits from `secrets`, valid 10 minutes, at most 5 wrong guesses, then it is dead and a new one must be asked for;
  * only an HMAC is stored -- keyed with the session secret and bound to the access-request row, so one row's code never
    verifies against another and a database leak does not reveal a live code;
  * a new code is refused for 60 seconds after the last one (no mail bombing through the page);
  * a verified code is good for 30 minutes to finish the password step, and is single-use with the invite itself.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import AccessRequest

OTP_TTL = timedelta(minutes=10)
OTP_RESEND_AFTER = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5
VERIFIED_TTL = timedelta(minutes=30)


def otp_required(*, is_first_user: bool) -> bool:
    """Off when disabled, when mail cannot be sent (a code nobody can receive would lock people out), and for the
    first account on a deployment, where there is no operator to rescue a mail problem."""
    from app.integrations import email_client

    return bool(settings.invite_require_otp) and email_client.smtp_configured() and not is_first_user


def _digest(row_id, code: str) -> str:
    key = settings.session_secret.encode("utf-8")
    return hmac.new(key, f"invite-otp:{row_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def seconds_until_resend(row: AccessRequest, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if row.otp_sent_at is None:
        return 0
    wait = (row.otp_sent_at + OTP_RESEND_AFTER) - now
    return max(0, int(wait.total_seconds()) + (1 if wait.total_seconds() % 1 else 0))


def issue(row: AccessRequest, now: datetime | None = None) -> str:
    """Mutates `row` (caller commits) and returns the plaintext code to mail."""
    now = now or datetime.now(timezone.utc)
    code = new_code()
    row.otp_hash = _digest(row.id, code)
    row.otp_expires_at = now + OTP_TTL
    row.otp_sent_at = now
    row.otp_attempts = 0
    row.otp_verified_at = None
    return code


def check(row: AccessRequest, code: str, now: datetime | None = None) -> str:
    """'ok' | 'none' (no live code) | 'expired' | 'locked' | 'wrong'. Mutates `row` (caller commits)."""
    now = now or datetime.now(timezone.utc)
    if not row.otp_hash or row.otp_expires_at is None:
        return "none"
    if (row.otp_attempts or 0) >= OTP_MAX_ATTEMPTS:
        return "locked"
    if now > row.otp_expires_at:
        return "expired"
    candidate = _digest(row.id, str(code or "").strip())
    if hmac.compare_digest(candidate, row.otp_hash):
        row.otp_hash = None
        row.otp_expires_at = None
        row.otp_attempts = 0
        row.otp_verified_at = now
        return "ok"
    row.otp_attempts = (row.otp_attempts or 0) + 1
    if row.otp_attempts >= OTP_MAX_ATTEMPTS:
        row.otp_hash = None  # burnt: a sixth guess cannot succeed, and a fresh code is needed
        return "locked"
    return "wrong"


def is_verified(row: AccessRequest, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return row.otp_verified_at is not None and now <= row.otp_verified_at + VERIFIED_TTL


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"

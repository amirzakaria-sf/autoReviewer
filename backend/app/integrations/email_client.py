"""Transactional email (plan.md §6.4): the same event set Slack gets, plus a
signed, single-use, short-lived magic link so a fix can be approved or
rejected straight from an inbox -- exactly as auditable as a Slack button
click, and just as unforgeable.

SMTP transport mirrors jobFlowAuto's app/lib/email/transport.ts: Brevo relay,
STARTTLS on 587, same verified sender domain (zakarias.in) -- reusing a
deliverability-proven relay rather than standing up a fresh one for a
single-operator tool.

Single-use is enforced by resolve_approval's OWN idempotency guard (it only
acts when Fix.status == AWAITING_APPROVAL, otherwise returns
already_handled) -- there is no separate token-use ledger, because the
approval flow was already idempotent-by-construction for exactly this reason
(Slack button, dashboard click, and a GitHub /reject comment already share
this same guarantee).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings
from app.integrations import email_templates

logger = logging.getLogger("whipguard.email_client")

ACTION_TOKEN_TTL_SECONDS = 7 * 24 * 3600  # a week -- long enough to matter from an inbox


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_key)


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    """Never raises -- a mail failure must not take down a graph node any
    more than a Slack failure does (see every `except Exception` around
    slack_client.post_message elsewhere in this codebase)."""
    if not smtp_configured():
        logger.warning('SMTP not configured; dropped "%s" to %s', subject, _mask(to))
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    message["To"] = to
    message.attach(MIMEText(text, "plain"))
    message.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_key)
            server.sendmail(settings.email_from_address, [to], message.as_string())
        logger.info('sent "%s" to %s', subject, _mask(to))
        return True
    except Exception:
        logger.exception('failed sending "%s" to %s', subject, _mask(to))
        return False


def _signing_key() -> bytes:
    return settings.session_secret.encode("utf-8")


def sign_action_token(fix_id: str, action: str) -> str:
    """A single-use-by-idempotency, short-lived, signed token binding one
    fix to one action -- clicking it is exactly as auditable as a Slack
    button click (plan.md §6.4). HMAC over the payload, not a bare signed
    cookie: this travels in a URL an email client may prefetch or log, so it
    must be self-contained and independently verifiable, never a session
    reference."""
    payload = {"fix_id": fix_id, "action": action, "exp": int(time.time()) + ACTION_TOKEN_TTL_SECONDS}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    signature = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_action_token(token: str) -> dict | None:
    """Returns {"fix_id", "action"} if the token is well-formed, correctly
    signed, and not expired -- None for anything else (tampered, malformed,
    expired). Constant-time signature comparison -- a timing side-channel on
    a token that authorizes a real state change is a real vulnerability, not
    a theoretical one."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None

    if not isinstance(payload, dict) or payload.get("action") not in ("approve", "reject"):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None

    return {"fix_id": payload["fix_id"], "action": payload["action"]}


def _action_url(fix_id: str, action: str) -> str:
    token = sign_action_token(fix_id, action)
    return f"{email_templates.public_url()}/api/email/action?token={token}"


# The builders live in email_templates.py (one shell, everything escaped). These names are kept so no caller changes.

def build_fix_proposed_email(issue_title: str, category: str, score: int, pr_url: str, fix_id: str) -> tuple[str, str, str]:
    return email_templates.fix_proposed_email(
        issue_title, category, score, pr_url, _action_url(fix_id, "approve"), _action_url(fix_id, "reject")
    )


def build_status_email(issue_title: str, status_label: str, detail: str = "") -> tuple[str, str, str]:
    return email_templates.status_email(issue_title, status_label, detail)


def build_invite_email(name: str, invite_token: str) -> tuple[str, str, str]:
    return email_templates.invite_email(name, invite_token)


def build_org_invite_email(name: str, org_name: str, inviter: str, accept_url: str) -> tuple[str, str, str]:
    """An invitation to join a team, not an approved access request. Says who invited them and to which organization --
    an unattributed "set a password" mail from a product nobody signed up for reads as phishing."""
    return email_templates.org_invite_email(name, org_name, inviter, accept_url)


def build_access_declined_email(name: str, reason: str) -> tuple[str, str, str]:
    return email_templates.access_declined_email(name, reason)


def build_otp_email(name: str, code: str) -> tuple[str, str, str]:
    return email_templates.otp_email(name, code)


def build_request_received_email(name: str, reason: str) -> tuple[str, str, str]:
    return email_templates.request_received_email(name, reason)


def build_admin_new_request_email(name: str, email: str, reason: str) -> tuple[str, str, str]:
    return email_templates.admin_new_request_email(name, email, reason, f"{email_templates.public_url()}/admin/users")


def build_welcome_email(name: str) -> tuple[str, str, str]:
    return email_templates.welcome_email(name)

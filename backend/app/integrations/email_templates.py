"""One HTML shell for every WhipGuard email, and the builders that fill it.

Why this exists: the mails used to be f-strings with the user's name, an issue title or a
rejection reason pasted straight into HTML -- an attacker-chosen name of `<a href=...>` went out
under WhipGuard's sender. Everything user-supplied now goes through `esc` here, the layout is one
table-based shell (the only thing mail clients render reliably) with inline CSS, and every
builder returns `(subject, html, text)` so the plain-text part is never forgotten.

`email_client` re-exports the old `build_*` names so no caller changes.
"""

from __future__ import annotations

from html import escape

from app.config import settings

PUBLIC_URL = "https://whip-guard.zakarias.in"

_INK = "#0b1020"
_ACCENT = "#5b7cfa"
_TEXT = "#1e293b"
_MUTED = "#64748b"
_LINE = "#e2e8f0"


def esc(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def public_url() -> str:
    return (settings.public_base_url or PUBLIC_URL).rstrip("/")


def button(href: str, label: str, color: str = _ACCENT, text_color: str = "#ffffff") -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 8px 0;"><tr>'
        f'<td style="border-radius:8px;background:{color};">'
        f'<a href="{esc(href)}" style="display:inline-block;padding:13px 26px;font:600 15px/1 Helvetica,Arial,sans-serif;'
        f'color:{text_color};text-decoration:none;">{esc(label)}</a></td></tr></table>'
    )


def heading(text: str) -> str:
    return f'<h1 style="margin:0 0 12px 0;font:700 22px/1.3 Helvetica,Arial,sans-serif;color:{_TEXT};">{esc(text)}</h1>'


def para(text_html: str, *, muted: bool = True) -> str:
    color = _MUTED if muted else _TEXT
    return f'<p style="margin:0 0 14px 0;font:400 15px/1.6 Helvetica,Arial,sans-serif;color:{color};">{text_html}</p>'


def quote(text: str) -> str:
    return (
        f'<div style="margin:6px 0 16px 0;padding:12px 14px;background:#f8fafc;border:1px solid {_LINE};border-radius:8px;'
        f'font:400 14px/1.6 Helvetica,Arial,sans-serif;color:{_TEXT};white-space:pre-wrap;">{esc(text)}</div>'
    )


def code_block(code: str) -> str:
    return (
        f'<div style="margin:20px 0;padding:16px;background:#f1f5f9;border:1px solid {_LINE};border-radius:10px;text-align:center;'
        f"font:700 30px/1 'SF Mono',Menlo,Consolas,monospace;letter-spacing:10px;color:{_INK};\">{esc(code)}</div>"
    )


def shell(title: str, body_html: str, footer: str = "") -> str:
    footer_line = footer or "You are receiving this because this address was used on WhipGuard."
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid {_LINE};">
<tr><td style="background:{_INK};padding:22px 32px;border-bottom:3px solid {_ACCENT};">
<span style="font:700 14px/1 'Courier New',monospace;letter-spacing:4px;color:#ffffff;">WHIPGUARD</span></td></tr>
<tr><td style="padding:32px;">{body_html}</td></tr>
<tr><td style="padding:18px 32px;background:#f8fafc;border-top:1px solid {_LINE};font:400 12px/1.5 Helvetica,Arial,sans-serif;color:#94a3b8;">{esc(footer_line)}</td></tr>
</table></td></tr></table></body></html>"""


Mail = tuple[str, str, str]


def _first(name: str) -> str:
    return (name or "").strip() or "there"


# --- access lifecycle ------------------------------------------------------------------------------------------------

def otp_email(name: str, code: str, *, minutes: int = 10) -> Mail:
    subject = "Your WhipGuard verification code"
    body = (
        heading("Verify your email")
        + para(f"Hi {esc(_first(name))}, enter this code to finish setting up your WhipGuard account. It expires in {minutes} minutes.")
        + code_block(code)
        + para("Never share this code. WhipGuard will not ask you for it.")
    )
    text = f"Your WhipGuard verification code is {code}. It expires in {minutes} minutes. Never share it."
    return subject, shell(subject, body, "Sent because someone is setting up a WhipGuard account with this address."), text


def request_received_email(name: str, reason: str) -> Mail:
    subject = "We received your WhipGuard access request"
    shown = reason if len(reason) <= 400 else reason[:399] + "…"
    body = (
        heading("Request received")
        + para(f"Hi {esc(_first(name))}, thanks for asking for access to WhipGuard. A person will review your request.")
        + quote(shown)
        + para("When it has been reviewed you will get another email with the next step. There is nothing else to do now.")
    )
    text = "We received your WhipGuard access request. A person will review it and you will get another email with the next step."
    return subject, shell(subject, body, "Sent because this address was entered on the WhipGuard access request form."), text


def admin_new_request_email(name: str, email: str, reason: str, review_url: str) -> Mail:
    subject = f"WhipGuard: new access request — {name}"
    body = (
        heading("New access request")
        + para(f'<strong style="color:{_TEXT};">{esc(name)}</strong> &lt;{esc(email)}&gt; asked for access.')
        + quote(reason)
        + button(review_url, "Review request")
    )
    text = f"{name} <{email}> requested access.\nReason: {reason}\nReview: {review_url}"
    return subject, shell(subject, body, "Sent to the WhipGuard operator address (NOTIFY_EMAIL)."), text


def invite_email(name: str, invite_token: str) -> Mail:
    accept_url = f"{public_url()}/accept-invite?token={invite_token}"
    subject = "You're in — set up your WhipGuard account"
    body = (
        heading(f"Welcome, {_first(name)}")
        + para("Your access request was approved. Verify your email and choose a password to finish creating your account.")
        + button(accept_url, "Set up your account")
        + para("This link is single-use and expires in 7 days.")
    )
    text = f"Welcome, {_first(name)}\n\nYour access request was approved. Set up your account here:\n{accept_url}\n\nSingle-use, expires in 7 days."
    return subject, shell(subject, body, "Sent because an administrator approved your WhipGuard access request."), text


def access_declined_email(name: str, reason: str) -> Mail:
    subject = "WhipGuard access request update"
    body = heading("Your request was not approved") + para(
        f"Hi {esc(_first(name))}, after review we are not able to give you access to WhipGuard right now."
    )
    if reason:
        body += para("<strong>Note from the reviewer</strong>", muted=False) + quote(reason)
    body += para("This is not permanent. You are welcome to send a new request later with more detail.")
    text = f"Hi {_first(name)}, your request for access to WhipGuard was not approved." + (f"\nReason: {reason}" if reason else "")
    return subject, shell(subject, body, "Sent because this address was used to request access to WhipGuard."), text


def welcome_email(name: str) -> Mail:
    subject = "Welcome to WhipGuard"
    login = f"{public_url()}/login"
    body = (
        heading(f"Welcome, {_first(name)}")
        + para("Your WhipGuard account is ready. It stays active until an administrator removes it.")
        + button(login, "Sign in")
    )
    text = f"Your WhipGuard account is ready. Sign in at {login}."
    return subject, shell(subject, body, "Sent because an account was just created with this address."), text


# --- product notifications (unchanged in content, now escaped) -------------------------------------------------------

def fix_proposed_email(issue_title: str, category: str, score: int, pr_url: str, approve_url: str, reject_url: str) -> Mail:
    subject = f'WhipGuard: fix proposed for "{issue_title}"'
    body = (
        heading("Fix proposed")
        + para(esc(issue_title), muted=False)
        + para(f"Category: <b>{esc(category)}</b> &middot; Resolution confidence: <b>{esc(score)}/100</b>")
        + para(f'<a href="{esc(pr_url)}" style="color:{_ACCENT};">View the pull request &rarr;</a>')
        + '<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding-right:12px;">{button(approve_url, "Approve", "#16a34a")}</td>'
        f'<td>{button(reject_url, "Reject", "#dc2626")}</td></tr></table>'
        + para("These links are single-use and expire in 7 days. Whoever acts first — here, in Slack, or on the dashboard — wins; the others just reflect it.")
    )
    text = (
        f'WhipGuard: fix proposed for "{issue_title}"\nCategory: {category}. Resolution confidence: {score}/100.\n'
        f"PR: {pr_url}\n\nApprove: {approve_url}\nReject: {reject_url}\n"
    )
    return subject, shell(subject, body, "Sent to the WhipGuard operator address (NOTIFY_EMAIL)."), text


def status_email(issue_title: str, status_label: str, detail: str = "") -> Mail:
    subject = f'WhipGuard: {status_label} — "{issue_title}"'
    body = heading(status_label) + para(esc(issue_title), muted=False)
    if detail:
        body += para(esc(detail))
    body += button(f"{public_url()}/dashboard", "Open the dashboard")
    text = f'WhipGuard: {status_label} — "{issue_title}"\n{detail}\n\n{public_url()}/dashboard'
    return subject, shell(subject, body, "Sent to the WhipGuard operator address (NOTIFY_EMAIL)."), text


def org_invite_email(name: str, org_name: str, inviter: str, accept_url: str) -> Mail:
    subject = f"{inviter} invited you to {org_name} on WhipGuard"
    body = (
        heading(f"Join {org_name}")
        + para(
            f"Hi {esc(_first(name))}, <b>{esc(inviter)}</b> invited you to the <b>{esc(org_name)}</b> organization on WhipGuard, "
            "where bugs found in your repositories are reviewed and fixes approved."
        )
        + button(accept_url, "Accept the invitation", "#ffb224", "#1a1a1a")
        + para("This link expires in 7 days. If you were not expecting it, ignore this email.")
    )
    text = (
        f"Hi {_first(name)},\n\n{inviter} invited you to the {org_name} organization on WhipGuard.\n\n"
        f"Accept here:\n{accept_url}\n\nExpires in 7 days. If you were not expecting this, ignore it."
    )
    return subject, shell(subject, body, "Sent because someone invited this address to WhipGuard."), text

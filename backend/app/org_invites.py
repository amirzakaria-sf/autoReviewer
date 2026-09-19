"""Inviting someone into an organization.

Distinct from an access request on purpose. An access request is a stranger
asking the PLATFORM for an account, decided by a system admin. This is an org
admin adding a colleague to a team that already exists -- a different actor,
a different decision, and a different destination.

Conflating them is exactly what left the onboarding flow broken: approving an
access request created an active user who belonged to no organization and
landed on a page telling them so.

Two cases, one endpoint:
  * the invitee has no account -> they set a password and join in one step
  * the invitee already has one -> they just join, no password prompt
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.enums import OrgRole, Seniority, UserRole, UserStatus
from app.models import OrgInvite, User

logger = logging.getLogger("whipguard.org_invites")


class InviteError(Exception):
    """Phrased for whoever is reading it -- an admin or an invitee."""


def _normalise(email: str) -> str:
    return (email or "").strip().lower()


async def create_invite(
    db,
    *,
    org_id,
    email: str,
    name: str = "",
    role: OrgRole = OrgRole.MEMBER,
    seniority: Seniority = Seniority.SDE2,
    designation_keys: list[str] | None = None,
    invited_by=None,
) -> OrgInvite:
    email = _normalise(email)
    if "@" not in email:
        raise InviteError("That does not look like an email address.")

    existing = (
        await db.execute(
            select(OrgInvite).where(
                OrgInvite.org_id == org_id,
                OrgInvite.email == email,
                OrgInvite.status == "pending",
            )
        )
    ).scalars().first()
    if existing is not None:
        # Re-inviting is a normal thing to do when the first email was missed.
        # Refresh the terms and hand back the same row rather than failing on
        # a unique index the admin cannot see.
        existing.role = role
        existing.seniority = seniority
        existing.designation_keys = designation_keys or []
        existing.name = name or existing.name
        await db.commit()
        return existing

    invite = OrgInvite(
        org_id=org_id,
        email=email,
        name=name.strip(),
        role=role,
        seniority=seniority,
        designation_keys=designation_keys or [],
        invited_by=invited_by,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


async def revoke_invite(db, invite_id) -> None:
    invite = await db.get(OrgInvite, invite_id)
    if invite is None:
        raise InviteError("That invitation no longer exists.")
    if invite.status != "pending":
        raise InviteError(f"That invitation is already {invite.status}.")
    invite.status = "revoked"
    await db.commit()


async def load_pending(db, token: str) -> OrgInvite:
    """Resolve a token to a live invitation, or say precisely why not.

    Every rejection names its own reason. "Invalid link" for a revoked invite,
    an expired one and a typo alike leaves the invitee with nothing to act on.
    """
    from app.security import decode_org_invite_token

    invite_id = decode_org_invite_token(token)
    if invite_id is None:
        raise InviteError("This invitation link is invalid or has expired.")

    invite = await db.get(OrgInvite, invite_id)
    if invite is None:
        raise InviteError("This invitation no longer exists.")
    if invite.status == "accepted":
        raise InviteError("This invitation has already been used -- sign in instead.")
    if invite.status == "revoked":
        raise InviteError("This invitation was withdrawn by the organization's admin.")
    return invite


async def accept(db, *, invite: OrgInvite, password: str) -> User:
    """Join the org, creating the account first if there is not one yet.

    Idempotent against a double submit: the membership insert is itself
    idempotent (app/orgs.py's add_member), and the invite is marked accepted
    in the same transaction.
    """
    import asyncio

    from app import orgs
    from app.security import hash_password

    user = (await db.execute(select(User).where(User.email == invite.email))).scalars().first()

    if user is None:
        if len(password or "") < 8:
            raise InviteError("Choose a password of at least 8 characters.")
        first, _, last = (invite.name or "").partition(" ")
        user = User(
            email=invite.email,
            password_hash=hash_password(password),
            # A member of an org, not a platform administrator. The two roles
            # are unrelated: org_admin governs one organization, User.role
            # governs this deployment.
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
            first_name=first or None,
            last_name=last or None,
        )
        db.add(user)
        # Committed here, not flushed: add_member below runs on a SEPARATE
        # synchronous connection (app/orgs.py), which cannot see a row this
        # session has not committed -- it fails on the foreign key instead.
        #
        # The cost is that a failure between the two leaves an account with no
        # membership. Recoverable rather than stuck: add_member is idempotent
        # and the invitation is still pending, so the same link works again.
        await db.commit()
        await db.refresh(user)
    elif user.status != UserStatus.ACTIVE:
        raise InviteError("This account is deactivated. Ask a system admin to restore it first.")

    # Propagated as an InviteError so the /join page shows the reason rather
    # than a generic failure -- "you already belong to X" is actionable.
    try:
        await asyncio.to_thread(
            orgs.add_member,
            org_id=invite.org_id,
            user_id=user.id,
            role=invite.role,
            seniority=invite.seniority,
            designation_keys=list(invite.designation_keys or []),
        )
    except orgs.OrgError as error:
        raise InviteError(str(error)) from error

    invite.status = "accepted"
    invite.accepted_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("org invite %s accepted by %s", invite.id, invite.email)
    return user


async def pending_for_org(db, org_id) -> list[dict]:
    rows = (
        await db.execute(
            select(OrgInvite)
            .where(OrgInvite.org_id == org_id, OrgInvite.status == "pending")
            .order_by(OrgInvite.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "email": row.email,
            "name": row.name,
            "role": row.role.value,
            "seniority": row.seniority.value,
            "designations": list(row.designation_keys or []),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

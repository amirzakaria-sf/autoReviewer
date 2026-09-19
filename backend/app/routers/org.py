"""Org administration: members, roles, designations, routing.

Every write here is org-admin only. These settings decide who gets woken up
for a production defect, so a member quietly reassigning a category to
themselves -- or away from themselves -- is a real problem, not a
hypothetical one.

Read access is deliberately open to any member: a developer should be able to
see why an issue reached them without asking an admin.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app import org_invites, orgs
from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.enums import OrgRole, Seniority
from app.integrations import email_client
from app.models import OrgInvite, User
from app.security import sign_org_invite_token

router = APIRouter(prefix="/api/org")
logger = logging.getLogger("whipguard.org.router")


async def _current_org(user: User) -> dict:
    """The caller's org. Single-membership today; when a person can belong to
    several, this becomes an explicit selection rather than a guess."""
    memberships = await asyncio.to_thread(orgs.orgs_for_user, user.id)
    if not memberships:
        raise HTTPException(404, "You do not belong to an organization yet.")
    return memberships[0]


async def _require_org_admin(user: User) -> dict:
    org = await _current_org(user)
    # A system admin can administer any org; an org admin only their own.
    if org["role"] != OrgRole.ORG_ADMIN.value and user.role.value != "admin":
        raise HTTPException(403, "Only an organization admin can change this.")
    return org


@router.get("")
async def my_org(user: User = Depends(current_user)):
    org = await _current_org(user)
    return {
        **org,
        "members": await asyncio.to_thread(orgs.members, org["id"]),
        "designations": await asyncio.to_thread(orgs.designations, org["id"]),
        "routing_rules": await asyncio.to_thread(orgs.routing_rules, org["id"]),
    }


@router.patch("/members/{member_id}")
async def update_member(member_id: uuid.UUID, body: dict, user: User = Depends(current_user)):
    """Role, seniority and designations are three independent edits. Sending
    one must never reset the other two."""
    org = await _require_org_admin(user)

    if "role" in body:
        try:
            role = OrgRole(body["role"])
        except ValueError:
            raise HTTPException(400, f"role must be one of: {[r.value for r in OrgRole]}") from None

        if role != OrgRole.ORG_ADMIN:
            # Removing the last admin leaves an org nobody can administer,
            # including to undo this.
            current = await asyncio.to_thread(orgs.members, org["id"])
            admins = [m for m in current if m["role"] == OrgRole.ORG_ADMIN.value]
            if len(admins) <= 1 and any(m["member_id"] == str(member_id) for m in admins):
                raise HTTPException(409, "This is the only admin — promote someone else first.")
        await asyncio.to_thread(orgs.set_member_role, member_id, role)

    if "seniority" in body:
        try:
            await asyncio.to_thread(orgs.set_member_seniority, member_id, Seniority(body["seniority"]))
        except ValueError:
            raise HTTPException(400, f"seniority must be one of: {[s.value for s in Seniority]}") from None

    if "designations" in body:
        keys = [str(key) for key in (body["designations"] or [])]
        known = {d["key"] for d in await asyncio.to_thread(orgs.designations, org["id"])}
        unknown = set(keys) - known
        if unknown:
            raise HTTPException(400, f"unknown designation(s): {sorted(unknown)}")
        await asyncio.to_thread(orgs.set_member_designations, member_id, keys, org["id"])

    return {"ok": True, "members": await asyncio.to_thread(orgs.members, org["id"])}


@router.post("/identity-links")
async def add_identity_link(body: dict, user: User = Depends(current_user)):
    """Map a git identity to a person, so blame can resolve to an assignee.

    The messy part of attribution, and the reason it is manual: GitHub
    noreply addresses and personal-versus-work email cannot be inferred
    reliably, and guessing wrong assigns someone else's work to you.
    """
    org = await _require_org_admin(user)
    provider = str(body.get("provider", "git-email"))
    external_id = str(body.get("external_id", "")).strip()
    target_user = str(body.get("user_id", "")).strip()
    if not external_id or not target_user:
        raise HTTPException(400, "Both user_id and external_id are required.")
    if provider not in {"git-email", "github", "slack"}:
        raise HTTPException(400, "provider must be git-email, github or slack")

    # Validated rather than passed straight through. A malformed id reached
    # the insert and came back as a 500 with no explanation (hit for real:
    # a shell variable expanded to "1000" instead of a uuid).
    try:
        target_uuid = uuid.UUID(target_user)
    except ValueError as error:
        raise HTTPException(400, "user_id must be a valid user identifier.") from error

    # And it has to be someone in THIS organization. Without the check an
    # admin could map a git author to a user in another org, and every
    # finding that author touched would be assigned across the tenancy
    # boundary -- the one thing repos.org_id exists to prevent.
    roster = await asyncio.to_thread(orgs.members, org["id"])
    if not any(member["user_id"] == str(target_uuid) for member in roster):
        raise HTTPException(400, "That person is not in this organization.")

    await asyncio.to_thread(orgs.link_identity, org["id"], target_uuid, provider, external_id)
    return {"ok": True, "links": await asyncio.to_thread(orgs.identity_links, org["id"])}


@router.post("/preview-assignment")
async def preview_assignment(body: dict, user: User = Depends(current_user)):
    """Answer "who would get this?" without waiting for a real finding.

    Routing rules are the kind of configuration nobody trusts until they have
    watched it decide something, so this makes the decision inspectable
    before it matters.
    """
    org = await _current_org(user)
    return await asyncio.to_thread(
        orgs.assign_issue,
        org_id=org["id"],
        category=str(body.get("category", "ui")),
        severity=int(body.get("severity", 2)),
        author_external_id=str(body.get("author", "")),
    )


# --- membership ---------------------------------------------------------------


class InviteIn(BaseModel):
    # Validated with a pattern rather than pydantic's EmailStr, which pulls in
    # the email-validator package. What matters here is catching a typo before
    # an invitation is sent to nobody; full RFC conformance is not worth a
    # dependency in the request path.
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default="", max_length=120)
    role: str = Field(default="member")
    seniority: str = Field(default="sde2")
    designations: list[str] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("that does not look like an email address")
        return value


@router.get("/invites")
async def list_invites(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    org = await _require_org_admin(user)
    return await org_invites.pending_for_org(db, uuid.UUID(org["id"]))


@router.post("/invites")
async def invite_member(
    body: InviteIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invite one person into this organization.

    The invite carries the role, seniority and designations chosen here, so
    accepting produces a configured member rather than someone an admin then
    has to set up a second time.
    """
    org = await _require_org_admin(user)
    try:
        invite = await org_invites.create_invite(
            db,
            org_id=uuid.UUID(org["id"]),
            email=str(body.email),
            name=body.name,
            role=OrgRole(body.role),
            seniority=Seniority(body.seniority),
            designation_keys=body.designations,
            invited_by=user.id,
        )
    except ValueError as error:
        raise HTTPException(400, f"Unknown role or seniority: {error}") from error
    except org_invites.InviteError as error:
        raise HTTPException(400, str(error)) from error

    token = sign_org_invite_token(invite.id)
    link = f"{settings.public_base_url}/join?token={token}"

    delivered = False
    if email_client.smtp_configured():
        try:
            subject, html, text = email_client.build_org_invite_email(
                invite.name or str(body.email).split("@")[0], org["name"], user.email, link
            )
            await asyncio.to_thread(email_client.send_email, str(body.email), subject, html, text)
            delivered = True
        except Exception:
            logger.exception("could not send the org invite email for %s", invite.id)

    return {
        "ok": True,
        "invite": {
            "id": str(invite.id),
            "email": invite.email,
            "name": invite.name,
            "role": invite.role.value,
            "seniority": invite.seniority.value,
            "designations": list(invite.designation_keys or []),
        },
        "email_sent": delivered,
        # Returned so an admin can pass the link on by hand when SMTP is not
        # configured -- otherwise the invitation exists and reaches nobody,
        # which looks exactly like the feature being broken.
        "link": link,
    }


@router.delete("/invites/{invite_id}")
async def revoke_invite(
    invite_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _require_org_admin(user)
    invite = await db.get(OrgInvite, invite_id)
    if invite is None or str(invite.org_id) != org["id"]:
        raise HTTPException(404, "That invitation is not in your organization.")
    try:
        await org_invites.revoke_invite(db, invite_id)
    except org_invites.InviteError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@router.delete("/members/{member_id}")
async def remove_member(member_id: uuid.UUID, user: User = Depends(current_user)):
    org = await _require_org_admin(user)
    existing = await asyncio.to_thread(orgs.members, org["id"])
    if not any(m["member_id"] == str(member_id) for m in existing):
        raise HTTPException(404, "That member is not in your organization.")
    try:
        await asyncio.to_thread(orgs.remove_member, member_id)
    except orgs.OrgError as error:
        # 409, not 400: the request was well-formed and the refusal is about
        # the org's state (the last admin), which the UI explains rather than
        # treating as a validation error.
        raise HTTPException(409, str(error)) from error
    return {"ok": True, "members": await asyncio.to_thread(orgs.members, org["id"])}


# --- configuration: designations, routing, identities ---------------------------


class DesignationIn(BaseModel):
    key: str = Field(min_length=1, max_length=48)
    label: str = Field(default="", max_length=64)


class RoutingRuleIn(BaseModel):
    designation_key: str = Field(min_length=1, max_length=48)
    escalate_at_severity: int = Field(default=4, ge=1, le=5)
    min_seniority: str = Field(default="sde3")


@router.post("/designations")
async def upsert_designation(body: DesignationIn, user: User = Depends(current_user)):
    """Add an area of expertise, or rename one.

    The key is what routing rules and member assignments reference, so it is
    slugified and permanent; renaming changes the label only.
    """
    org = await _require_org_admin(user)
    try:
        designation = await asyncio.to_thread(
            orgs.upsert_designation, org["id"], body.key, body.label
        )
    except orgs.OrgError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "designation": designation, "designations": await asyncio.to_thread(orgs.designations, org["id"])}


@router.delete("/designations/{key}")
async def delete_designation(key: str, user: User = Depends(current_user)):
    org = await _require_org_admin(user)
    try:
        await asyncio.to_thread(orgs.delete_designation, org["id"], key)
    except orgs.OrgError as error:
        # 409: the request is fine, the org's own configuration refuses it --
        # something still routes there.
        raise HTTPException(409, str(error)) from error
    return {"ok": True, "designations": await asyncio.to_thread(orgs.designations, org["id"])}


@router.put("/routing-rules/{category}")
async def set_routing_rule(category: str, body: RoutingRuleIn, user: User = Depends(current_user)):
    """Which designation owns a category, and the seniority a severe finding
    escalates to. These decide who gets woken up, which is why every write
    here is org-admin only."""
    from app.categories import CATEGORY_REGISTRY

    org = await _require_org_admin(user)
    if category not in CATEGORY_REGISTRY:
        raise HTTPException(400, f"unknown category: {category}")
    try:
        await asyncio.to_thread(
            orgs.set_routing_rule,
            org["id"],
            category,
            designation_key=body.designation_key,
            escalate_at_severity=body.escalate_at_severity,
            min_seniority=Seniority(body.min_seniority),
        )
    except ValueError as error:
        raise HTTPException(400, f"unknown seniority: {body.min_seniority}") from error
    except orgs.OrgError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True, "routing_rules": await asyncio.to_thread(orgs.routing_rules, org["id"])}


@router.get("/identity-links")
async def list_identity_links(user: User = Depends(current_user)):
    """Linked identities, plus the git authors in this org's repositories that
    map to nobody yet.

    The suggestions are what makes linking usable: without them an admin has
    to already know which address a colleague commits under and type it in
    blind. Read access is open to any member -- "why did this reach me" should
    be answerable without asking an admin.
    """
    org = await _current_org(user)
    return {
        "links": await asyncio.to_thread(orgs.identity_links, org["id"]),
        "unlinked_authors": await asyncio.to_thread(orgs.unlinked_authors, org["id"]),
    }


@router.delete("/identity-links/{link_id}")
async def remove_identity_link(link_id: uuid.UUID, user: User = Depends(current_user)):
    org = await _require_org_admin(user)
    try:
        await asyncio.to_thread(orgs.unlink_identity, org["id"], link_id)
    except orgs.OrgError as error:
        raise HTTPException(404, str(error)) from error
    return {"ok": True, "links": await asyncio.to_thread(orgs.identity_links, org["id"])}

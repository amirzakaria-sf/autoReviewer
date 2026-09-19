"""Designations, routing rules and identity links -- the three settings that
were seeded once and then read-only.

The page displayed all three. Nothing could change any of them, so every
organization was stuck with whichever six designations and six routing rules
`create_org` happened to write, and git blame -- the FIRST stage of the
assignment pipeline -- could never resolve anyone, because nothing could
create an identity link.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from app import orgs
from app.db import async_session
from app.enums import OrgRole, Seniority, UserRole, UserStatus
from app.models import Organization, User


async def _org(name: str) -> dict:
    import asyncio

    return await asyncio.to_thread(orgs.create_org, name=f"{name}-{uuid.uuid4().hex[:8]}", created_by=None)


async def _member(org_id, email: str) -> uuid.UUID:
    import asyncio

    from app.security import hash_password

    async with async_session() as db:
        user = User(
            email=email, password_hash=hash_password("a-real-password"),
            role=UserRole.MEMBER, status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.commit()
        user_id = user.id

    await asyncio.to_thread(
        orgs.add_member, org_id=org_id, user_id=user_id, role=OrgRole.MEMBER, seniority=Seniority.SDE2
    )
    return user_id


async def _cleanup(org_id: str, emails: list[str] | None = None) -> None:
    from app.models import Designation, IdentityLink, MemberDesignation, OrgMember, RoutingRule

    async with async_session() as db:
        member_ids = (
            await db.execute(OrgMember.__table__.select().with_only_columns(OrgMember.id).where(
                OrgMember.org_id == uuid.UUID(org_id)))
        ).scalars().all()
        if member_ids:
            await db.execute(delete(MemberDesignation).where(MemberDesignation.member_id.in_(member_ids)))
        await db.execute(delete(IdentityLink).where(IdentityLink.org_id == uuid.UUID(org_id)))
        await db.execute(delete(OrgMember).where(OrgMember.org_id == uuid.UUID(org_id)))
        await db.execute(delete(RoutingRule).where(RoutingRule.org_id == uuid.UUID(org_id)))
        await db.execute(delete(Designation).where(Designation.org_id == uuid.UUID(org_id)))
        await db.execute(delete(Organization).where(Organization.id == uuid.UUID(org_id)))
        for email in emails or []:
            await db.execute(delete(User).where(User.email == email))
        await db.commit()


# --- designations ---------------------------------------------------------------


async def test_a_designation_can_be_added_and_renamed():
    import asyncio

    org = await _org("Designation Co")
    try:
        created = await asyncio.to_thread(orgs.upsert_designation, org["id"], "Machine Learning", "")
        assert created["key"] == "machine-learning"
        # Label defaults from the key rather than being left blank.
        assert created["label"] == "Machine Learning"

        renamed = await asyncio.to_thread(orgs.upsert_designation, org["id"], "machine-learning", "ML Platform")
        assert renamed["key"] == "machine-learning", "the key is what rules reference, so it is permanent"
        assert renamed["label"] == "ML Platform"

        keys = {d["key"] for d in orgs.designations(org["id"])}
        assert "machine-learning" in keys
    finally:
        await _cleanup(org["id"])


async def test_a_designation_in_use_cannot_be_deleted():
    """Deleting one a routing rule points at leaves that category resolving to
    a designation nobody holds -- the assignment falls through to the org
    admin, which reads as the router being broken rather than a configuration
    change somebody did not finish."""
    import asyncio

    org = await _org("In Use Co")
    try:
        # Two categories route to frontend, so this is the plural wording.
        with pytest.raises(orgs.OrgError) as error:
            await asyncio.to_thread(orgs.delete_designation, org["id"], "frontend")
        assert "ui" in str(error.value) and "accessibility" in str(error.value)
        assert "still route to this designation" in str(error.value)
        assert any(d["key"] == "frontend" for d in orgs.designations(org["id"]))

        # Exactly one category routes to security, so it reads as a singular.
        with pytest.raises(orgs.OrgError) as singular:
            await asyncio.to_thread(orgs.delete_designation, org["id"], "security")
        assert "security still routes to this designation" in str(singular.value)
        assert "that category" in str(singular.value)
    finally:
        await _cleanup(org["id"])


async def test_an_unused_designation_deletes_cleanly():
    import asyncio

    org = await _org("Unused Co")
    try:
        await asyncio.to_thread(orgs.upsert_designation, org["id"], "mobile", "Mobile")
        await asyncio.to_thread(orgs.delete_designation, org["id"], "mobile")
        assert not any(d["key"] == "mobile" for d in orgs.designations(org["id"]))
    finally:
        await _cleanup(org["id"])


# --- routing --------------------------------------------------------------------


async def test_a_category_can_be_pointed_at_a_different_designation():
    import asyncio

    org = await _org("Routing Co")
    try:
        await asyncio.to_thread(orgs.upsert_designation, org["id"], "platform", "Platform")
        await asyncio.to_thread(
            orgs.set_routing_rule, org["id"], "performance",
            designation_key="platform", escalate_at_severity=3, min_seniority=Seniority.STAFF,
        )
        rule = next(r for r in orgs.routing_rules(org["id"]) if r["category"] == "performance")
        assert rule["designation_key"] == "platform"
        assert rule["escalate_at_severity"] == 3
        assert rule["min_seniority"] == "staff"
    finally:
        await _cleanup(org["id"])


async def test_a_rule_naming_a_designation_that_does_not_exist_is_refused():
    """Such a rule silently never matches, and the failure surfaces much later
    as an issue assigned to the org admin for no visible reason."""
    import asyncio

    org = await _org("Ghost Co")
    try:
        with pytest.raises(orgs.OrgError) as error:
            await asyncio.to_thread(
                orgs.set_routing_rule, org["id"], "ui",
                designation_key="nonexistent", escalate_at_severity=4, min_seniority=Seniority.SDE3,
            )
        assert "no 'nonexistent' designation" in str(error.value)
    finally:
        await _cleanup(org["id"])


async def test_an_out_of_range_escalation_severity_is_refused():
    import asyncio

    org = await _org("Range Co")
    try:
        with pytest.raises(orgs.OrgError):
            await asyncio.to_thread(
                orgs.set_routing_rule, org["id"], "ui",
                designation_key="frontend", escalate_at_severity=9, min_seniority=Seniority.SDE3,
            )
    finally:
        await _cleanup(org["id"])


# --- identity links ---------------------------------------------------------------


async def test_an_identity_link_makes_blame_resolve_to_a_person():
    """The first stage of the assignment pipeline. Without a link,
    resolve_identity returns nothing, every issue falls through to designation
    routing, and the trace reads "No WhipGuard account is linked to ..."."""
    import asyncio

    org = await _org("Blame Co")
    email = f"dev-{uuid.uuid4().hex[:8]}@example.com"
    try:
        user_id = await _member(org["id"], email)
        assert orgs.resolve_identity(org["id"], "git-email", email) is None

        await asyncio.to_thread(orgs.link_identity, org["id"], user_id, "git-email", email)
        assert orgs.resolve_identity(org["id"], "git-email", email) == str(user_id)

        assignment = await asyncio.to_thread(
            orgs.assign_issue, org_id=org["id"], category="ui", severity=2, author_external_id=email,
        )
        assert assignment["assignee"]["email"] == email
        # The trace says WHY, which is the whole point of storing it -- before
        # the link it read "No WhipGuard account is linked to ...".
        assert any("most context here" in line for line in assignment["reasoning"])
    finally:
        await _cleanup(org["id"], [email])


async def test_an_identity_is_matched_case_insensitively():
    """Git records whatever case the author configured; nobody types it back
    identically."""
    import asyncio

    org = await _org("Case Co")
    email = f"Dev-{uuid.uuid4().hex[:8]}@Example.com"
    try:
        user_id = await _member(org["id"], email.lower())
        await asyncio.to_thread(orgs.link_identity, org["id"], user_id, "git-email", email)
        assert orgs.resolve_identity(org["id"], "git-email", email.upper()) == str(user_id)
    finally:
        await _cleanup(org["id"], [email.lower()])


async def test_a_link_can_be_removed_and_stops_resolving():
    import asyncio

    org = await _org("Unlink Co")
    email = f"dev-{uuid.uuid4().hex[:8]}@example.com"
    try:
        user_id = await _member(org["id"], email)
        await asyncio.to_thread(orgs.link_identity, org["id"], user_id, "git-email", email)
        link = orgs.identity_links(org["id"])[0]

        await asyncio.to_thread(orgs.unlink_identity, org["id"], link["id"])
        assert orgs.identity_links(org["id"]) == []
        assert orgs.resolve_identity(org["id"], "git-email", email) is None
    finally:
        await _cleanup(org["id"], [email])


async def test_another_orgs_link_cannot_be_removed():
    import asyncio

    one = await _org("Owner Co")
    two = await _org("Other Co")
    email = f"dev-{uuid.uuid4().hex[:8]}@example.com"
    try:
        user_id = await _member(one["id"], email)
        await asyncio.to_thread(orgs.link_identity, one["id"], user_id, "git-email", email)
        link = orgs.identity_links(one["id"])[0]

        with pytest.raises(orgs.OrgError):
            await asyncio.to_thread(orgs.unlink_identity, two["id"], link["id"])
        assert len(orgs.identity_links(one["id"])) == 1
    finally:
        await _cleanup(one["id"], [email])
        await _cleanup(two["id"])


async def test_author_suggestions_never_raise_on_a_repo_that_is_not_cloned():
    """A suggestion list is an enhancement, never load-bearing."""
    import asyncio

    org = await _org("Suggest Co")
    try:
        assert await asyncio.to_thread(orgs.unlinked_authors, org["id"]) == []
    finally:
        await _cleanup(org["id"])


async def test_an_identity_cannot_be_linked_to_someone_outside_the_org():
    """Otherwise an admin could map a git author to a user in another
    organization, and every finding that author touched would be assigned
    across the tenancy boundary -- the one thing repos.org_id exists to
    prevent."""
    from fastapi import HTTPException

    from app.routers.org import add_identity_link

    one = await _org("Inside Co")
    two = await _org("Outside Co")
    insider = f"in-{uuid.uuid4().hex[:8]}@example.com"
    outsider = f"out-{uuid.uuid4().hex[:8]}@example.com"
    try:
        await _member(one["id"], insider)
        outsider_id = await _member(two["id"], outsider)

        admin = type("U", (), {"id": uuid.uuid4(), "role": type("R", (), {"value": "admin"})()})()
        with pytest.raises(HTTPException) as error:
            await add_identity_link(
                {"user_id": str(outsider_id), "external_id": "someone@example.com"}, user=admin
            )
        assert error.value.status_code in (400, 404)
    finally:
        await _cleanup(one["id"], [insider])
        await _cleanup(two["id"], [outsider])


async def test_a_malformed_user_id_is_a_clear_refusal_not_a_crash():
    """Hit for real: a shell variable expanded to "1000" instead of a uuid and
    the insert came back as a 500 with nothing to act on."""
    from fastapi import HTTPException

    from app.routers.org import add_identity_link

    admin = type("U", (), {"id": uuid.uuid4(), "role": type("R", (), {"value": "admin"})()})()
    with pytest.raises(HTTPException) as error:
        await add_identity_link({"user_id": "1000", "external_id": "a@b.com"}, user=admin)
    assert error.value.status_code in (400, 404)

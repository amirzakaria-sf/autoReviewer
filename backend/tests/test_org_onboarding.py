"""Creating an organization and getting people into it.

Everything the org feature could do before this was read or edit an org that
already existed. Nothing created one -- the first org, its members, its
designations and its routing rules were all inserted by hand -- so a second
company could not exist, and an approved user landed in no organization at
all, on a page telling them so.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app import org_invites, orgs
from app.db import async_session
from app.enums import OrgRole, Seniority, UserRole, UserStatus
from app.models import OrgInvite, Organization, User
from app.security import decode_invite_token, decode_org_invite_token, sign_invite_token, sign_org_invite_token


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _make_user(email: str) -> uuid.UUID:
    from app.security import hash_password

    async with async_session() as db:
        user = User(
            email=email, password_hash=hash_password("a-real-password"),
            role=UserRole.MEMBER, status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.commit()
        return user.id


async def _cleanup(org_id: str | None = None, emails: list[str] | None = None) -> None:
    from app.models import Designation, MemberDesignation, OrgMember, RoutingRule

    async with async_session() as db:
        if org_id:
            member_ids = (
                await db.execute(select(OrgMember.id).where(OrgMember.org_id == uuid.UUID(org_id)))
            ).scalars().all()
            if member_ids:
                await db.execute(delete(MemberDesignation).where(MemberDesignation.member_id.in_(member_ids)))
            await db.execute(delete(OrgMember).where(OrgMember.org_id == uuid.UUID(org_id)))
            await db.execute(delete(OrgInvite).where(OrgInvite.org_id == uuid.UUID(org_id)))
            await db.execute(delete(RoutingRule).where(RoutingRule.org_id == uuid.UUID(org_id)))
            await db.execute(delete(Designation).where(Designation.org_id == uuid.UUID(org_id)))
            await db.execute(delete(Organization).where(Organization.id == uuid.UUID(org_id)))
        for email in emails or []:
            await db.execute(delete(User).where(User.email == email))
        await db.commit()


# --- creating an org ------------------------------------------------------------


async def test_a_new_org_arrives_usable_rather_than_empty():
    """An org with no designations and no routing rules can route nothing, and
    "configure six things before anything works" is not an onboarding step
    anyone should have to discover."""
    org = None
    try:
        org = await orgs_create(_unique("Acme Rockets"))
        designations = orgs.designations(org["id"])
        rules = orgs.routing_rules(org["id"])

        assert {d["key"] for d in designations} >= {"frontend", "backend", "security"}
        assert {r["category"] for r in rules} >= {"ui", "security", "documentation"}
    finally:
        if org:
            await _cleanup(org["id"])


async def orgs_create(name: str) -> dict:
    import asyncio

    return await asyncio.to_thread(orgs.create_org, name=name, created_by=None)


async def test_the_slug_is_derived_and_url_safe():
    """It becomes the on-disk directory every one of this org's repositories is
    cloned under (app/sandbox/worktree.py), so it cannot contain anything a
    path would object to."""
    assert orgs.slugify("Acme Rockets!! 2026") == "acme-rockets-2026"
    assert orgs.slugify("  --Hello--  ") == "hello"
    assert orgs.slugify("") == "org"
    assert orgs.slugify("///") == "org"


async def test_a_duplicate_slug_is_refused_rather_than_silently_suffixed():
    """Two orgs quietly sharing a near-identical slug would share a workspace
    directory. Being asked to pick another name is the better outcome."""
    org = None
    try:
        name = _unique("Duplicate Co")
        org = await orgs_create(name)
        with pytest.raises(orgs.OrgError) as error:
            await orgs_create(name)
        assert "already taken" in str(error.value)
    finally:
        if org:
            await _cleanup(org["id"])


# --- membership ----------------------------------------------------------------


async def test_adding_the_same_person_twice_is_not_an_error():
    """Re-inviting someone who is already in the org is a normal thing to do
    when the first email was missed."""
    org = None
    email = _unique("dev") + "@example.com"
    try:
        org = await orgs_create(_unique("Repeat Co"))
        user_id = await _make_user(email)
        import asyncio

        first = await asyncio.to_thread(
            orgs.add_member, org_id=org["id"], user_id=user_id, role=OrgRole.MEMBER, seniority=Seniority.SDE2
        )
        second = await asyncio.to_thread(
            orgs.add_member, org_id=org["id"], user_id=user_id, role=OrgRole.MEMBER, seniority=Seniority.SDE2
        )
        assert first == second
        assert len(orgs.members(org["id"])) == 1
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_the_last_admin_cannot_be_removed():
    """An org with no admin can invite nobody, promote nobody, and cannot
    recover without a database console."""
    import asyncio

    org = None
    email = _unique("boss") + "@example.com"
    try:
        org = await orgs_create(_unique("Solo Co"))
        user_id = await _make_user(email)
        member_id = await asyncio.to_thread(
            orgs.add_member, org_id=org["id"], user_id=user_id,
            role=OrgRole.ORG_ADMIN, seniority=Seniority.STAFF,
        )
        with pytest.raises(orgs.OrgError) as error:
            await asyncio.to_thread(orgs.remove_member, member_id)
        assert "last admin" in str(error.value)
        assert len(orgs.members(org["id"])) == 1
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_a_second_admin_makes_the_first_removable():
    import asyncio

    org = None
    one, two = _unique("a") + "@example.com", _unique("b") + "@example.com"
    try:
        org = await orgs_create(_unique("Pair Co"))
        first = await asyncio.to_thread(
            orgs.add_member, org_id=org["id"], user_id=await _make_user(one),
            role=OrgRole.ORG_ADMIN, seniority=Seniority.STAFF,
        )
        await asyncio.to_thread(
            orgs.add_member, org_id=org["id"], user_id=await _make_user(two),
            role=OrgRole.ORG_ADMIN, seniority=Seniority.STAFF,
        )
        await asyncio.to_thread(orgs.remove_member, first)
        assert len(orgs.members(org["id"])) == 1
    finally:
        await _cleanup(org["id"] if org else None, [one, two])


# --- invitations -----------------------------------------------------------------


async def test_an_invitation_carries_the_setup_the_admin_chose():
    """Accepting produces a configured member, not somebody the admin then has
    to set up a second time."""
    org = None
    email = _unique("newhire") + "@example.com"
    try:
        org = await orgs_create(_unique("Invite Co"))
        async with async_session() as db:
            invite = await org_invites.create_invite(
                db, org_id=uuid.UUID(org["id"]), email=email, name="New Hire",
                role=OrgRole.MEMBER, seniority=Seniority.SDE3, designation_keys=["backend", "security"],
            )
            user = await org_invites.accept(db, invite=invite, password="a-real-password")

        member = orgs.members(org["id"])[0]
        assert member["email"] == email
        assert member["seniority"] == "sde3"
        assert sorted(member["designations"]) == ["backend", "security"]
        assert user.role == UserRole.MEMBER, "joining a team never grants platform admin"
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_an_existing_account_joins_without_being_recreated():
    org = None
    email = _unique("already") + "@example.com"
    try:
        org = await orgs_create(_unique("Existing Co"))
        user_id = await _make_user(email)
        async with async_session() as db:
            invite = await org_invites.create_invite(db, org_id=uuid.UUID(org["id"]), email=email)
            # No password: there is already an account, so none is asked for.
            user = await org_invites.accept(db, invite=invite, password="")

        assert user.id == user_id
        assert len(orgs.members(org["id"])) == 1
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_a_used_invitation_cannot_be_replayed():
    org = None
    email = _unique("once") + "@example.com"
    try:
        org = await orgs_create(_unique("Replay Co"))
        async with async_session() as db:
            invite = await org_invites.create_invite(db, org_id=uuid.UUID(org["id"]), email=email)
            token = sign_org_invite_token(invite.id)
            await org_invites.accept(db, invite=invite, password="a-real-password")

        async with async_session() as db:
            with pytest.raises(org_invites.InviteError) as error:
                await org_invites.load_pending(db, token)
        assert "already been used" in str(error.value)
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_a_revoked_invitation_says_so_rather_than_just_failing():
    """"Invalid link" for a withdrawn invitation, an expired one and a typo
    alike leaves the invitee with nothing to act on."""
    org = None
    email = _unique("revoked") + "@example.com"
    try:
        org = await orgs_create(_unique("Revoke Co"))
        async with async_session() as db:
            invite = await org_invites.create_invite(db, org_id=uuid.UUID(org["id"]), email=email)
            token = sign_org_invite_token(invite.id)
            await org_invites.revoke_invite(db, invite.id)

        async with async_session() as db:
            with pytest.raises(org_invites.InviteError) as error:
                await org_invites.load_pending(db, token)
        assert "withdrawn" in str(error.value)
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_re_inviting_refreshes_the_terms_instead_of_colliding():
    """A pending invite per (org, email) is unique in the database. An admin
    re-sending with a different seniority must not hit a constraint they
    cannot see."""
    org = None
    email = _unique("again") + "@example.com"
    try:
        org = await orgs_create(_unique("Resend Co"))
        async with async_session() as db:
            first = await org_invites.create_invite(
                db, org_id=uuid.UUID(org["id"]), email=email, seniority=Seniority.SDE1
            )
            second = await org_invites.create_invite(
                db, org_id=uuid.UUID(org["id"]), email=email, seniority=Seniority.STAFF
            )
            assert first.id == second.id
            assert second.seniority == Seniority.STAFF

            pending = await org_invites.pending_for_org(db, uuid.UUID(org["id"]))
        assert len(pending) == 1
    finally:
        await _cleanup(org["id"] if org else None, [email])


def test_an_access_request_token_cannot_be_redeemed_as_an_org_invitation():
    """Both are signed with the same secret. A shared payload key would let a
    platform access-request link join an organization, and an org invitation
    create a platform account -- different decisions, made by different
    people."""
    some_id = uuid.uuid4()

    assert decode_org_invite_token(sign_invite_token(some_id)) is None
    assert decode_invite_token(sign_org_invite_token(some_id)) is None
    assert decode_org_invite_token(sign_org_invite_token(some_id)) == some_id


# --- repository tenancy ----------------------------------------------------------


async def test_connecting_a_repo_requires_an_organization():
    """`repos.org_id` is the one column carrying tenancy, and nothing set it
    until now -- every repository connected through the product was orphaned
    from the org that owned it. A connect with no org to attach to is refused
    rather than silently creating another orphan."""
    from unittest.mock import patch

    from fastapi import HTTPException

    from app.routers.github import connect_repo

    class _Request:
        state = type("S", (), {"user_id": str(uuid.uuid4())})()

    with patch("app.orgs.orgs_for_user", return_value=[]):
        with pytest.raises(HTTPException) as error:
            await connect_repo({"full_name": "acme/demo"}, _Request())

    assert error.value.status_code == 409
    assert "not in an organization" in error.value.detail


async def test_a_repo_is_assigned_to_the_connectors_org():
    import asyncio

    from sqlalchemy import delete as sql_delete

    from app.models import Repo

    org = None
    full_name = f"acme/{_unique('repo')}"
    try:
        org = await orgs_create(_unique("Tenancy Co"))
        async with async_session() as db:
            repo = Repo(github_full_name=full_name)
            db.add(repo)
            await db.commit()
            repo_id = repo.id

        await asyncio.to_thread(orgs.assign_repo, repo_id, org["id"])

        async with async_session() as db:
            stored = await db.get(Repo, repo_id)
            assert str(stored.org_id) == org["id"]
        assert orgs.org_for_repo(repo_id) == org["id"]
    finally:
        async with async_session() as db:
            await db.execute(sql_delete(Repo).where(Repo.github_full_name == full_name))
            await db.commit()
        if org:
            await _cleanup(org["id"])


# --- the access-request path ------------------------------------------------------


async def test_an_approved_access_request_lands_in_the_chosen_org():
    """The other half of onboarding. Approving used to produce an active
    account belonging to no organization -- which sees no repositories, no
    issues, and a page saying so. Half the signup flow led there."""
    import asyncio
    from datetime import datetime, timezone

    from app.enums import AccessRequestStatus
    from app.models import AccessRequest
    from app.security import hash_password

    org = None
    email = _unique("requester") + "@example.com"
    try:
        org = await orgs_create(_unique("Request Co"))

        async with async_session() as db:
            request_row = AccessRequest(
                name="A Requester", email=email, reason="I work here",
                status=AccessRequestStatus.APPROVED,
                decided_at=datetime.now(timezone.utc),
                org_id=uuid.UUID(org["id"]),
            )
            db.add(request_row)
            await db.commit()
            request_id = request_row.id

        # What /complete-invite does once the person sets a password.
        async with async_session() as db:
            row = await db.get(AccessRequest, request_id)
            user = User(
                email=row.email, password_hash=hash_password("a-real-password"),
                role=UserRole.MEMBER, status=UserStatus.ACTIVE,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            await asyncio.to_thread(
                orgs.add_member, org_id=row.org_id, user_id=user.id,
                role=OrgRole.MEMBER, seniority=Seniority.SDE2,
            )

        assert [m["email"] for m in orgs.members(org["id"])] == [email]
        assert orgs.orgs_for_user(user.id)[0]["id"] == org["id"]
    finally:
        async with async_session() as db:
            await db.execute(delete(AccessRequest).where(AccessRequest.email == email))
            await db.commit()
        await _cleanup(org["id"] if org else None, [email])


async def test_an_org_created_without_an_admin_can_still_be_given_one():
    """Otherwise it is unreachable: every one of its own admin endpoints
    requires an org admin, so an org with none can invite nobody and
    configure nothing without a database console."""
    import asyncio

    org = None
    email = _unique("rescue") + "@example.com"
    try:
        org = await orgs_create(_unique("Adminless Co"))
        assert orgs.members(org["id"]) == []

        user_id = await _make_user(email)
        await asyncio.to_thread(
            orgs.add_member, org_id=org["id"], user_id=user_id,
            role=OrgRole.ORG_ADMIN, seniority=Seniority.SDE3,
        )

        member = orgs.members(org["id"])[0]
        assert member["role"] == "org_admin"
    finally:
        await _cleanup(org["id"] if org else None, [email])


async def test_a_person_cannot_be_added_to_a_second_organization():
    """Every org-scoped read resolves the caller's org as
    `orgs_for_user(...)[0]`, ordered by creation date. A second membership
    does not fail -- it silently decides which organization someone sees by
    which was created first, and nothing anywhere says so. Refusing is the
    honest version of the same constraint."""
    import asyncio

    first = second = None
    email = _unique("shared") + "@example.com"
    try:
        first = await orgs_create(_unique("First Co"))
        second = await orgs_create(_unique("Second Co"))
        user_id = await _make_user(email)

        await asyncio.to_thread(orgs.add_member, org_id=first["id"], user_id=user_id)
        with pytest.raises(orgs.OrgError) as error:
            await asyncio.to_thread(orgs.add_member, org_id=second["id"], user_id=user_id)

        assert "one organization at a time" in str(error.value)
        assert orgs.members(second["id"]) == []
        # Re-adding to the org they ARE in stays idempotent, not an error.
        await asyncio.to_thread(orgs.add_member, org_id=first["id"], user_id=user_id)
        assert len(orgs.members(first["id"])) == 1
    finally:
        await _cleanup(first["id"] if first else None, [email])
        await _cleanup(second["id"] if second else None)

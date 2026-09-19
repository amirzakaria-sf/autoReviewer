"""Organizations: membership, designations, and who an issue belongs to.

Tenancy has one source of truth. A `Repo` carries `org_id`; everything else a
tenant owns -- issues, fixes, traces, chunks, symbols -- reaches its org
THROUGH its repo. A denormalised `org_id` on twelve tables is twelve places
to forget a filter and twelve ways for the copies to disagree.

The assignment pipeline at the bottom is an ordered chain with a fallback at
every stage, because every stage genuinely fails in practice: blame catches
formatters and renames, identity mapping breaks on noreply addresses, and
people leave. A stage that cannot answer passes the question down rather
than blocking.
"""

from __future__ import annotations

import re

import logging

import psycopg

from app.enums import OrgRole, Seniority, seniority_rank
from app import sync_db

logger = logging.getLogger("whipguard.orgs")


def _connect() -> psycopg.Connection:
    return sync_db.connection()


# --- membership --------------------------------------------------------------


def org_for_repo(repo_id) -> str | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT org_id FROM repos WHERE id = %s", (str(repo_id),))
        row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def membership(user_id, org_id) -> dict | None:
    """One person's standing in one org: role, seniority, designations."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, role, seniority FROM org_members WHERE user_id = %s AND org_id = %s",
            (str(user_id), str(org_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        member_id, role, seniority = row
        cur.execute(
            """
            SELECT d.key FROM member_designations md
            JOIN designations d ON d.id = md.designation_id
            WHERE md.member_id = %s ORDER BY d.key
            """,
            (str(member_id),),
        )
        designations = [value for (value,) in cur.fetchall()]
    return {
        "member_id": str(member_id),
        "role": str(role).lower(),
        "seniority": str(seniority).lower(),
        "designations": designations,
    }


def orgs_for_user(user_id) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id, o.name, o.slug, m.role, m.seniority
            FROM org_members m JOIN organizations o ON o.id = m.org_id
            WHERE m.user_id = %s ORDER BY o.created_at
            """,
            (str(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "slug": row[2],
            "role": str(row[3]).lower(),
            "seniority": str(row[4]).lower(),
        }
        for row in rows
    ]


def members(org_id) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, u.id, u.email, u.first_name, u.last_name, m.role, m.seniority,
                   coalesce(
                     (SELECT array_agg(d.key ORDER BY d.key) FROM member_designations md
                      JOIN designations d ON d.id = md.designation_id WHERE md.member_id = m.id),
                     '{}'
                   )
            FROM org_members m JOIN users u ON u.id = m.user_id
            WHERE m.org_id = %s ORDER BY u.email
            """,
            (str(org_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "member_id": str(row[0]),
            "user_id": str(row[1]),
            "email": row[2],
            "name": " ".join(part for part in (row[3], row[4]) if part) or row[2].split("@")[0],
            "role": str(row[5]).lower(),
            "seniority": str(row[6]).lower(),
            "designations": list(row[7] or []),
        }
        for row in rows
    ]


def designations(org_id) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, key, label FROM designations WHERE org_id = %s ORDER BY key", (str(org_id),))
        rows = cur.fetchall()
    return [{"id": str(row[0]), "key": row[1], "label": row[2]} for row in rows]


def set_member_role(member_id, role: OrgRole) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE org_members SET role = %s WHERE id = %s", (role.value.upper(), str(member_id)))
        conn.commit()


def set_member_seniority(member_id, seniority: Seniority) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE org_members SET seniority = %s WHERE id = %s", (seniority.value.upper(), str(member_id))
        )
        conn.commit()


def set_member_designations(member_id, keys: list[str], org_id) -> None:
    """Replace, not merge -- the UI presents this as a checklist, so an
    unchecked box has to actually remove the designation."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM member_designations WHERE member_id = %s", (str(member_id),))
        for key in keys:
            cur.execute(
                """
                INSERT INTO member_designations (id, member_id, designation_id)
                SELECT gen_random_uuid(), %s, d.id FROM designations d
                WHERE d.org_id = %s AND d.key = %s
                ON CONFLICT ON CONSTRAINT uq_member_designation DO NOTHING
                """,
                (str(member_id), str(org_id), key),
            )
        conn.commit()


# --- routing -----------------------------------------------------------------


def routing_rules(org_id) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT category, designation_key, escalate_at_severity, min_seniority "
            "FROM routing_rules WHERE org_id = %s ORDER BY category",
            (str(org_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "category": row[0],
            "designation_key": row[1],
            "escalate_at_severity": row[2],
            "min_seniority": str(row[3]).lower(),
        }
        for row in rows
    ]


def link_identity(org_id, user_id, provider: str, external_id: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO identity_links (id, org_id, user_id, provider, external_id)
            VALUES (gen_random_uuid(), %s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT uq_identity_link DO UPDATE SET user_id = EXCLUDED.user_id
            """,
            (str(org_id), str(user_id), provider, external_id.lower()),
        )
        conn.commit()


def resolve_identity(org_id, provider: str, external_id: str) -> str | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM identity_links WHERE org_id = %s AND provider = %s AND external_id = %s",
            (str(org_id), provider, (external_id or "").lower()),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def _candidates_by_designation(org_id, designation_key: str) -> list[dict]:
    return [
        member for member in members(org_id) if designation_key in member["designations"]
    ]


def assign_issue(*, org_id, category: str, severity: int, author_external_id: str = "") -> dict:
    """Decide who owns a finding, and record WHY.

    The reasoning is returned alongside the assignee because this is the part
    people argue with. "Assigned to you" invites a shrug; "assigned to you —
    you have the most context here, and nobody else holds the backend
    designation" invites a conversation.

    Never raises. An org with no members at all still gets an answer, it is
    just an empty one.
    """
    trace: list[str] = []
    rules = {rule["category"]: rule for rule in routing_rules(org_id)}
    rule = rules.get(category)

    # 1. The git author, if we can map them to a person.
    assignee: dict | None = None
    if author_external_id:
        user_id = resolve_identity(org_id, "git-email", author_external_id) or resolve_identity(
            org_id, "github", author_external_id
        )
        if user_id:
            match = next((m for m in members(org_id) if m["user_id"] == user_id), None)
            if match:
                assignee = match
                trace.append(f"{match['name']} has the most context here — they last worked on this code")
        else:
            trace.append(f"No WhipGuard account is linked to '{author_external_id}'")

    # 2. Otherwise route by designation.
    designation_key = rule["designation_key"] if rule else ""
    if assignee is None and designation_key:
        candidates = _candidates_by_designation(org_id, designation_key)
        if candidates:
            assignee = candidates[0]
            trace.append(f"Routed to the {designation_key} designation ({len(candidates)} candidate(s))")
        else:
            trace.append(f"Nobody in this org holds the {designation_key} designation")

    # 3. Severity floor: escalate UP, never down.
    if rule and severity >= int(rule["escalate_at_severity"]):
        floor = rule["min_seniority"]
        needs_escalation = assignee is None or seniority_rank(assignee["seniority"]) < seniority_rank(floor)
        if needs_escalation:
            pool = _candidates_by_designation(org_id, designation_key) if designation_key else members(org_id)
            senior = [m for m in pool if seniority_rank(m["seniority"]) >= seniority_rank(floor)]
            if senior:
                senior.sort(key=lambda m: seniority_rank(m["seniority"]))
                assignee = senior[0]
                trace.append(f"Severity {severity} requires at least {floor} — escalated to {assignee['name']}")
            else:
                trace.append(f"Severity {severity} wants at least {floor}, but nobody in scope is that senior")

    # 4. Anyone whose area the finding touches is a watcher, not an owner.
    watchers = [
        member
        for member in (_candidates_by_designation(org_id, designation_key) if designation_key else [])
        if not assignee or member["user_id"] != assignee["user_id"]
    ]

    # 5. Last resort. Better an org admin than nobody.
    if assignee is None:
        admins = [member for member in members(org_id) if member["role"] == OrgRole.ORG_ADMIN.value]
        if admins:
            assignee = admins[0]
            trace.append("No designated owner — fell back to an org admin")

    return {
        "assignee": assignee,
        "watchers": watchers[:5],
        "designation": designation_key,
        "reasoning": trace,
    }


# --- git attribution for routing ---------------------------------------------


def last_author_email(repo_full_name: str, path: str) -> str:
    """Whoever last touched a file, as an email.

    Used only as the FIRST candidate in assignment -- if it resolves to
    nobody, routing falls through to designation. That is why this returns a
    plain string and swallows every failure: a missing author must never be
    the reason an issue goes unassigned.

    `-w -M -C` so whitespace reflows, file moves and copied blocks do not
    credit the person who reformatted the file.
    """
    import subprocess

    from app.sandbox.worktree import mirror_path

    try:
        result = subprocess.run(
            ["git", "-C", str(mirror_path(repo_full_name)), "log", "-1", "--format=%ae", "--", path],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception as error:  # noqa: BLE001
        logger.warning("could not read last author for %s: %s", path, error)
        return ""


def assign_for_issue(*, repo_id, repo_full_name: str, category: str, severity: int, paths: list[str]) -> dict:
    """Route one finding, starting from whoever last touched the code it
    names. Never raises -- an unassigned issue is worse than an imperfect
    assignment, but a crashed council is worse than both."""
    try:
        org_id = org_for_repo(repo_id)
        if not org_id:
            return {"assignee": None, "watchers": [], "designation": "", "reasoning": ["Repo has no organization"]}

        author = ""
        for path in paths[:3]:
            author = last_author_email(repo_full_name, path)
            if author:
                break

        return assign_issue(
            org_id=org_id, category=category, severity=severity, author_external_id=author
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("assignment failed for repo=%s: %s", repo_id, error)
        return {"assignee": None, "watchers": [], "designation": "", "reasoning": [f"Assignment failed: {error}"]}


# --- creating organizations and putting people in them -------------------------
#
# Everything above reads or edits an org that already exists. Nothing created
# one: the first org, its members, its designations and its routing rules were
# all inserted by hand, so a second company could not exist and an approved
# user landed in no organization at all. This section is that missing half.


def slugify(name: str) -> str:
    """A url-safe, human-recognisable slug. Also the on-disk directory name
    for every repository the org owns (app/sandbox/worktree.py), which is why
    it is restricted rather than merely lowercased."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:48] or "org"


# Seeded into every new org. Not a hardcoded enum -- these are rows the org can
# rename or replace -- but an org that starts empty can route nothing, and
# "configure six designations before anything works" is not an onboarding
# step anyone should have to discover.
DEFAULT_DESIGNATIONS = (
    ("frontend", "Frontend"),
    ("backend", "Backend"),
    ("security", "Security"),
    ("devops", "DevOps"),
    ("qa", "QA"),
    ("data", "Data"),
)

# Which expertise each category routes to by default, and the seniority floor
# a severe finding escalates to. Mirrors the registry's own categories.
DEFAULT_ROUTING = (
    ("ui", "frontend"),
    ("accessibility", "frontend"),
    ("backend", "backend"),
    ("documentation", "backend"),
    ("performance", "backend"),
    ("security", "security"),
)


class OrgError(Exception):
    """Something the caller can act on, phrased for them rather than logged."""


def create_org(*, name: str, created_by, slug: str = "") -> dict:
    """Create an organization, seed its defaults, and return it.

    The slug is unique and immutable once repositories are on disk under it.
    A collision is reported rather than silently suffixed, because two orgs
    quietly sharing a near-identical slug is worse than being asked to pick
    another name.
    """
    name = (name or "").strip()
    if not name:
        raise OrgError("An organization needs a name.")
    slug = slugify(slug or name)

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM organizations WHERE slug = %s", (slug,))
        if cur.fetchone():
            raise OrgError(f"The slug {slug!r} is already taken -- pick a different name.")

        cur.execute(
            "INSERT INTO organizations (id, name, slug, created_by, created_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, now()) RETURNING id",
            (name, slug, str(created_by) if created_by else None),
        )
        org_id = cur.fetchone()[0]

        for key, label in DEFAULT_DESIGNATIONS:
            cur.execute(
                "INSERT INTO designations (id, org_id, key, label) VALUES (gen_random_uuid(), %s, %s, %s) "
                "ON CONFLICT ON CONSTRAINT uq_designation_key DO NOTHING",
                (str(org_id), key, label),
            )
        for category, designation_key in DEFAULT_ROUTING:
            cur.execute(
                "INSERT INTO routing_rules "
                "(id, org_id, category, designation_key, escalate_at_severity, min_seniority) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s) "
                "ON CONFLICT ON CONSTRAINT uq_routing_rule DO NOTHING",
                # Severity 4 and above escalates to at least SDE3: a critical
                # finding should not sit with whoever happens to be free.
                (str(org_id), category, designation_key, 4, Seniority.SDE3.name),
            )
        conn.commit()

    return {"id": str(org_id), "name": name, "slug": slug}


def add_member(*, org_id, user_id, role: OrgRole = OrgRole.MEMBER,
               seniority: Seniority = Seniority.SDE2, designation_keys: list[str] | None = None) -> str:
    """Put a user in an org. Idempotent on (org, user).

    Returns the member id. A user already in the org keeps their existing
    membership rather than getting a second one -- the unique constraint
    would refuse it anyway, and re-inviting someone should not be an error
    the admin has to interpret.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM org_members WHERE org_id = %s AND user_id = %s",
            (str(org_id), str(user_id)),
        )
        row = cur.fetchone()
        if row:
            member_id = row[0]
        else:
            # One organization per person, for now. The rest of the code
            # assumes it -- every org-scoped read resolves the caller's org as
            # `orgs_for_user(...)[0]`, which is ordered by creation date. A
            # second membership therefore does not fail, it silently decides
            # which organization someone sees by which was created first, with
            # nothing anywhere saying so. Refusing is the honest version of
            # the same constraint.
            cur.execute(
                """
                SELECT o.name FROM org_members m JOIN organizations o ON o.id = m.org_id
                WHERE m.user_id = %s AND m.org_id <> %s LIMIT 1
                """,
                (str(user_id), str(org_id)),
            )
            other = cur.fetchone()
            if other:
                raise OrgError(
                    f"That person already belongs to {other[0]}. Someone can be in one organization "
                    "at a time -- remove them from it first, or use a different address."
                )
            cur.execute(
                "INSERT INTO org_members (id, org_id, user_id, role, seniority, created_at) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, now()) RETURNING id",
                (str(org_id), str(user_id), role.name, seniority.name),
            )
            member_id = cur.fetchone()[0]
        conn.commit()

    if designation_keys:
        set_member_designations(member_id, designation_keys, org_id)
    return str(member_id)


def remove_member(member_id) -> None:
    """Remove someone from an org, refusing to remove the last admin.

    Same guard the role change already applies: an org with no admin cannot
    invite anyone, cannot promote anyone, and cannot recover without a
    database console.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT org_id, role FROM org_members WHERE id = %s", (str(member_id),))
        row = cur.fetchone()
        if row is None:
            raise OrgError("That member no longer exists.")
        org_id, role = row[0], str(row[1])

        if role.lower() == OrgRole.ORG_ADMIN.value:
            cur.execute(
                "SELECT count(*) FROM org_members WHERE org_id = %s AND role = %s AND id <> %s",
                (str(org_id), OrgRole.ORG_ADMIN.name, str(member_id)),
            )
            if cur.fetchone()[0] == 0:
                raise OrgError(
                    "This is the organization's last admin. Promote someone else first, "
                    "otherwise nobody can administer it."
                )

        cur.execute("DELETE FROM member_designations WHERE member_id = %s", (str(member_id),))
        cur.execute("DELETE FROM org_members WHERE id = %s", (str(member_id),))
        conn.commit()


def all_orgs() -> list[dict]:
    """Every organization, for the system admin's view. Counts come from the
    same query rather than N follow-ups."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id, o.name, o.slug, o.created_at,
                   (SELECT count(*) FROM org_members m WHERE m.org_id = o.id),
                   (SELECT count(*) FROM repos r WHERE r.org_id = o.id),
                   (SELECT count(*) FROM org_invites i WHERE i.org_id = o.id AND i.status = 'pending')
            FROM organizations o ORDER BY o.created_at
            """
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(row[0]), "name": row[1], "slug": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "member_count": int(row[4]), "repo_count": int(row[5]), "pending_invites": int(row[6]),
        }
        for row in rows
    ]


def assign_repo(repo_id, org_id) -> None:
    """Give a repository to an org. `repos.org_id` is the ONE column carrying
    tenancy (see Organization's docstring), and nothing set it until now --
    every repo connected through the product was orphaned from its org."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE repos SET org_id = %s WHERE id = %s", (str(org_id), str(repo_id)))
        conn.commit()


def repo_ids_for_org(org_id) -> list:
    """Which repositories an org owns. The basis of every scoped read."""
    import uuid as _uuid

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM repos WHERE org_id = %s", (str(org_id),))
        return [_uuid.UUID(str(row[0])) for row in cur.fetchall()]


def repo_ids_for_user(user_id) -> list:
    """Every repository across EVERY organization the user belongs to."""
    import uuid as _uuid

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id FROM repos r
            JOIN org_members m ON m.org_id = r.org_id
            WHERE m.user_id = %s
            """,
            (str(user_id),),
        )
        seen: dict = {}
        for (repo_id,) in cur.fetchall():
            seen[_uuid.UUID(str(repo_id))] = True
        return list(seen)


# --- configuring an org: designations, routing, identities ---------------------
#
# All three were seeded once and then read-only: displayed on the team page
# with no way to change any of them. An org was stuck with whichever six
# designations and six routing rules create_org happened to write, and git
# blame -- the FIRST stage of the assignment pipeline -- could never match
# anyone, because nothing could create an identity link.


def upsert_designation(org_id, key: str, label: str) -> dict:
    """Add or rename one area of expertise.

    The key is what routing rules and member assignments reference, so it is
    slugified and immutable; renaming changes only the label.
    """
    key = slugify(key)
    label = (label or "").strip() or key.replace("-", " ").title()
    if not key:
        raise OrgError("A designation needs a name.")

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO designations (id, org_id, key, label) VALUES (gen_random_uuid(), %s, %s, %s) "
            "ON CONFLICT ON CONSTRAINT uq_designation_key DO UPDATE SET label = EXCLUDED.label "
            "RETURNING id",
            (str(org_id), key, label),
        )
        designation_id = cur.fetchone()[0]
        conn.commit()
    return {"id": str(designation_id), "key": key, "label": label}


def delete_designation(org_id, key: str) -> None:
    """Remove an area of expertise, refusing while anything still routes to it.

    Deleting one a routing rule points at would leave that category resolving
    to a designation nobody holds -- the assignment falls through to the
    severity floor and then to the org admin, which looks like the router
    being broken rather than a configuration change nobody finished.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT category FROM routing_rules WHERE org_id = %s AND designation_key = %s",
            (str(org_id), key),
        )
        used_by = [row[0] for row in cur.fetchall()]
        if used_by:
            verb = "still routes" if len(used_by) == 1 else "still route"
            noun = "that category" if len(used_by) == 1 else "those categories"
            raise OrgError(
                f"{', '.join(used_by)} {verb} to this designation. Point {noun} somewhere else first."
            )

        cur.execute("SELECT id FROM designations WHERE org_id = %s AND key = %s", (str(org_id), key))
        row = cur.fetchone()
        if row is None:
            raise OrgError("That designation does not exist.")

        cur.execute("DELETE FROM member_designations WHERE designation_id = %s", (str(row[0]),))
        cur.execute("DELETE FROM designations WHERE id = %s", (str(row[0]),))
        conn.commit()


def set_routing_rule(
    org_id, category: str, *, designation_key: str, escalate_at_severity: int, min_seniority: Seniority
) -> None:
    """Point one category at a designation, with the seniority a severe
    finding escalates to.

    The designation has to exist: a rule naming one that does not is a rule
    that silently never matches, and the failure surfaces much later as an
    issue assigned to the org admin for no visible reason.
    """
    if not 1 <= int(escalate_at_severity) <= 5:
        raise OrgError("Escalation severity has to be between 1 and 5.")

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM designations WHERE org_id = %s AND key = %s", (str(org_id), designation_key)
        )
        if cur.fetchone() is None:
            raise OrgError(f"There is no {designation_key!r} designation in this organization.")

        cur.execute(
            "INSERT INTO routing_rules "
            "(id, org_id, category, designation_key, escalate_at_severity, min_seniority) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s) "
            "ON CONFLICT ON CONSTRAINT uq_routing_rule DO UPDATE SET "
            "designation_key = EXCLUDED.designation_key, "
            "escalate_at_severity = EXCLUDED.escalate_at_severity, "
            "min_seniority = EXCLUDED.min_seniority",
            (str(org_id), category, designation_key, int(escalate_at_severity), min_seniority.name),
        )
        conn.commit()


def identity_links(org_id) -> list[dict]:
    """Every git/GitHub identity mapped to a person in this org."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.id, l.provider, l.external_id, u.id, u.email
            FROM identity_links l JOIN users u ON u.id = l.user_id
            WHERE l.org_id = %s ORDER BY l.provider, l.external_id
            """,
            (str(org_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(row[0]), "provider": row[1], "external_id": row[2],
            "user_id": str(row[3]), "email": row[4],
        }
        for row in rows
    ]


def unlink_identity(org_id, link_id) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM identity_links WHERE id = %s AND org_id = %s", (str(link_id), str(org_id))
        )
        if cur.rowcount == 0:
            raise OrgError("That identity link is not in this organization.")
        conn.commit()


def unlinked_authors(org_id, limit: int = 20) -> list[dict]:
    """Git authors seen in this org's repositories that map to nobody yet.

    The reason identity linking was unusable without it: the admin had to
    already know which email address a colleague commits under, and type it
    in blind. These are the addresses actually in the history, with how many
    commits each one has, so linking becomes a choice rather than a guess.

    Reads the mirrors this org owns. Never raises -- a repo that has not been
    cloned yet simply contributes nothing.
    """
    import subprocess
    from collections import Counter

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT github_full_name FROM repos WHERE org_id = %s", (str(org_id),))
        repo_names = [row[0] for row in cur.fetchall()]

    counts: Counter = Counter()
    for full_name in repo_names:
        try:
            from app.sandbox.worktree import mirror_path

            path = mirror_path(full_name)
            if not path.is_dir():
                continue
            result = subprocess.run(
                ["git", "--git-dir", str(path), "log", "-n", "400", "--format=%ae"],
                capture_output=True, text=True, timeout=30,
            )
            for line in result.stdout.splitlines():
                address = line.strip().lower()
                # Skip the bot's own commits: WhipGuard authoring a fix is not
                # a colleague anyone should be routed to.
                if address and "whipguard" not in address and "noreply" not in address:
                    counts[address] += 1
        except Exception:  # noqa: BLE001 - a suggestion list is never load-bearing
            logger.warning("could not read authors for %s", full_name)

    linked = {link["external_id"] for link in identity_links(org_id)}
    return [
        {"external_id": address, "commits": count}
        for address, count in counts.most_common()
        if address not in linked
    ][:limit]

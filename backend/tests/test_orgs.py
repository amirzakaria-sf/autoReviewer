"""Org membership and the assignment pipeline.

Runs against real Postgres because the pipeline is mostly SQL and ordering,
and a mocked version would assert that this file's own logic matches itself.

The pipeline's contract is that it ALWAYS produces an answer with reasoning.
A routing system that silently returns nobody is worse than one that picks
badly, because nothing surfaces the failure until someone asks why an issue
sat untouched for a week.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app import orgs
from app.enums import OrgRole, Seniority, seniority_rank
from app.retrieval import _sync_dsn

SLUG = "pytest-org"


def _sql(query: str, params: tuple = ()) -> list:
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


def _purge(slug: str) -> None:
    """Delete in dependency order.

    `organizations` has no ON DELETE CASCADE on purpose -- an accidental org
    delete wiping every member is a far worse failure than a fiddly teardown
    -- so the cleanup has to walk the graph itself.
    """
    rows = _sql("SELECT id FROM organizations WHERE slug = %s", (slug,))
    for (org_id,) in rows:
        _sql(
            "DELETE FROM member_designations WHERE member_id IN "
            "(SELECT id FROM org_members WHERE org_id = %s)",
            (str(org_id),),
        )
        for table in ("identity_links", "routing_rules", "designations", "org_members"):
            _sql(f"DELETE FROM {table} WHERE org_id = %s", (str(org_id),))
        _sql("DELETE FROM organizations WHERE id = %s", (str(org_id),))


@pytest.fixture
def org():
    """A small but realistic org: two frontend devs at different levels, one
    backend dev, one admin who holds no designation."""
    # Cleaned at SETUP as well as teardown: a run that fails partway never
    # reaches its teardown, and the next run then collides on the unique
    # email rather than telling you what actually broke.
    _purge(SLUG)
    _sql("DELETE FROM users WHERE email LIKE %s", ("%@pytest.test",))
    org_id = uuid.uuid4()
    _sql(
        "INSERT INTO organizations (id, name, slug, created_at) VALUES (%s, 'Pytest Org', %s, now())",
        (str(org_id), SLUG),
    )
    for key in ("frontend", "backend", "security"):
        _sql(
            "INSERT INTO designations (id, org_id, key, label) VALUES (gen_random_uuid(), %s, %s, %s)",
            (str(org_id), key, key.title()),
        )
    _sql(
        "INSERT INTO routing_rules (id, org_id, category, designation_key, escalate_at_severity, min_seniority) "
        "VALUES (gen_random_uuid(), %s, 'ui', 'frontend', 4, 'SDE3')",
        (str(org_id),),
    )

    people = [
        ("junior@pytest.test", OrgRole.MEMBER, Seniority.SDE1, ["frontend"]),
        ("senior@pytest.test", OrgRole.MEMBER, Seniority.SDE3, ["frontend"]),
        ("backender@pytest.test", OrgRole.MEMBER, Seniority.SDE2, ["backend"]),
        ("boss@pytest.test", OrgRole.ORG_ADMIN, Seniority.STAFF, []),
    ]
    created = {}
    for email, role, seniority, designations in people:
        user_id = uuid.uuid4()
        _sql(
            "INSERT INTO users (id, email, password_hash, role, status, created_at) "
            "VALUES (%s, %s, 'x', 'MEMBER', 'ACTIVE', now())",
            (str(user_id), email),
        )
        member_id = uuid.uuid4()
        _sql(
            "INSERT INTO org_members (id, org_id, user_id, role, seniority, created_at) "
            "VALUES (%s, %s, %s, %s, %s, now())",
            (str(member_id), str(org_id), str(user_id), role.value.upper(), seniority.value.upper()),
        )
        orgs.set_member_designations(member_id, designations, org_id)
        created[email] = {"user_id": str(user_id), "member_id": str(member_id)}

    yield {"id": str(org_id), "people": created}

    _purge(SLUG)
    _sql("DELETE FROM users WHERE email LIKE %s", ("%@pytest.test",))


# --- the three axes ----------------------------------------------------------


def test_role_designation_and_seniority_are_independent(org):
    """"SDE Backend 2" is three facts. Promoting someone to org admin must not
    touch what they know or what they can be escalated."""
    member_id = org["people"]["backender@pytest.test"]["member_id"]
    before = next(m for m in orgs.members(org["id"]) if m["member_id"] == member_id)

    orgs.set_member_role(member_id, OrgRole.ORG_ADMIN)
    after = next(m for m in orgs.members(org["id"]) if m["member_id"] == member_id)

    assert after["role"] == "org_admin"
    assert after["seniority"] == before["seniority"]
    assert after["designations"] == before["designations"]


def test_designations_are_replaced_not_merged(org):
    """The UI is a checklist, so unchecking a box has to actually remove it."""
    member_id = org["people"]["junior@pytest.test"]["member_id"]
    orgs.set_member_designations(member_id, ["backend", "security"], org["id"])
    updated = next(m for m in orgs.members(org["id"]) if m["member_id"] == member_id)
    assert sorted(updated["designations"]) == ["backend", "security"]
    assert "frontend" not in updated["designations"]


def test_seniority_is_ordered_not_alphabetical():
    """Sorted as strings, sde1 sorts above staff and every escalation goes
    the wrong way."""
    assert seniority_rank("staff") > seniority_rank("sde3") > seniority_rank("sde1")


# --- the assignment pipeline -------------------------------------------------


def test_a_linked_git_author_gets_the_issue(org):
    """Stage 1: whoever last worked on the code has the most context."""
    orgs.link_identity(org["id"], org["people"]["backender@pytest.test"]["user_id"], "git-email", "Dev@Pytest.test")

    result = orgs.assign_issue(org_id=org["id"], category="ui", severity=2, author_external_id="dev@pytest.test")

    assert result["assignee"]["email"] == "backender@pytest.test"
    assert any("context" in line for line in result["reasoning"])


def test_an_unknown_author_falls_through_to_designation_routing(org):
    """Stage 2. GitHub noreply addresses and ex-employees are the normal
    case, not the exception -- an unmatched author must never block."""
    result = orgs.assign_issue(
        org_id=org["id"], category="ui", severity=2, author_external_id="ghost@nowhere.test"
    )

    assert result["assignee"]["designations"] == ["frontend"]
    assert any("No WhipGuard account is linked" in line for line in result["reasoning"])


def test_high_severity_escalates_up_the_ladder(org):
    """Stage 3: a critical finding should not sit with an SDE1."""
    result = orgs.assign_issue(org_id=org["id"], category="ui", severity=5)

    assert result["assignee"]["email"] == "senior@pytest.test"
    assert seniority_rank(result["assignee"]["seniority"]) >= seniority_rank("sde3")
    assert any("escalated" in line.lower() for line in result["reasoning"])


def test_escalation_never_moves_an_issue_DOWN_the_ladder(org):
    """The floor is a minimum, not a target. A staff engineer who already
    owns something must not be demoted to an SDE2 because a rule said sde2."""
    orgs.link_identity(org["id"], org["people"]["boss@pytest.test"]["user_id"], "git-email", "boss@pytest.test")

    result = orgs.assign_issue(
        org_id=org["id"], category="ui", severity=5, author_external_id="boss@pytest.test"
    )

    assert result["assignee"]["email"] == "boss@pytest.test"


def test_everyone_else_in_the_designation_becomes_a_watcher(org):
    """The interlinked case: notified, not on the hook."""
    result = orgs.assign_issue(org_id=org["id"], category="ui", severity=2)

    assignee = result["assignee"]["user_id"]
    watcher_ids = {watcher["user_id"] for watcher in result["watchers"]}
    assert assignee not in watcher_ids
    assert watcher_ids, "the other frontend dev should be watching"


def test_a_category_nobody_covers_still_produces_an_owner(org):
    """Stage 5. An issue with no assignee is an issue nobody notices."""
    result = orgs.assign_issue(org_id=org["id"], category="documentation", severity=1)

    assert result["assignee"] is not None
    assert result["assignee"]["role"] == "org_admin"
    assert any("fell back" in line for line in result["reasoning"])


def test_every_assignment_explains_itself(org):
    """Assignment by git history reads as blame unless the reasoning is
    visible. This is the difference between "you broke it" and "you have the
    most context"."""
    for severity in (1, 3, 5):
        result = orgs.assign_issue(org_id=org["id"], category="ui", severity=severity)
        assert result["reasoning"], f"severity {severity} produced an unexplained assignment"


def test_tenancy_is_resolved_through_the_repo_not_a_copied_column(org):
    """One source of truth. If this ever needs a second column somewhere,
    the two can disagree and a tenant sees another tenant's issues."""
    rows = _sql("SELECT column_name FROM information_schema.columns WHERE table_name = 'issues'")
    assert "org_id" not in {row[0] for row in rows}, (
        "issues must reach their org through repo_id, not carry their own copy"
    )


# --- wiring into the council -------------------------------------------------


async def test_a_raised_issue_carries_its_owner_and_the_reason(org, monkeypatch):
    """Routing happens BEFORE the issue is persisted, so the record has an
    owner from the moment it exists -- assigning afterwards leaves a window
    where a notification fires for an unowned finding."""
    import types
    from unittest.mock import MagicMock, patch

    from app.graphs.bug_council import run_and_persist

    captured = {}

    class _DB:
        def add(self, obj):
            captured["issue"] = obj

        async def flush(self):
            pass

        async def commit(self):
            pass

        async def get(self, model, id_):
            return None

    repo = types.SimpleNamespace(
        id=uuid.uuid4(), github_full_name="acme/demo", thresholds=None, ask_mode="balanced"
    )
    # The repo has to belong to the fixture org for routing to find anyone.
    _sql(
        "INSERT INTO repos (id, github_full_name, default_branch, org_id, connected_at) "
        "VALUES (%s, %s, 'main', %s, now())",
        (str(repo.id), f"acme/demo-{repo.id}", org["id"]),
    )
    try:
        graph_result = {
            "score": 95,
            "verdict": "Real bug.",
            "rubric": [],
            "evidence": {"failed": True, "assertion_text": "boom"},
            "needs_clarification": None,
        }
        with (
            patch("app.graphs.bug_council.build_bug_council_graph") as mock_graph,
            patch("app.orgs.last_author_email", return_value=""),
            patch("app.integrations.github_client.create_issue", return_value=7),
            patch("app.retrieval.store_issue_embedding"),
            patch("app.retrieval.find_similar_past_issue", return_value=None),
            patch("app.notifications.record_condition", new_callable=MagicMock),
        ):
            mock_graph.return_value.invoke.return_value = graph_result
            try:
                await run_and_persist(_DB(), repo, category="ui")
            except Exception:
                # Downstream notification plumbing is not what this asserts;
                # the issue object is captured before any of it runs.
                pass

        issue = captured.get("issue")
        assert issue is not None, "no issue was built"
        assert issue.assignee_user_id is not None, "a raised issue must have an owner"
        assert issue.assignment_reasoning, "and must record why it reached them"
    finally:
        _sql("DELETE FROM repos WHERE id = %s", (str(repo.id),))

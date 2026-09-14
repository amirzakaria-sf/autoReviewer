"""Wiring tests for the notification-dedupe calls added to
app.graphs.bug_council.run_and_persist and app.graphs.approval_graph.resolve_approval.

These exercise the REAL should_notify()/mark_notified() logic from
app.notifications (already unit-tested in test_notifications.py) plus the real
wiring added at each call site. record_condition's persistence is faked with a
small in-memory dict that mirrors its actual upsert-by-identity semantics
(see app/notifications.py), since this project has no async-sqlite test harness
and the ORM models use Postgres-only JSONB/UUID column types. All external
calls (GitHub, Cloudflare, Slack, git/subprocess, the sandbox runner, Azure)
are mocked with unittest.mock.patch -- nothing here hits a real network.
"""

from __future__ import annotations

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.enums import FixStatus
from app.graphs.approval_graph import resolve_approval
from app.graphs.bug_council import run_and_persist
from app.models import Fix, Issue, Notification


def _make_record_condition_fake():
    """Stateful stand-in for app.notifications.record_condition. Mirrors the
    real function's upsert-by-(fix_id, issue_id, condition_key) semantics
    without touching a database."""
    rows: dict[tuple, Notification] = {}

    async def _record_condition(db, fix_id, issue_id, condition_key):
        key = (fix_id, issue_id, condition_key)
        existing = rows.get(key)
        if existing is not None:
            existing.occurrence_count += 1
            return existing
        notification = Notification(
            fix_id=fix_id,
            issue_id=issue_id,
            condition_key=condition_key,
            occurrence_count=1,
            notify_count=0,
        )
        rows[key] = notification
        return notification

    return _record_condition


class _FakeDB:
    """Minimal stand-in for an AsyncSession -- only the operations
    run_and_persist / resolve_approval actually call. `fixed_issue_id`, when
    given, is assigned onto any newly-added Issue in place of the real
    ORM-assigned id (which only appears after a real flush against a real
    engine), so repeated calls can simulate "the same condition recurring"."""

    def __init__(self, get_map: dict | None = None, fixed_issue_id=None):
        self._get_map = get_map or {}
        self._fixed_issue_id = fixed_issue_id
        self.added = []
        self.commits = 0

    def add(self, obj):
        if isinstance(obj, Issue) and obj.id is None and self._fixed_issue_id is not None:
            obj.id = self._fixed_issue_id
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def get(self, model, id_):
        return self._get_map.get(model)


def _bug_council_patches(mock_record_condition):
    """Patches for everything run_and_persist's graph touches besides the
    scoring itself, mirroring tests/test_bug_council_graph.py's own patch set."""
    from app.categories import DetectionResult

    mock_detector = MagicMock()
    mock_detector.run.return_value = DetectionResult(failed=True, assertion_text="assertion failed here")

    mock_azure = MagicMock()
    mock_skeptic_opinion = MagicMock(confidence=10, transcript="looks real")
    mock_corroborator_opinion = MagicMock(confidence=90, transcript="mechanical evidence supports it")
    mock_azure.call_skeptic_opinion.return_value = mock_skeptic_opinion
    mock_azure.call_corroborator_opinion.return_value = mock_corroborator_opinion

    return (
        patch("app.graphs.bug_council.get_detector", return_value=mock_detector),
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", mock_azure),
        patch("app.integrations.github_client.create_issue", return_value=101),
        patch("app.notifications.record_condition", new_callable=AsyncMock, side_effect=mock_record_condition),
        patch("app.integrations.slack_client.post_message", return_value="1700000000.000100"),
    )


def _above_threshold_verdict():
    verdict = MagicMock()
    verdict.score = 92
    verdict.factors = []
    verdict.verdict = "clear bug"
    verdict.needs_clarification = None
    return verdict


async def test_bug_raised_notifies_immediately_on_first_occurrence():
    """A raised bug is escalation-worthy on its own: each Issue is a fresh UUID
    per detection (no identity to dedupe "the same bug" across runs), and this
    only fires after already clearing the assurance threshold + mechanical
    recheck — a confirmed, singular event, not a flapping detector. Waiting for
    a second occurrence (as an ordinary condition_key would) means this would
    never fire in production, since occurrence_count of a fresh UUID never
    reaches 2 — this is why bug_council.py passes is_escalation=True."""
    repo = types.SimpleNamespace(id=uuid.uuid4(), github_full_name="acme/demo", thresholds=None)
    db = _FakeDB()
    record_condition_fake = _make_record_condition_fake()

    patches = _bug_council_patches(record_condition_fake)
    with patches[0], patches[1], patches[2], patches[3], patches[4] as mock_azure, patches[5], patches[6] as mock_record_condition, patches[7] as mock_post_message, patch.object(
        settings, "slack_bot_token", "xoxb-test-token"
    ):
        mock_azure.call_skeptic.return_value = "looks real"
        mock_azure.call_arbiter.return_value = _above_threshold_verdict()

        issue = await run_and_persist(db, repo)

    assert issue.github_issue_number == 101
    mock_record_condition.assert_called_once()
    _, kwargs = mock_record_condition.call_args
    assert kwargs["condition_key"] == "bug-raised"
    mock_post_message.assert_called_once()


async def test_bug_raised_empty_slack_token_does_not_crash_the_flow():
    """No live Slack app configured yet (slack_bot_token == '') -- issue
    creation must still succeed without raising, and without a real Slack call
    succeeding (mirrors the approval-flow empty-token test below)."""
    repo = types.SimpleNamespace(id=uuid.uuid4(), github_full_name="acme/demo", thresholds=None)
    db = _FakeDB()
    record_condition_fake = _make_record_condition_fake()

    patches = _bug_council_patches(record_condition_fake)
    with patches[0], patches[1], patches[2], patches[3], patches[4] as mock_azure, patches[5], patches[6], patches[7] as mock_post_message, patch.object(
        settings, "slack_bot_token", ""
    ):
        mock_azure.call_skeptic.return_value = "looks real"
        mock_azure.call_arbiter.return_value = _above_threshold_verdict()

        issue = await run_and_persist(db, repo)

    assert issue.github_issue_number == 101
    mock_post_message.assert_not_called()


def _make_fix():
    fix = Fix(issue_id=uuid.uuid4(), branch_name="whipguard/fix-abc123", status=FixStatus.AWAITING_APPROVAL)
    fix.id = uuid.uuid4()
    return fix


def _make_issue():
    issue = Issue(repo_id=uuid.uuid4(), title="Deleting an item removes the wrong one")
    issue.id = uuid.uuid4()
    return issue


def _approval_patches(mock_record_condition, sandbox_exit_code):
    return (
        patch("app.graphs.approval_graph.subprocess.run", side_effect=[MagicMock(returncode=0), MagicMock(returncode=0)]),
        patch("app.graphs.approval_graph.run_in_sandbox", return_value=(sandbox_exit_code, "", "post-deploy failed")),
        patch("app.integrations.github_client.push_branch"),
        patch("app.integrations.cloudflare_client.deploy_branch", return_value="https://preview.example.com"),
        patch("app.graphs.approval_graph.record_condition", new_callable=AsyncMock, side_effect=mock_record_condition),
        patch("app.integrations.slack_client.post_message", return_value="1700000000.000200"),
    )


async def test_verification_failed_notifies_immediately_on_first_occurrence():
    """Escalation (is_escalation=True) bypasses the 2-occurrence minimum --
    the very first verification failure for a fix must still notify."""
    fix = _make_fix()
    issue = _make_issue()
    db = _FakeDB(get_map={Fix: fix, Issue: issue})
    record_condition_fake = _make_record_condition_fake()

    patches = _approval_patches(record_condition_fake, sandbox_exit_code=1)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mock_post_message, patch.object(
        settings, "slack_bot_token", "xoxb-test-token"
    ):
        result = await resolve_approval(db, fix.id, approved=True, actor="tester", surface="dashboard")

    assert result["status"] == FixStatus.VERIFICATION_FAILED.value
    mock_post_message.assert_called_once()


async def test_empty_slack_bot_token_does_not_crash_the_flow():
    """No live Slack app configured yet (slack_bot_token == '') -- the
    escalation path must still complete the approval flow without raising,
    and without a real Slack call succeeding."""
    fix = _make_fix()
    issue = _make_issue()
    db = _FakeDB(get_map={Fix: fix, Issue: issue})
    record_condition_fake = _make_record_condition_fake()

    patches = _approval_patches(record_condition_fake, sandbox_exit_code=1)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mock_post_message, patch.object(
        settings, "slack_bot_token", ""
    ):
        result = await resolve_approval(db, fix.id, approved=True, actor="tester", surface="dashboard")

    assert result["ok"] is True
    assert result["status"] == FixStatus.VERIFICATION_FAILED.value
    mock_post_message.assert_not_called()

"""The memory layer is only worth its cost if something actually WRITES to
it, and only correct if the briefing actually reaches the jury.

Both are easy to break silently: `record_trace` swallows every exception by
design (bookkeeping must never fail a run), and a briefing that comes back
empty looks identical to one that was never requested. These tests assert
the wiring rather than the internals, which are covered in
test_retrieval_core.py.
"""

from __future__ import annotations

import types
import uuid
from unittest.mock import MagicMock, patch

from app.categories import DetectionResult
from app.graphs.bug_council import _evidence_suffix, detect_node


def _repo():
    return types.SimpleNamespace(
        id=uuid.uuid4(), github_full_name="acme/demo", thresholds=None, slack_channel_id="C1", ask_mode="balanced"
    )


def test_a_failing_detection_builds_a_briefing_while_the_worktree_exists():
    """The worktree is deleted in detect_node's own `finally`, so a briefing
    assembled any later than this would be retrieving against a directory
    that no longer exists."""
    repo_id = uuid.uuid4()
    state = {"repo_full_name": "acme/demo", "repo_id": repo_id, "category": "ui"}

    detector = MagicMock()
    detector.run.return_value = DetectionResult(failed=True, assertion_text="expected 2 got 3")

    with (
        patch("app.graphs.bug_council.get_detector", return_value=detector),
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree", return_value="/tmp/wt"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council._read_entry_files", return_value="source here"),
        patch("app.context_broker.build_briefing", return_value="CONTEXT BRIEFING\n\nsymbols…") as mock_briefing,
    ):
        out = detect_node(state)

    mock_briefing.assert_called_once()
    assert mock_briefing.call_args.kwargs["worktree_path"] == "/tmp/wt"
    assert out["briefing"].startswith("CONTEXT BRIEFING")


def test_a_clean_detection_does_not_pay_for_a_briefing():
    """Assembling one for a run with nothing to judge costs index reads to
    inform nobody."""
    state = {"repo_full_name": "acme/demo", "repo_id": uuid.uuid4(), "category": "ui"}

    detector = MagicMock()
    detector.run.return_value = DetectionResult(failed=False, assertion_text="all passed")

    with (
        patch("app.graphs.bug_council.get_detector", return_value=detector),
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree", return_value="/tmp/wt"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council._read_entry_files", return_value=""),
        patch("app.context_broker.build_briefing") as mock_briefing,
    ):
        out = detect_node(state)

    mock_briefing.assert_not_called()
    assert out["briefing"] == ""


def test_every_jury_role_sees_the_briefing():
    """Skeptic, Corroborator and Arbiter all build their user message from
    _evidence_suffix. If the briefing were missing here, the whole retrieval
    and memory layer would be assembled and then shown to nobody."""
    suffix = _evidence_suffix(
        {
            "evidence": {"assertion_text": "expected 2 got 3", "source_excerpt": "const x = 1;"},
            "briefing": "CONTEXT BRIEFING\n\nSYMBOLS THIS FINDING MENTIONS: deleteItem -- app.js:22",
        },
    )
    assert "expected 2 got 3" in suffix
    assert "CONTEXT BRIEFING" in suffix
    assert "deleteItem" in suffix


def test_the_failing_assertion_survives_a_budget_the_rest_cannot():
    """Priority ordering, at the one place it decides what a jury sees: the
    assertion being judged is MANDATORY, the raw source dump is the first
    thing dropped."""
    suffix = _evidence_suffix(
        {
            "evidence": {"assertion_text": "expected 2 got 3", "source_excerpt": "x" * 400_000},
            "briefing": "CONTEXT BRIEFING body",
        },
    )
    assert "expected 2 got 3" in suffix
    assert len(suffix) < 400_000, "the low-priority source dump must be trimmed, not passed through whole"


async def test_a_below_threshold_finding_is_remembered():
    """The most common real outcome: the detector failed, the jury did not
    think it was worth raising. Recording nothing here is what starved the
    memory corpus of exactly the cases most worth remembering, so the same
    marginal finding got re-argued from scratch on every scan."""
    from app.graphs.bug_council import run_and_persist

    repo = _repo()
    graph_result = {
        "score": 30,
        "verdict": "Probably intentional styling.",
        "rubric": [],
        "evidence": {"failed": True, "assertion_text": "contrast 3.1:1 below 4.5:1"},
        "needs_clarification": None,
    }

    class _DB:
        def add(self, obj):
            pass

        async def flush(self):
            pass

        async def commit(self):
            pass

        async def get(self, model, id_):
            return None

    with (
        patch("app.graphs.bug_council.build_bug_council_graph") as mock_graph,
        patch("app.memory_traces.record_trace") as mock_trace,
    ):
        mock_graph.return_value.invoke.return_value = graph_result
        await run_and_persist(_DB(), repo, category="accessibility")

    mock_trace.assert_called_once()
    kwargs = mock_trace.call_args.kwargs
    assert kwargs["outcome"] == "below-threshold"
    assert kwargs["stage"] == "detect"
    assert kwargs["repo_id"] == repo.id
    assert "30" in kwargs["detail"], "the score it fell short by belongs in the trace"


async def test_a_clean_scan_records_no_trace():
    """Nothing failed, so there is nothing to remember -- a trace here would
    be noise the next run has to read past."""
    from app.graphs.bug_council import run_and_persist

    repo = _repo()
    graph_result = {
        "score": 0,
        "verdict": "Nothing detected.",
        "rubric": [],
        "evidence": {"failed": False, "assertion_text": "all passed"},
        "needs_clarification": None,
    }

    class _DB:
        def add(self, obj):
            pass

        async def flush(self):
            pass

        async def commit(self):
            pass

        async def get(self, model, id_):
            return None

    with (
        patch("app.graphs.bug_council.build_bug_council_graph") as mock_graph,
        patch("app.memory_traces.record_trace") as mock_trace,
    ):
        mock_graph.return_value.invoke.return_value = graph_result
        await run_and_persist(_DB(), repo, category="ui")

    mock_trace.assert_not_called()

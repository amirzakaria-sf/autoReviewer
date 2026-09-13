from unittest.mock import MagicMock, patch

from app.graphs.bug_council import build_bug_council_graph


def _mock_evidence_failed():
    return {"evidence": {"failed": True, "assertion_text": "expected X, got Y", "stderr": ""}}


def test_routes_to_raise_above_threshold():
    with (
        patch("app.graphs.bug_council.run_in_sandbox") as mock_sandbox,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client") as mock_azure,
    ):
        mock_sandbox.return_value = (1, "assertion failed here", "")
        mock_azure.call_skeptic.return_value = "no reason to doubt this"

        verdict = MagicMock()
        verdict.score = 92
        verdict.factors = []
        verdict.verdict = "clear bug"
        mock_azure.call_arbiter.return_value = verdict

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui"})

    assert result["score"] == 92


def test_routes_to_hold_below_threshold():
    with (
        patch("app.graphs.bug_council.run_in_sandbox") as mock_sandbox,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client") as mock_azure,
    ):
        mock_sandbox.return_value = (1, "assertion failed here", "")
        mock_azure.call_skeptic.return_value = "might be a flake"

        verdict = MagicMock()
        verdict.score = 40
        verdict.factors = []
        verdict.verdict = "weak evidence"
        mock_azure.call_arbiter.return_value = verdict

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui"})

    assert result["score"] == 40


def test_mechanical_recheck_flake_zeroes_the_score_before_any_second_model_call():
    """A flake that passes on rerun is dropped in code, before the Arbiter is
    ever asked to score it a second time (plan.md §3)."""
    with (
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client") as mock_azure,
        patch("app.graphs.bug_council.run_in_sandbox") as mock_sandbox,
    ):
        # First call (detect) fails, second call (mechanical recheck) passes -> flake.
        mock_sandbox.side_effect = [(1, "failed once", ""), (0, "passed on rerun", "")]
        mock_azure.call_skeptic.return_value = "looks real to me"

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui"})

    assert result["score"] == 0
    mock_azure.call_arbiter.assert_not_called()

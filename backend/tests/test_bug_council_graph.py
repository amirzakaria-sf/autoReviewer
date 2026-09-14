from unittest.mock import MagicMock, patch

from app.categories import DetectionResult
from app.graphs.bug_council import DISAGREEMENT_THRESHOLD, build_bug_council_graph, _juries_disagree


def _opinion(confidence, transcript="transcript"):
    m = MagicMock()
    m.confidence = confidence
    m.transcript = transcript
    return m


def _verdict(score, verdict="verdict"):
    m = MagicMock()
    m.score = score
    m.factors = []
    m.verdict = verdict
    m.needs_clarification = None
    return m


def _agreeing_mock_azure(skeptic_not_bug_confidence=10, corroborator_is_bug_confidence=90, arbiter_score=92):
    """Both roles point the same direction: Skeptic is NOT confident it's a
    false positive, Corroborator IS confident it's real -- agreement."""
    mock_azure = MagicMock()
    mock_azure.call_skeptic_opinion.return_value = _opinion(skeptic_not_bug_confidence)
    mock_azure.call_corroborator_opinion.return_value = _opinion(corroborator_is_bug_confidence)
    mock_azure.call_arbiter.return_value = _verdict(arbiter_score)
    return mock_azure


def test_routes_to_raise_above_threshold():
    with (
        patch("app.graphs.bug_council.get_detector") as mock_get_detector,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", _agreeing_mock_azure()),
    ):
        mock_detector = MagicMock()
        mock_detector.run.return_value = DetectionResult(failed=True, assertion_text="assertion failed here")
        mock_get_detector.return_value = mock_detector

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui", "category": "ui"})

    assert result["score"] == 92
    assert not result.get("meta_audited")


def test_routes_to_hold_below_threshold():
    with (
        patch("app.graphs.bug_council.get_detector") as mock_get_detector,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", _agreeing_mock_azure(skeptic_not_bug_confidence=60, corroborator_is_bug_confidence=40, arbiter_score=40)),
    ):
        mock_detector = MagicMock()
        mock_detector.run.return_value = DetectionResult(failed=True, assertion_text="assertion failed here")
        mock_get_detector.return_value = mock_detector

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui", "category": "ui"})

    assert result["score"] == 40


def test_mechanical_recheck_flake_zeroes_the_score_before_any_second_model_call():
    """A flake that passes on rerun is dropped in code, before the Arbiter is
    ever asked to score it a second time (plan.md §3)."""
    with (
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", _agreeing_mock_azure()) as mock_azure,
        patch("app.graphs.bug_council.get_detector") as mock_get_detector,
    ):
        mock_detector = MagicMock()
        # First call (detect) fails, second call (mechanical recheck) passes -> flake.
        mock_detector.run.side_effect = [
            DetectionResult(failed=True, assertion_text="failed once"),
            DetectionResult(failed=False, assertion_text="passed on rerun"),
        ]
        mock_get_detector.return_value = mock_detector

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui", "category": "ui"})

    assert result["score"] == 0
    mock_azure.call_arbiter.assert_not_called()


def test_routes_using_backend_category_threshold():
    """The backend category has a different (higher) resolution threshold than
    ui, per the registry -- confirms route_on_score reads the category's own
    config row rather than a single hardcoded threshold."""
    with (
        patch("app.graphs.bug_council.get_detector") as mock_get_detector,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", _agreeing_mock_azure(arbiter_score=80)),
    ):
        mock_detector = MagicMock()
        mock_detector.run.return_value = DetectionResult(failed=True, assertion_text="calculateTotal returned 15, expected 40")
        mock_get_detector.return_value = mock_detector

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui", "category": "backend"})

        mock_get_detector.assert_called_with("backend")

    assert result["score"] == 80


def test_juries_disagree_normalizes_opposite_polarity_before_comparing():
    """Skeptic's confidence is "confident NOT a bug" and Corroborator's is
    "confident IS a bug" -- opposite polarities. Both reporting "90% confident"
    is NOT agreement (that's each fully committed to the opposite
    conclusion) -- a naive abs(90-90)=0 would wrongly call that agreement."""
    state = {"skeptic_confidence": 90, "corroborator_confidence": 90}
    assert _juries_disagree(state) is True

    agreeing_state = {"skeptic_confidence": 10, "corroborator_confidence": 90}
    assert _juries_disagree(agreeing_state) is False


def test_disagreement_triggers_meta_audit_and_overrides_the_score():
    """Skeptic strongly believes it's NOT a bug, Corroborator strongly
    believes it IS -- a sharp disagreement (plan.md §10.2) that must escalate
    to the meta-auditor rather than silently trusting the first-pass Arbiter."""
    mock_azure = MagicMock()
    mock_azure.call_skeptic_opinion.return_value = _opinion(90, "this looks like an environment issue, not a real bug")
    mock_azure.call_corroborator_opinion.return_value = _opinion(95, "the mechanical evidence clearly reproduces a real defect")
    mock_azure.call_arbiter.return_value = _verdict(60, "first-pass: uncertain")
    mock_azure.call_meta_auditor.return_value = _verdict(85, "meta-audit: the corroborator's mechanical evidence is more credible")

    with (
        patch("app.graphs.bug_council.get_detector") as mock_get_detector,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", mock_azure),
    ):
        mock_detector = MagicMock()
        mock_detector.run.return_value = DetectionResult(failed=True, assertion_text="assertion failed here")
        mock_get_detector.return_value = mock_detector

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui", "category": "ui"})

    mock_azure.call_meta_auditor.assert_called_once()
    assert result["score"] == 85
    assert result["meta_audited"] is True
    assert "[Meta-audited]" in result["verdict"]


def test_arbiter_needs_clarification_short_circuits_before_score_routing():
    """plan.md §10.5: forcing a confident score out of a model missing a fact
    only a human has manufactures false confidence -- needs_clarification
    must short-circuit BEFORE the disagreement check and BEFORE route_on_score,
    not be treated as an ordinary low score."""
    mock_azure = MagicMock()
    mock_azure.call_skeptic_opinion.return_value = _opinion(20)
    mock_azure.call_corroborator_opinion.return_value = _opinion(80)
    verdict = MagicMock()
    verdict.needs_clarification = {"question": "Is this hardcoded value intentional for this deployment?", "options": ["Yes, intentional", "No, it's a bug"]}
    mock_azure.call_arbiter.return_value = verdict

    with (
        patch("app.graphs.bug_council.get_detector") as mock_get_detector,
        patch("app.graphs.bug_council.ensure_mirror"),
        patch("app.graphs.bug_council.create_worktree"),
        patch("app.graphs.bug_council.remove_worktree"),
        patch("app.graphs.bug_council.azure_client", mock_azure),
    ):
        mock_detector = MagicMock()
        mock_detector.run.return_value = DetectionResult(failed=True, assertion_text="assertion failed here")
        mock_get_detector.return_value = mock_detector

        graph = build_bug_council_graph()
        result = graph.invoke({"repo_full_name": "amirzakaria-sf/whipguard-demo-ui", "category": "ui"})

    mock_azure.call_meta_auditor.assert_not_called()
    assert result["needs_clarification"]["question"].startswith("Is this hardcoded value")
    assert result["score"] == -1

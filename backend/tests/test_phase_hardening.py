from __future__ import annotations

import hashlib
import hmac
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.boot_checks import refuse_insecure_defaults, secrets_are_insecure
from app.categories import DetectionResult, category_from_labels, entry_files_for
from app.config import settings
from app.detectors.backend import discover_backend_command
from app.detectors.security import SecurityDetector
from app.graphs.bug_council import _merge_dependabot
from app.graphs.fix_council import route_after_patch, route_on_resolution_score
from app.integrations.github_client import verify_webhook_signature
from app.preview import probe_preview_url
from app.workspace_map import build_workspace_map


def test_webhook_signature_accepts_a_valid_hex_digest():
    body = b'{"zen":"ok"}'
    secret = "top-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, f"sha256={digest}", secret) is True


def test_webhook_signature_rejects_a_wrong_secret():
    body = b'{"zen":"ok"}'
    digest = hmac.new(b"other", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, f"sha256={digest}", "top-secret") is False


def test_category_from_labels_reads_the_registry_key():
    assert category_from_labels([{"name": "whipguard:fix-me"}, {"name": "whipguard:category/backend"}]) == "backend"
    assert category_from_labels(["whipguard:fix-me"]) == "ui"
    assert category_from_labels([]) == "ui"


def test_entry_files_for_keeps_fixture_defaults_when_those_files_exist(tmp_path):
    (tmp_path / "app.js").write_text("ok")
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "style.css").write_text("body{}")
    assert entry_files_for("ui", str(tmp_path)) == ("app.js",)
    assert entry_files_for("accessibility", str(tmp_path)) == ("index.html", "style.css")


def test_entry_files_for_infers_a_next_app_when_fixture_files_are_absent(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.tsx").write_text("export default function App(){}")
    (tmp_path / "package.json").write_text("{}")
    assert "src/App.tsx" in entry_files_for("ui", str(tmp_path))


def test_workspace_map_does_not_claim_the_fixture_stack_for_an_unseen_repo():
    text = build_workspace_map("acme/other")
    assert "static HTML/CSS/vanilla JS" not in text
    assert "not yet inspected" in text or "unknown" in text


def test_workspace_map_describes_a_python_layout(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    text = build_workspace_map("acme/svc", worktree_path=str(tmp_path))
    assert "python" in text


def test_backend_command_discovers_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert discover_backend_command(str(tmp_path)) == ["python -m pytest -q"]


def test_backend_command_discovers_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    assert discover_backend_command(str(tmp_path)) == ["go test ./..."]


def test_backend_command_discovers_cargo(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"x\"\n")
    assert discover_backend_command(str(tmp_path)) == ["cargo test"]


def test_backend_command_keeps_the_fixture_node_glob(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "add.test.js").write_text("ok")
    assert discover_backend_command(str(tmp_path)) == ["node --test backend/*.test.js"]


def test_security_detector_never_puts_the_secret_value_in_evidence(tmp_path):
    (tmp_path / "app.js").write_text('const DEBUG_AWS_KEY = "AKIAQ7X3K9M2P5R8T1WZ";\n')
    result = SecurityDetector().run(str(tmp_path))
    assert result.failed is True
    assert "AKIAQ7X3K9M2P5R8T1WZ" not in result.assertion_text
    assert "AWS Access Key ID" in result.assertion_text


def test_preview_probe_treats_http_200_as_reachable():
    class _Resp:
        status_code = 200

    with patch("app.preview.httpx.get", return_value=_Resp()):
        assert probe_preview_url("https://example.pages.dev") is True


def test_preview_probe_treats_an_empty_url_as_unreachable():
    assert probe_preview_url("") is False


def test_insecure_defaults_are_refused_when_the_escape_hatch_is_off():
    from app.config import settings

    with (
        patch.object(settings, "admin_password", "whipguard-demo"),
        patch.object(settings, "session_secret", "change-me-in-real-deployments"),
        patch.object(settings, "allow_insecure_defaults", False),
    ):
        assert secrets_are_insecure() is True
        try:
            refuse_insecure_defaults()
            raise AssertionError("should have refused")
        except RuntimeError as error:
            assert "ADMIN_PASSWORD" in str(error)


def test_insecure_defaults_are_allowed_when_the_escape_hatch_is_on():
    from app.config import settings

    with (
        patch.object(settings, "admin_password", "whipguard-demo"),
        patch.object(settings, "allow_insecure_defaults", True),
    ):
        refuse_insecure_defaults()  # must not raise


def test_fix_council_retry_is_a_second_attempt_not_end():
    assert route_on_resolution_score(
        {"score": 10, "attempt": 1, "category": "ui", "resolution_threshold": 80}
    ) == "retry"
    assert route_on_resolution_score(
        {"score": 10, "attempt": 2, "category": "ui", "resolution_threshold": 80}
    ) == "hold"
    assert route_on_resolution_score(
        {"score": 90, "attempt": 1, "category": "ui", "resolution_threshold": 80}
    ) == "propose"


def test_ask_human_routes_the_patch_loop_to_end_instead_of_the_verifier():
    assert route_after_patch({"needs_human": {"question": "which helper?"}}) == "ask"
    assert route_after_patch({}) == "verify"


def test_retry_edge_on_the_compiled_graph_is_not_end():
    from app.graphs.fix_council import build_fix_council_graph

    compiled = build_fix_council_graph()
    nodes = compiled.get_graph().nodes
    if isinstance(nodes, dict):
        names = set(nodes)
    else:
        names = {getattr(n, "id", getattr(n, "name", str(n))) for n in nodes}
    assert "retry_prepare" in names


def test_dependabot_alerts_fail_the_scan_without_dropping_regex_findings():
    result = DetectionResult(failed=True, assertion_text="app.js:1: AWS Access Key ID")
    with patch(
        "app.integrations.github_client.list_dependabot_alerts",
        return_value=[{"security_advisory": {"ghsa_id": "GHSA-x", "summary": "lodash proto"}}],
    ):
        merged = _merge_dependabot("acme/svc", result)
    assert merged.failed is True
    assert "AWS Access Key ID" in merged.assertion_text
    assert "GHSA-x" in merged.assertion_text


def test_kill_switch_round_trips_through_app_settings():
    store: dict[str, str] = {}
    with (
        patch("app.app_settings.get_setting", side_effect=lambda key, default="": store.get(key, default)),
        patch("app.app_settings.set_setting", side_effect=lambda key, value: store.__setitem__(key, value)),
    ):
        from app import kill_switch

        assert kill_switch.detection_paused() is False
        kill_switch.set_paused(detection=True, proposals=False)
        assert kill_switch.detection_paused() is True
        assert kill_switch.proposals_paused() is False


class _FakeWebhookRequest:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    async def body(self) -> bytes:
        return self._body


async def test_github_webhook_rejects_a_bad_signature():
    from app.routers.webhooks import github_webhook

    request = _FakeWebhookRequest(b'{"zen":"ok"}', {"X-Hub-Signature-256": "sha256=00"})
    with patch.object(settings, "github_webhook_secret", "sekrit"):
        try:
            await github_webhook(request, db=MagicMock())
            raise AssertionError("should have refused")
        except HTTPException as error:
            assert error.status_code == 401


async def test_prd_below_coverage_floor_is_bannered_as_incomplete():
    from app.counsel.prd import PrdResult, apply_coverage_floor

    result = PrdResult(
        title="Feasibility",
        summary="thin",
        requirements=[{"requirement": "streaming", "bucket": "build-new"}],
        coverage_score=12,
        ungrounded=[],
        markdown="## Draft\n",
    )
    apply_coverage_floor(result)
    assert "below the 50 floor" in result.markdown
    assert result.ungrounded


async def test_prd_at_the_floor_is_not_bannered():
    from app.counsel.prd import PrdResult, apply_coverage_floor

    result = PrdResult(title="ok", summary="ok", coverage_score=50, markdown="## Draft\n")
    apply_coverage_floor(result)
    assert "floor" not in result.markdown

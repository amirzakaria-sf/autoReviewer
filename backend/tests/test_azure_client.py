import json
from unittest.mock import MagicMock, patch

from app.azure_client import ArbiterVerdict, call_arbiter, call_patch_worker, call_skeptic, call_verifier
from app.config import settings


def _fake_response(content: str):
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def test_skeptic_uses_fast_deployment():
    with patch("app.azure_client._client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("skeptical take")
        mock_client_factory.return_value = mock_client

        result = call_skeptic("prefix", "suffix")

        assert result == "skeptical take"
        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == settings.azure_fast_deployment


def test_verifier_and_patch_worker_use_worker_deployment():
    with patch("app.azure_client._client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("ok")
        mock_client_factory.return_value = mock_client

        call_verifier("p", "s")
        call_patch_worker("p", "s")

        for call in mock_client.chat.completions.create.call_args_list:
            assert call.kwargs["model"] == settings.azure_worker_deployment


def test_arbiter_uses_planner_deployment_and_validates_schema():
    payload = {
        "score": 80,
        "factors": [{"factor": "x", "weight": 80, "note": "y"}],
        "verdict": "looks real",
        "needs_clarification": None,
    }
    with patch("app.azure_client._client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(json.dumps(payload))
        mock_client_factory.return_value = mock_client

        verdict = call_arbiter("prefix", "suffix")

        assert isinstance(verdict, ArbiterVerdict)
        assert verdict.score == 80
        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == settings.azure_planner_deployment

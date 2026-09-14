from unittest.mock import MagicMock, patch

from app.graphs.fix_council import build_workspace_map, patch_generation_node
from app.prompts import build_prefix


def test_retry_stable_prefix_is_byte_identical_across_attempts(tmp_path):
    """Direct test of the §9.6 contract: the stable prefix passed to
    PatchGenerationNode's model call must be byte-identical on a retry — only the
    volatile suffix (the rejection reason) may differ."""
    (tmp_path / "app.js").write_text("function deleteItem(i) { items.splice(i - 1, 1); }")

    captured_system_prompts: list[str] = []

    def fake_create(model, messages, tools):
        captured_system_prompts.append(messages[0]["content"])
        finish_message = MagicMock()
        finish_message.tool_calls = [
            MagicMock(id="call1", function=MagicMock(name="finish_patch", arguments="{}"))
        ]
        finish_message.tool_calls[0].function.name = "finish_patch"
        finish_message.model_dump.return_value = {"role": "assistant", "content": None}
        response = MagicMock()
        response.choices = [MagicMock(message=finish_message)]
        return response

    with patch("app.graphs.fix_council.AzureOpenAI") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create
        mock_client_cls.return_value = mock_client

        state1 = {
            "worktree_path": str(tmp_path), "category": "ui", "touched_files": ["app.js"],
            "attempt": 1, "prior_rejection": None,
        }
        patch_generation_node(state1)

        state2 = {
            "worktree_path": str(tmp_path),
            "category": "ui",
            "touched_files": ["app.js"],
            "attempt": 2,
            "prior_rejection": "patch touched the add handler too, out of scope",
        }
        patch_generation_node(state2)

    assert len(captured_system_prompts) == 2
    assert captured_system_prompts[0] == captured_system_prompts[1], (
        "stable prefix (system prompt) must be byte-identical across attempts"
    )

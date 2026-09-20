"""PatchGenerationNode's contract with the model.

The node no longer builds its own Azure client, so these patch
`azure_client.complete_turn` and assert on the Responses-shaped history the
node hands it -- which is the thing that actually has to stay right, since a
mis-shaped history is a 400 in production and an invisible no-op in a mock
that shrugs.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from app.azure_client import ModelTurn, ToolCall
from app.graphs.fix_council import patch_generation_node


def _turn(tool_calls=None, content="", reasoning_items=None):
    return ModelTurn(
        content=content,
        tool_calls=list(tool_calls or []),
        reasoning_items=list(reasoning_items or []),
        protocol="responses",
    )


def _finish(call_id="finish_1"):
    return ToolCall(name="finish_patch", arguments={"summary": "done"}, id=call_id)


def _git_worktree(tmp_path, contents: str = "function deleteItem(i) { items.splice(i - 1, 1); }\n"):
    (tmp_path / "app.js").write_text(contents)
    for command in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(command, cwd=str(tmp_path), check=True, capture_output=True)
    return tmp_path


def _state(worktree, **overrides):
    base = {
        "worktree_path": str(worktree),
        "category": "ui",
        "touched_files": ["app.js"],
        "attempt": 1,
        "prior_rejection": None,
        "repo_full_name": "acme/demo",
    }
    base.update(overrides)
    return base


def test_retry_stable_prefix_is_byte_identical_across_attempts(tmp_path):
    """The §9.6 contract: the system item is byte-identical on a retry. Only
    the volatile suffix (the rejection reason) may differ."""
    worktree = _git_worktree(tmp_path)
    systems: list[str] = []
    users: list[str] = []

    def fake_complete_turn(**kwargs):
        items = kwargs["messages"]
        systems.append(next(item["content"] for item in items if item.get("role") == "system"))
        users.append(next(item["content"] for item in items if item.get("role") == "user"))
        return _turn([_finish()])

    with patch("app.azure_client.complete_turn", side_effect=fake_complete_turn):
        patch_generation_node(_state(worktree))
        patch_generation_node(_state(worktree, attempt=2, prior_rejection="touched the add handler too"))

    assert len(systems) == 2
    assert systems[0] == systems[1], "stable prefix (system item) must be byte-identical across attempts"
    assert users[0] != users[1], "the rejection reason belongs in the volatile half"


def test_the_prompt_cache_key_is_stable_across_attempts_too(tmp_path):
    worktree = _git_worktree(tmp_path)
    keys: list[str] = []

    def fake_complete_turn(**kwargs):
        keys.append(kwargs["prompt_cache_key"])
        return _turn([_finish()])

    with patch("app.azure_client.complete_turn", side_effect=fake_complete_turn):
        patch_generation_node(_state(worktree))
        patch_generation_node(_state(worktree, attempt=2, prior_rejection="try again"))

    assert keys[0] == keys[1]
    assert keys[0].startswith("acme/demo:patch_worker:")


def test_patch_generation_is_never_served_from_the_response_cache(tmp_path):
    worktree = _git_worktree(tmp_path)
    seen: list[bool] = []

    def fake_complete_turn(**kwargs):
        seen.append(kwargs["cacheable"])
        return _turn([_finish()])

    with patch("app.azure_client.complete_turn", side_effect=fake_complete_turn):
        patch_generation_node(_state(worktree))

    assert seen == [False]


def test_apply_patch_changes_only_the_span_it_named(tmp_path):
    """The whole reason apply_patch displaced write_file: the diff is the size
    of the edit, not the size of the file."""
    worktree = _git_worktree(
        tmp_path,
        "function deleteItem(i) { items.splice(i - 1, 1); }\nfunction addItem(x) { items.push(x); }\n",
    )
    turns = [
        _turn([ToolCall(
            name="apply_patch",
            arguments={"path": "app.js", "old_string": "items.splice(i - 1, 1)", "new_string": "items.splice(i, 1)"},
            id="c1",
        )]),
        _turn([_finish()]),
    ]

    with patch("app.azure_client.complete_turn", side_effect=turns):
        result = patch_generation_node(_state(worktree))

    assert (worktree / "app.js").read_text() == (
        "function deleteItem(i) { items.splice(i, 1); }\nfunction addItem(x) { items.push(x); }\n"
    )
    changed = [line for line in result["diff"].splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert len(changed) == 2, "a one-line edit must not produce a whole-file diff"
    assert "addItem" not in "\n".join(changed)


def test_the_committed_patch_is_what_approval_will_push(tmp_path):
    """Committed at proposal time, once. Approval pushes THIS commit rather
    than regenerating one against a moved HEAD."""
    worktree = _git_worktree(tmp_path)
    turns = [
        _turn([ToolCall(
            name="apply_patch",
            arguments={"path": "app.js", "old_string": "i - 1", "new_string": "i"},
            id="c1",
        )]),
        _turn([_finish()]),
    ]
    with patch("app.azure_client.complete_turn", side_effect=turns):
        patch_generation_node(_state(worktree))

    log = subprocess.run(["git", "log", "--oneline"], cwd=str(worktree), capture_output=True, text=True)
    assert "WhipGuard: fix ui issue" in log.stdout
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(worktree), capture_output=True, text=True)
    assert status.stdout.strip() == "", "nothing may be left uncommitted for approval to miss"


def test_a_refused_write_comes_back_as_a_tool_result_the_model_can_act_on(tmp_path):
    worktree = _git_worktree(tmp_path)
    histories: list[list[dict]] = []

    turns = [
        _turn([ToolCall(
            name="apply_patch",
            arguments={"path": "backend/server.js", "old_string": "a", "new_string": "b"},
            id="c1",
        )]),
        _turn([_finish()]),
    ]

    def fake_complete_turn(**kwargs):
        histories.append([dict(item) for item in kwargs["messages"]])
        return turns.pop(0)

    with patch("app.azure_client.complete_turn", side_effect=fake_complete_turn):
        patch_generation_node(_state(worktree))

    outputs = [item for item in histories[1] if item.get("type") == "function_call_output"]
    assert outputs and outputs[0]["call_id"] == "c1"
    assert "write scope" in outputs[0]["output"]


def test_the_history_replayed_on_the_next_tick_is_responses_shaped(tmp_path):
    worktree = _git_worktree(tmp_path)
    histories: list[list[dict]] = []
    turns = [
        _turn(
            [ToolCall(name="read_file", arguments={"path": "app.js"}, id="c1")],
            content="Let me look at the file.",
            reasoning_items=[{"type": "reasoning", "id": "rs_1", "summary": []}],
        ),
        _turn([_finish()]),
    ]

    def fake_complete_turn(**kwargs):
        histories.append([dict(item) for item in kwargs["messages"]])
        return turns.pop(0)

    with patch("app.azure_client.complete_turn", side_effect=fake_complete_turn):
        patch_generation_node(_state(worktree))

    second = histories[1]
    types = [item.get("type") or item.get("role") for item in second]
    assert types == ["system", "user", "reasoning", "assistant", "function_call", "function_call_output"]
    call_item = next(item for item in second if item.get("type") == "function_call")
    assert isinstance(call_item["arguments"], str), "arguments must be a JSON string, not a dict"
    assert json.loads(call_item["arguments"]) == {"path": "app.js"}
    assert second[-1]["call_id"] == "c1"


def test_the_loop_tripwire_still_fires_on_a_repeated_identical_call(tmp_path):
    worktree = _git_worktree(tmp_path)
    repeated = ToolCall(name="read_file", arguments={"path": "app.js"}, id="same")

    with patch("app.azure_client.complete_turn", return_value=_turn([repeated])):
        result = patch_generation_node(_state(worktree))

    assert result["verifier_result"] == {"error": "loop_tripwire_failed"}
    assert result["diff"] == ""


def test_ask_human_pauses_the_attempt_rather_than_answering_itself(tmp_path):
    worktree = _git_worktree(tmp_path)
    ask = ToolCall(
        name="ask_human",
        arguments={"question": "Should the cap be configurable?", "already_considered": "checked config.js"},
        id="c1",
    )
    with patch("app.azure_client.complete_turn", return_value=_turn([ask])), \
         patch("app.graphs.fix_council._record_fix_ask") as record:
        result = patch_generation_node(_state(worktree))

    record.assert_called_once()
    assert result["needs_human"]["question"] == "Should the cap be configurable?"
    assert result["diff"] == ""

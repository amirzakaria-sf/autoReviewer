"""The generation client's protocol contract.

Everything here is about SHAPE: which surface was called, what went on the
wire, and what came back as a `ModelTurn`. A fake Responses object is built
from `SimpleNamespace` items rather than by mocking `choices[0].message`,
because the two protocols disagree about exactly that structure and a mock
that shrugs would let a real mismatch pass.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from openai import BadRequestError

from app import azure_client
from app.azure_client import (
    ArbiterVerdict,
    JurorOpinion,
    ModelTurn,
    call_arbiter,
    call_patch_worker,
    call_skeptic,
    call_skeptic_opinion,
    call_verifier,
    complete_turn,
)
from app.config import settings


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def _usage(input_tokens=100, output_tokens=20, cached=0, reasoning=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )


def _text_item(text: str, annotations=None):
    part = SimpleNamespace(type="output_text", text=text, annotations=annotations or [])
    return SimpleNamespace(type="message", content=[part])


def _call_item(name: str, arguments: str, call_id: str = "call_1"):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=call_id)


def _reasoning_item(item_id: str = "rs_1", summary=None):
    return SimpleNamespace(type="reasoning", id=item_id, summary=summary or [])


def _search_item(queries):
    return SimpleNamespace(
        type="web_search_call",
        action=SimpleNamespace(type="search", queries=list(queries), query=queries[0] if queries else None),
        status="completed",
    )


def _responses(output, usage=None, status="completed"):
    return SimpleNamespace(output=list(output), usage=usage or _usage(), status=status, incomplete_details=None)


def _chat_response(content: str = "", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


def _bad_request(body: str) -> BadRequestError:
    response = MagicMock()
    response.status_code = 400
    error = BadRequestError(message=body, response=response, body={"error": {"message": body}})
    return error


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No database, no leaked process-level parameter opt-outs between tests."""
    monkeypatch.setattr("app.azure_client.record_council_run", lambda **kwargs: None)
    monkeypatch.setattr("app.llm_cache.get", lambda key: None)
    monkeypatch.setattr("app.llm_cache.put", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings, "whipguard_azure_api", "responses")
    azure_client._UNSUPPORTED_RESPONSES_PARAMS.clear()
    yield
    azure_client._UNSUPPORTED_RESPONSES_PARAMS.clear()


def _responses_mock(return_value=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.responses.create.side_effect = side_effect
    else:
        client.responses.create.return_value = return_value
    return client


# --------------------------------------------------------------------------- #
# Deployment routing survives the protocol change
# --------------------------------------------------------------------------- #


def test_skeptic_uses_fast_deployment():
    client = _responses_mock(_responses([_text_item("skeptical take")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        assert call_skeptic("prefix", "suffix") == "skeptical take"
    assert client.responses.create.call_args.kwargs["model"] == settings.azure_fast_deployment


def test_verifier_and_patch_worker_use_worker_deployment():
    client = _responses_mock(_responses([_text_item("ok")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        call_verifier("p", "s")
        call_patch_worker("p", "s")
    for call in client.responses.create.call_args_list:
        assert call.kwargs["model"] == settings.azure_worker_deployment


def test_arbiter_uses_planner_deployment_and_validates_schema():
    payload = {"score": 80, "factors": [{"factor": "x", "weight": 80, "note": "y"}], "verdict": "looks real"}
    client = _responses_mock(_responses([_call_item("ArbiterVerdict", json.dumps(payload))]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        verdict = call_arbiter("prefix", "suffix")
    assert isinstance(verdict, ArbiterVerdict)
    assert verdict.score == 80
    assert client.responses.create.call_args.kwargs["model"] == settings.azure_planner_deployment


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


def test_the_responses_surface_is_used_and_chat_completions_is_not():
    responses = _responses_mock(_responses([_text_item("hi")]))
    chat = MagicMock()
    with patch.object(azure_client, "_responses_client", return_value=responses), \
         patch.object(azure_client, "_chat_client", return_value=chat):
        call_skeptic("p", "s")
    responses.responses.create.assert_called_once()
    chat.chat.completions.create.assert_not_called()


def test_reasoning_effort_is_medium_for_the_arbiter_and_low_for_the_skeptic():
    client = _responses_mock(
        _responses([_call_item("ArbiterVerdict", json.dumps({"score": 1, "factors": [], "verdict": "v"}))])
    )
    with patch.object(azure_client, "_responses_client", return_value=client):
        call_arbiter("p", "s")
    assert client.responses.create.call_args.kwargs["reasoning"] == {"effort": "medium"}

    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        call_skeptic("p", "s")
    assert client.responses.create.call_args.kwargs["reasoning"] == {"effort": "low"}


def test_effort_none_omits_the_reasoning_parameter_entirely():
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], reasoning_effort="none")
    assert "reasoning" not in client.responses.create.call_args.kwargs


def test_temperature_is_never_sent():
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}])
    assert "temperature" not in client.responses.create.call_args.kwargs


def test_tools_are_sent_flat_with_strict_false():
    nested = [{
        "type": "function",
        "function": {"name": "read_file", "description": "Read.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    }]
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], tools=nested)
    sent = client.responses.create.call_args.kwargs["tools"][0]
    assert sent["name"] == "read_file"
    assert "function" not in sent
    assert sent["strict"] is False


def test_a_builtin_tool_passes_through_untouched():
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], tools=[{"type": "web_search"}])
    assert client.responses.create.call_args.kwargs["tools"] == [{"type": "web_search"}]


def test_defs_are_preserved_for_a_nested_pydantic_schema():
    schema = azure_client._to_responses_tool(ArbiterVerdict)
    assert "$defs" in schema["parameters"]
    assert "ArbiterFactor" in schema["parameters"]["$defs"]


def test_structured_output_forces_the_model_as_the_only_tool():
    client = _responses_mock(
        _responses([_call_item("ArbiterVerdict", json.dumps({"score": 5, "factors": [], "verdict": "v"}))])
    )
    with patch.object(azure_client, "_responses_client", return_value=client):
        call_arbiter("p", "s")
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "function", "name": "ArbiterVerdict"}
    assert [tool["name"] for tool in kwargs["tools"]] == ["ArbiterVerdict"]


def test_structured_output_falls_back_to_parsing_prose_when_no_tool_call_came():
    payload = {"confidence": 40, "transcript": "not convinced"}
    client = _responses_mock(_responses([_text_item("```json\n" + json.dumps(payload) + "\n```")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        opinion = call_skeptic_opinion("p", "s")
    assert isinstance(opinion, JurorOpinion)
    assert opinion.confidence == 40


# --------------------------------------------------------------------------- #
# Message items
# --------------------------------------------------------------------------- #


def test_reasoning_items_come_back_for_the_caller_to_replay():
    client = _responses_mock(_responses([_reasoning_item("rs_9", ["thought"]), _text_item("answer")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}])
    assert turn.reasoning_items == [{"type": "reasoning", "id": "rs_9", "summary": ["thought"]}]


def test_a_reasoning_item_in_the_input_is_sent_through_unchanged():
    history = [
        {"role": "user", "content": "q"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": '{"path": "a.js"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "contents"},
    ]
    client = _responses_mock(_responses([_text_item("done")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=history)
    assert client.responses.create.call_args.kwargs["input"] == history


def test_malformed_function_call_arguments_drop_that_call_and_keep_the_turn():
    client = _responses_mock(_responses([_call_item("read_file", "{not json"), _text_item("still here")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}])
    assert turn.tool_calls == []
    assert turn.content == "still here"


def test_tool_results_convert_to_chat_tool_messages_at_the_fallback_boundary():
    items = [
        {"role": "system", "content": "sys"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "contents"},
    ]
    converted = azure_client._to_chat_messages(items)
    assert converted[0] == {"role": "system", "content": "sys"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["tool_calls"][0]["id"] == "c1"
    assert converted[2] == {"role": "tool", "tool_call_id": "c1", "content": "contents"}
    assert all(item.get("type") != "reasoning" for item in converted)


# --------------------------------------------------------------------------- #
# Prompt cache
# --------------------------------------------------------------------------- #


def test_prompt_cache_key_is_sent_when_one_is_supplied():
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], prompt_cache_key="repo:role:abc")
    assert client.responses.create.call_args.kwargs["prompt_cache_key"] == "repo:role:abc"


def test_an_empty_prompt_cache_key_is_omitted_rather_than_sent_blank():
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], prompt_cache_key="")
    assert "prompt_cache_key" not in client.responses.create.call_args.kwargs


def test_the_arbiter_sends_a_partition_key_built_from_the_repo_when_it_has_one():
    client = _responses_mock(
        _responses([_call_item("ArbiterVerdict", json.dumps({"score": 1, "factors": [], "verdict": "v"}))])
    )
    with patch.object(azure_client, "_responses_client", return_value=client):
        call_arbiter("prefix", "suffix", repo_full_name="acme/demo")
    key = client.responses.create.call_args.kwargs["prompt_cache_key"]
    assert key.startswith("acme/demo:arbiter:")


def test_a_cacheable_call_is_served_from_the_postgres_cache_without_touching_azure(monkeypatch):
    monkeypatch.setattr("app.llm_cache.get", lambda key: "cached answer")
    client = _responses_mock(_responses([_text_item("fresh")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        assert call_skeptic("p", "s") == "cached answer"
    client.responses.create.assert_not_called()


def test_patch_generation_is_never_served_from_the_postgres_cache(monkeypatch):
    """A repeated identical patch prompt is the loop tripwire's only signal."""
    monkeypatch.setattr("app.llm_cache.get", lambda key: pytest.fail("patch generation must not read the cache"))
    client = _responses_mock(_responses([_text_item("diff")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        call_patch_worker("p", "s")
    client.responses.create.assert_called_once()


# --------------------------------------------------------------------------- #
# Fail-closed 400 ladder
# --------------------------------------------------------------------------- #


def test_a_400_naming_prompt_cache_key_retries_once_without_it():
    ok = _responses([_text_item("recovered")])
    client = _responses_mock(side_effect=[_bad_request("Unknown parameter: 'prompt_cache_key'."), ok])
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(
            deployment="d", messages=[{"role": "user", "content": "q"}], prompt_cache_key="k",
        )
    assert turn.content == "recovered"
    assert "prompt_cache_key" not in client.responses.create.call_args_list[1].kwargs
    assert "prompt_cache_key" in azure_client._UNSUPPORTED_RESPONSES_PARAMS


def test_a_400_naming_reasoning_retries_once_without_reasoning():
    ok = _responses([_text_item("recovered")])
    client = _responses_mock(side_effect=[_bad_request("Unsupported parameter: 'reasoning'."), ok])
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], reasoning_effort="medium")
    assert turn.content == "recovered"
    assert "reasoning" not in client.responses.create.call_args_list[1].kwargs


def test_an_unrelated_400_raises_instead_of_degrading_silently():
    client = _responses_mock(side_effect=_bad_request("content_filter triggered on the prompt"))
    with patch.object(azure_client, "_responses_client", return_value=client):
        with pytest.raises(BadRequestError):
            complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}])


def test_a_dropped_parameter_is_never_sent_again_in_this_process():
    azure_client._UNSUPPORTED_RESPONSES_PARAMS.add("prompt_cache_key")
    client = _responses_mock(_responses([_text_item("x")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], prompt_cache_key="k")
    assert "prompt_cache_key" not in client.responses.create.call_args.kwargs


# --------------------------------------------------------------------------- #
# The operator hatch
# --------------------------------------------------------------------------- #


def test_the_chat_pin_bypasses_the_responses_surface_entirely(monkeypatch):
    monkeypatch.setattr(settings, "whipguard_azure_api", "chat")
    responses = MagicMock()
    chat = MagicMock()
    chat.chat.completions.create.return_value = _chat_response("from chat")
    with patch.object(azure_client, "_responses_client", return_value=responses), \
         patch.object(azure_client, "_chat_client", return_value=chat):
        assert call_skeptic("p", "s") == "from chat"
    responses.responses.create.assert_not_called()


def test_the_chat_pin_asks_for_a_written_plan_when_tools_are_bound(monkeypatch):
    monkeypatch.setattr(settings, "whipguard_azure_api", "chat")
    chat = MagicMock()
    chat.chat.completions.create.return_value = _chat_response("planned")
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    with patch.object(azure_client, "_chat_client", return_value=chat):
        complete_turn(
            deployment="d", messages=[{"role": "user", "content": "q"}], tools=tools, reasoning_effort="medium",
        )
    sent = chat.chat.completions.create.call_args.kwargs["messages"]
    assert "describing your plan" in sent[-1]["content"]
    assert "reasoning_effort" not in chat.chat.completions.create.call_args.kwargs


def test_the_chat_pin_drops_a_builtin_tool_it_cannot_express():
    with patch.object(settings, "whipguard_azure_api", "chat"):
        chat = MagicMock()
        chat.chat.completions.create.return_value = _chat_response("no search")
        with patch.object(azure_client, "_chat_client", return_value=chat):
            complete_turn(
                deployment="d", messages=[{"role": "user", "content": "q"}], tools=[{"type": "web_search"}],
            )
        assert "tools" not in chat.chat.completions.create.call_args.kwargs


# --------------------------------------------------------------------------- #
# Accounting and web search
# --------------------------------------------------------------------------- #


def test_usage_is_recorded_with_the_protocol_and_reasoning_cost(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr("app.azure_client.record_council_run", lambda **kwargs: recorded.append(kwargs))
    client = _responses_mock(_responses([_text_item("x")], usage=_usage(cached=64, reasoning=128)))
    with patch.object(azure_client, "_responses_client", return_value=client):
        complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}], role="patch_worker")
    assert recorded[0]["role"] == "patch_worker"
    assert recorded[0]["cached_input_tokens"] == 64
    assert recorded[0]["verdict"] == {"protocol": "responses", "reasoning_tokens": 128}


def test_bookkeeping_failure_never_breaks_the_model_call(monkeypatch):
    def explode(**kwargs):
        raise RuntimeError("the database is down")

    monkeypatch.setattr("app.azure_client.record_council_run", explode)
    client = _responses_mock(_responses([_text_item("still answered")]))
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}])
    assert turn.content == "still answered"


def test_web_search_queries_and_citations_are_surfaced_on_the_turn():
    annotation = {"type": "url_citation", "url": "https://expressjs.com/x", "title": "Express"}
    client = _responses_mock(
        _responses([_search_item(["express 5 app.del"]), _text_item("It was removed.", [annotation])])
    )
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(
            deployment="d", messages=[{"role": "user", "content": "q"}], tools=[{"type": "web_search"}],
        )
    assert turn.search_queries == ["express 5 app.del"]
    assert turn.citations == [{"url": "https://expressjs.com/x", "title": "Express"}]


def test_an_incomplete_response_is_logged_and_still_returns_what_exists(caplog):
    response = _responses([_text_item("partial")], status="incomplete")
    response.incomplete_details = SimpleNamespace(reason="max_output_tokens")
    client = _responses_mock(response)
    with patch.object(azure_client, "_responses_client", return_value=client):
        turn = complete_turn(deployment="d", messages=[{"role": "user", "content": "q"}])
    assert turn.content == "partial"
    assert "azure_responses_incomplete" in caplog.text


def test_a_model_turn_defaults_to_an_empty_but_usable_shape():
    turn = ModelTurn()
    assert turn.tool_calls == [] and turn.reasoning_items == [] and turn.citations == []

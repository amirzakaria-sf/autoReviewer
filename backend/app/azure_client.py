"""The single Azure generation client: Responses API first, Chat Completions as a
documented fallback.

Every call that generates text or reaches for a tool goes through here -- the jury,
the Arbiter, the Fix Council's patch loop, Counsel, and the PRD council. Embeddings
are deliberately elsewhere (`app/embeddings.py`): a different API surface with a
different client, nothing to unify.

Why Responses rather than the Chat Completions this file used to speak: on GPT-5.x a
tool-bound Chat Completions call cannot carry a non-`none` reasoning effort, so the
patch worker -- the one call in this product that most needs to think -- was an
eight-tick tool loop with reasoning switched off. Responses carries `reasoning`,
`tools` and `prompt_cache_key` in the same request. Verified live against
`gpt-5.6-terra` and `gpt-5.6-luna` before this was written, not inferred from docs.

Three shapes cost other projects a production incident each, so none of them is a
style choice here:

- History is replayed in full every turn. No `previous_response_id`.
- Reasoning items are replayed with `id` and `summary` only, never a guessed field.
- `function_call.arguments` is a JSON *string*; a tool result is a
  `function_call_output` item, not a `{"role": "tool"}` message.

Two providers, one call site. `LLM_PROVIDER=azure` is everything above.
`LLM_PROVIDER=bedrock` sends the same Responses-shaped input to Amazon Bedrock's
"mantle" endpoint, which speaks OpenAI Chat Completions and is signed with SigV4
from the host's IAM role -- there is no API key. It always takes the Chat path:
mantle's Responses surface is unsupported on GLM-5 and, on gpt-oss, returned
`status=failed` for a forced function call (probed live 2026-10-22). Callers do
not change; the name of this module is historical.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx
from openai import AzureOpenAI, BadRequestError, OpenAI
from pydantic import BaseModel, Field

from app.config import settings
from app.council_runs import record_council_run

logger = logging.getLogger("whipguard.azure_client")

# botocore at DEBUG logs signed requests and the credentials behind them. Pinned
# here, where the signing happens, so no app-wide log level can raise it.
for _noisy in ("botocore", "boto3", "urllib3", "httpx", "httpcore", "openai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class ArbiterFactor(BaseModel):
    factor: str
    weight: int
    note: str


class ArbiterVerdict(BaseModel):
    # Descriptions ride inside the forced function's schema, so the scale
    # reaches the model on every provider, not just the ones that guess it.
    score: int = Field(description="0-100: how confident you are, as a whole number")
    factors: list[ArbiterFactor]
    verdict: str
    needs_clarification: dict | None = None


class JurorOpinion(BaseModel):
    """A structured jury-role response: the free-text argument PLUS a
    confidence score, so a meta-council can measure disagreement mechanically
    (plan.md §10.2: |Skeptic_confidence - Corroborator_confidence| >
    disagreement_threshold) instead of trying to infer it from two paragraphs
    of prose."""

    confidence: int = Field(description="0-100, a whole number: your confidence in YOUR OWN position")
    transcript: str = Field(description="Your argument, several sentences")


# --------------------------------------------------------------------------- #
# The turn: one model call's result, protocol-independent.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    id: str


@dataclass(frozen=True)
class ModelTurn:
    """What came back, in a shape neither protocol leaks through.

    `reasoning_items` are opaque: a caller's only correct use is to splice them
    back into the next turn's input, ahead of the assistant message they belong
    to. Reading them is not the point; replaying them is.
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_items: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    protocol: str = "responses"
    # Populated only when the built-in `web_search` tool ran. `citations` are
    # the URLs the answer actually leans on; `search_queries` are the queries
    # the model chose to run, which is the audit trail for "where did this
    # claim come from" (app/research.py).
    citations: list[dict] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Clients. Two of them, and they are not interchangeable: the Responses surface
# lives under {endpoint}/openai/v1/ with api_version="preview", while Chat
# Completions uses azure_endpoint + the dated api-version. Crossing them 404s.
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _responses_client() -> AzureOpenAI:
    return AzureOpenAI(
        base_url=f"{settings.azure_api_endpoint.rstrip('/')}/openai/v1/",
        api_key=settings.azure_api_key,
        api_version="preview",
        timeout=180,
        max_retries=2,
    )


class _BedrockSigV4(httpx.Auth):
    """Signs every request -- retries included -- with whatever credentials the
    process has (instance role, task role, env). Signatures expire after a few
    minutes and role credentials rotate, so nothing here is cached but the
    session, which refreshes them itself."""

    requires_request_body = True

    def __init__(self, region: str):
        import boto3

        self.region = region
        self._session = boto3.Session()

    def auth_flow(self, request: httpx.Request):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        # The SDK's placeholder bearer key must not reach AWS.
        request.headers.pop("Authorization", None)
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("Bedrock: no AWS credentials available to sign the request")
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers={"Content-Type": request.headers.get("Content-Type", "application/json")},
        )
        SigV4Auth(credentials.get_frozen_credentials(), "bedrock-mantle", self.region).add_auth(signed)
        for key, value in signed.headers.items():
            request.headers[key] = value
        yield request


def _provider() -> str:
    return str(getattr(settings, "llm_provider", "azure") or "azure").lower()


def supports_web_search() -> bool:
    """Only Azure's Responses surface has a server-side `web_search` tool.
    Mantle rejects the tool type outright."""
    return _provider() == "azure" and _use_responses()


@lru_cache(maxsize=1)
def _chat_client() -> OpenAI:
    if _provider() == "bedrock":
        region = settings.bedrock_region
        return OpenAI(
            base_url=f"https://bedrock-mantle.{region}.api.aws/v1",
            api_key="sigv4",  # required by the SDK; stripped by the auth flow
            http_client=httpx.Client(auth=_BedrockSigV4(region), timeout=180),
            max_retries=2,
        )
    return AzureOpenAI(
        azure_endpoint=settings.azure_api_endpoint,
        api_key=settings.azure_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=120,
        max_retries=2,
    )


def _client() -> OpenAI:
    """Back-compat alias. Older tests patch this name."""
    return _chat_client()


def _use_responses() -> bool:
    if _provider() == "bedrock":
        return False
    return str(getattr(settings, "whipguard_azure_api", "responses") or "responses").lower() != "chat"


# --------------------------------------------------------------------------- #
# Tool schema conversion.
# --------------------------------------------------------------------------- #


def _parameters_from_schema(schema: dict) -> dict:
    """`$defs` must survive verbatim. Pydantic emits `{"$ref": "#/$defs/X"}` for
    every nested model field -- `ArbiterVerdict.factors` is one -- and a schema
    whose refs dangle is rejected outright, not degraded."""
    parameters = {
        "type": "object",
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }
    if schema.get("$defs"):
        parameters["$defs"] = schema["$defs"]
    return parameters


def _to_responses_tool(tool: Any) -> dict:
    """Chat-Completions-nested, already-flat, or a Pydantic model -> a flat
    Responses tool.

    `strict` is sent explicitly as False. The Responses API defaults it to true
    server-side, which demands `additionalProperties: false` plus every property
    listed in `required` -- neither of which our hand-written tool schemas
    guarantee, so leaving it unset 400s every tool-bound call, not just the
    nested ones.
    """
    if isinstance(tool, type) and issubclass(tool, BaseModel):
        return {
            "type": "function",
            "name": tool.__name__,
            "description": (tool.__doc__ or "").strip(),
            "strict": False,
            "parameters": _parameters_from_schema(tool.model_json_schema()),
        }
    if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
        inner = tool["function"]
        return {
            "type": "function",
            "name": inner.get("name", ""),
            "description": inner.get("description", ""),
            "strict": False,
            "parameters": _parameters_from_schema(inner.get("parameters") or {}),
        }
    if isinstance(tool, dict) and tool.get("type") and tool.get("type") != "function":
        # A built-in tool (`{"type": "web_search"}`) -- server-side, no schema
        # of ours to convert, and adding `strict`/`parameters` to it is an error.
        return dict(tool)
    if isinstance(tool, dict):
        converted = {
            "type": "function",
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "strict": False,
            "parameters": _parameters_from_schema(tool.get("parameters") or {}),
        }
        return converted
    raise TypeError(f"cannot convert tool of type {type(tool).__name__} to a Responses tool")


def _to_chat_tool(tool: Any) -> dict:
    """The inverse, for the fallback. A built-in server-side tool has no Chat
    Completions equivalent and is dropped -- the turn degrades to "no search"
    rather than 400ing on a tool type that surface has never heard of."""
    if isinstance(tool, type) and issubclass(tool, BaseModel):
        return {
            "type": "function",
            "function": {
                "name": tool.__name__,
                "description": (tool.__doc__ or "").strip(),
                "parameters": _parameters_from_schema(tool.model_json_schema()),
            },
        }
    if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
        return tool
    if isinstance(tool, dict) and tool.get("type") and tool.get("type") != "function":
        return {}
    if isinstance(tool, dict):
        return {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {},
            },
        }
    raise TypeError(f"cannot convert tool of type {type(tool).__name__} to a Chat tool")


# --------------------------------------------------------------------------- #
# Message conversion at the fallback boundary. Callers speak Responses input
# items and nothing else; this is the only place the Chat shape exists.
# --------------------------------------------------------------------------- #


def _to_chat_messages(items: list[dict]) -> list[dict]:
    messages: list[dict] = []
    pending_calls: list[dict] = []

    def flush() -> None:
        nonlocal pending_calls
        if pending_calls:
            # Callers replay an assistant's text and its function calls as
            # separate items. Two assistant messages in a row is tolerated by
            # Azure and not by every open-weight model, so the calls join the
            # text they followed.
            last = messages[-1] if messages else None
            if last and last.get("role") == "assistant" and not last.get("tool_calls"):
                last["tool_calls"] = pending_calls
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": pending_calls})
            pending_calls = []

    for item in items:
        item_type = item.get("type")
        if item_type == "reasoning":
            continue
        if item_type == "function_call":
            pending_calls.append(
                {
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
                }
            )
            continue
        if item_type == "function_call_output":
            flush()
            messages.append(
                {"role": "tool", "tool_call_id": item.get("call_id", ""), "content": item.get("output", "")}
            )
            continue
        flush()
        messages.append({"role": item.get("role", "user"), "content": item.get("content", "")})

    flush()
    return messages


# --------------------------------------------------------------------------- #
# Response -> ModelTurn.
# --------------------------------------------------------------------------- #


def _usage_numbers(usage: Any) -> tuple[int, int, int, int]:
    if usage is None:
        return 0, 0, 0, 0
    input_details = getattr(usage, "input_tokens_details", None) or getattr(usage, "prompt_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None) or getattr(usage, "completion_tokens_details", None)
    input_tokens = getattr(usage, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "prompt_tokens", 0)
    output_tokens = getattr(usage, "output_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "completion_tokens", 0)
    return (
        int(input_tokens or 0),
        int(output_tokens or 0),
        int(getattr(input_details, "cached_tokens", 0) or 0),
        int(getattr(output_details, "reasoning_tokens", 0) or 0),
    )


def _annotation_to_citation(annotation: Any) -> dict | None:
    if isinstance(annotation, dict):
        data = annotation
    else:
        data = {
            "type": getattr(annotation, "type", ""),
            "url": getattr(annotation, "url", ""),
            "title": getattr(annotation, "title", ""),
        }
    if data.get("type") != "url_citation" or not data.get("url"):
        return None
    return {"url": str(data.get("url")), "title": str(data.get("title") or "")}


def _responses_to_turn(response: Any) -> ModelTurn:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    reasoning_items: list[dict] = []
    citations: list[dict] = []
    search_queries: list[str] = []

    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "output_text":
                    text_parts.append(part.text or "")
                for annotation in getattr(part, "annotations", None) or []:
                    citation = _annotation_to_citation(annotation)
                    if citation and citation not in citations:
                        citations.append(citation)
        elif item_type == "function_call":
            try:
                arguments = json.loads(item.arguments or "{}")
            except (TypeError, ValueError) as error:
                # Drop this one call, not the whole tick. The caller has no way
                # to retry a single malformed call, so raising here would throw
                # away every other tool result in the same response.
                logger.warning(
                    "azure_responses_bad_function_call_args name=%s call_id=%s error=%s",
                    getattr(item, "name", "?"), getattr(item, "call_id", "?"), error,
                )
                continue
            tool_calls.append(
                ToolCall(name=item.name, arguments=arguments if isinstance(arguments, dict) else {}, id=item.call_id)
            )
        elif item_type == "reasoning":
            reasoning_items.append(
                {"type": "reasoning", "id": item.id, "summary": list(getattr(item, "summary", None) or [])}
            )
        elif item_type == "web_search_call":
            action = getattr(item, "action", None)
            queries = list(getattr(action, "queries", None) or [])
            single = getattr(action, "query", None)
            if not queries and single:
                queries = [single]
            search_queries.extend(str(query) for query in queries)

    input_tokens, output_tokens, cached, reasoning_tokens = _usage_numbers(getattr(response, "usage", None))
    return ModelTurn(
        content="".join(text_parts),
        tool_calls=tool_calls,
        reasoning_items=reasoning_items,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning_tokens,
        protocol="responses",
        citations=citations,
        search_queries=search_queries,
    )


def _chat_to_turn(response: Any, protocol: str = "chat") -> ModelTurn:
    if not getattr(response, "choices", None):
        raise RuntimeError(f"{protocol}: response carried no choices")
    message = response.choices[0].message
    tool_calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except (TypeError, ValueError) as error:
            logger.warning("azure_chat_bad_function_call_args name=%s error=%s", call.function.name, error)
            continue
        tool_calls.append(
            ToolCall(name=call.function.name, arguments=arguments if isinstance(arguments, dict) else {}, id=call.id)
        )
    input_tokens, output_tokens, cached, reasoning_tokens = _usage_numbers(getattr(response, "usage", None))
    return ModelTurn(
        content=message.content or "",
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning_tokens,
        protocol=protocol,
    )


# --------------------------------------------------------------------------- #
# Fail-closed 400 handling.
#
# A 400 is not evidence that the Responses API is unavailable. Treating it that
# way is how sibling projects silently swallowed content-filter rejections and
# malformed tool schemas for weeks -- the product appeared to work and every
# call had quietly lost its reasoning. So: drop the one optional knob the body
# actually names, retry once, and otherwise raise with the body in the log.
# --------------------------------------------------------------------------- #

_UNSUPPORTED_RESPONSES_PARAMS: set[str] = set()

# Cheapest first. `prompt_cache_key` only scopes a cache partition, so losing it
# costs money and nothing else; `reasoning` costs answer quality. `tools` is
# never droppable -- a tool-bound turn without tools is not degraded, it is a
# different and wrong call.
_DEGRADABLE_RESPONSES_PARAMS = ("prompt_cache_key", "reasoning")


def _error_body(error: BadRequestError) -> str:
    try:
        body = getattr(error, "body", None)
        return str(body)[:2000] if body else str(error)[:2000]
    except Exception:  # noqa: BLE001 - a log helper must never fail a request
        return "<unreadable BadRequestError body>"


def _mentions_unsupported(body: str, parameter: str) -> bool:
    lowered = (body or "").lower()
    if parameter.lower() not in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "unsupported", "not supported", "unknown", "unrecognized",
            "extra input", "invalid_request", "unexpected keyword",
        )
    )


def _drop_unsupported_param(
    kwargs: dict,
    body: str,
    degradable: tuple[str, ...] = _DEGRADABLE_RESPONSES_PARAMS,
    unsupported: set[str] = _UNSUPPORTED_RESPONSES_PARAMS,
) -> str | None:
    for parameter in degradable:
        if parameter not in kwargs or not _mentions_unsupported(body, parameter):
            continue
        unsupported.add(parameter)
        kwargs.pop(parameter, None)
        logger.warning(
            "azure_param_unsupported parameter=%s -- dropped for the rest of this process. detail=%s",
            parameter, (body or "")[:400],
        )
        return parameter
    return None


# --------------------------------------------------------------------------- #
# The one turn function.
# --------------------------------------------------------------------------- #


def _json_envelope_instruction(model: type[BaseModel]) -> dict:
    """Chat Completions has no forced-function structured output here, so the
    shape has to be asked for in words. Generated from the model itself rather
    than hand-written, so a field added to `ArbiterVerdict` cannot drift away
    from what the fallback asks for."""
    schema = model.model_json_schema()
    return {
        "role": "user",
        "content": (
            "Respond with ONLY a JSON object matching this JSON Schema, no prose outside it:\n"
            + json.dumps({"properties": schema.get("properties", {}), "required": schema.get("required", []), "$defs": schema.get("$defs", {})})
        ),
    }


def _cache_identity(messages: list[dict]) -> tuple[str, str]:
    """(prefix, suffix) for the Postgres exact-match cache and for the
    council_runs prompt hash. The prefix is the system content -- the stable,
    cacheable bytes -- and everything after it is the volatile half."""
    prefix = ""
    rest: list[dict] = []
    for item in messages:
        if not prefix and item.get("role") == "system":
            prefix = str(item.get("content") or "")
            continue
        rest.append(item)
    return prefix, json.dumps(rest, sort_keys=True, default=str)


def complete_turn(
    *,
    deployment: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    role: str = "unknown",
    reasoning_effort: str = "medium",
    cacheable: bool = False,
    prompt_cache_key: str = "",
    structured_model: type[BaseModel] | None = None,
    issue_id: Any = None,
    fix_id: Any = None,
) -> ModelTurn:
    """One model call. `messages` are Responses input items in every case; the
    Chat shape exists only past the fallback boundary."""
    from app import llm_cache

    prefix, suffix = _cache_identity(messages)
    cache_key = ""
    if cacheable:
        cache_key = llm_cache.cache_key_for(role=role, deployment=deployment, prefix=prefix, suffix=suffix)
        hit = llm_cache.get(cache_key)
        if hit is not None:
            logger.info("llm cache hit for role=%s deployment=%s", role, deployment)
            return ModelTurn(content=hit, protocol="cache")

    started = time.monotonic()
    if _use_responses():
        turn = _responses_turn(
            deployment=deployment,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
            structured_model=structured_model,
        )
    else:
        turn = _chat_turn(
            deployment=deployment,
            messages=messages,
            tools=tools,
            structured_model=structured_model,
            reasoning_effort=reasoning_effort,
        )
    latency_ms = int((time.monotonic() - started) * 1000)

    try:
        record_council_run(
            role=role,
            model=deployment,
            prefix=prefix,
            input_tokens=turn.input_tokens,
            cached_input_tokens=turn.cached_input_tokens,
            output_tokens=turn.output_tokens,
            latency_ms=latency_ms,
            # The parsed verdict already lives on the Issue/Fix row itself;
            # this column carries what only exists at call time -- which
            # protocol answered, and what the reasoning actually cost.
            verdict={"protocol": turn.protocol, "reasoning_tokens": turn.reasoning_tokens},
            issue_id=issue_id,
            fix_id=fix_id,
        )
    except Exception:  # noqa: BLE001
        pass  # never let usage bookkeeping break a model call

    if cache_key and turn.content:
        llm_cache.put(cache_key, turn.content, role=role, deployment=deployment)

    return turn


def _responses_turn(
    *,
    deployment: str,
    messages: list[dict],
    tools: list[dict] | None,
    reasoning_effort: str,
    prompt_cache_key: str,
    structured_model: type[BaseModel] | None,
) -> ModelTurn:
    kwargs: dict[str, Any] = {
        "model": deployment,
        "input": messages,
        "max_output_tokens": settings.azure_responses_max_output_tokens,
    }
    # Never `temperature`: GPT-5.x 400s on it, and there is nothing to tune here
    # that a reasoning effort does not express better.
    if prompt_cache_key:
        kwargs["prompt_cache_key"] = prompt_cache_key
    if reasoning_effort and reasoning_effort != "none":
        kwargs["reasoning"] = {"effort": reasoning_effort}

    if structured_model is not None:
        kwargs["tools"] = [_to_responses_tool(structured_model)]
        kwargs["tool_choice"] = {"type": "function", "name": structured_model.__name__}
    elif tools:
        kwargs["tools"] = [_to_responses_tool(tool) for tool in tools]
        kwargs["tool_choice"] = "auto"

    for parameter in _UNSUPPORTED_RESPONSES_PARAMS:
        kwargs.pop(parameter, None)

    while True:
        try:
            response = _responses_client().responses.create(**kwargs)
            break
        except BadRequestError as error:
            body = _error_body(error)
            if _drop_unsupported_param(kwargs, body) is not None:
                continue
            logger.error("azure_bad_request_fail_closed deployment=%s body=%s", deployment, body)
            raise

    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
        logger.error(
            "azure_responses_incomplete deployment=%s reason=%s max_output_tokens=%s",
            deployment, reason, kwargs["max_output_tokens"],
        )

    return _responses_to_turn(response)


def _chat_turn(
    *,
    deployment: str,
    messages: list[dict],
    tools: list[dict] | None,
    structured_model: type[BaseModel] | None,
    reasoning_effort: str,
) -> ModelTurn:
    if _provider() == "bedrock":
        return _bedrock_turn(
            deployment=deployment,
            messages=messages,
            tools=tools,
            structured_model=structured_model,
            reasoning_effort=reasoning_effort,
        )
    items = list(messages)
    if structured_model is not None:
        items = [*items, _json_envelope_instruction(structured_model)]
    elif tools and reasoning_effort not in ("", "none"):
        # Chat Completions cannot combine tools with a reasoning effort on
        # GPT-5.x. Asking for the plan in the response content is the nearest
        # thing that survives, and it goes in BEFORE the call rather than being
        # invented afterwards to fill an empty message.
        items = [
            *items,
            {
                "role": "user",
                "content": (
                    "Before calling any tool, write one or two sentences describing your plan, "
                    "then call the tool."
                ),
            },
        ]

    kwargs: dict[str, Any] = {"model": deployment, "messages": _to_chat_messages(items)}
    if structured_model is None and tools:
        chat_tools = [converted for converted in (_to_chat_tool(tool) for tool in tools) if converted]
        if chat_tools:
            kwargs["tools"] = chat_tools
            kwargs["tool_choice"] = "auto"

    response = _chat_client().chat.completions.create(**kwargs)
    return _chat_to_turn(response)


# Same fail-closed rule as the Responses path: a 400 drops only the optional
# knob its body names. Per model, since mantle serves several model families
# and one rejecting `reasoning_effort` says nothing about the others.
_UNSUPPORTED_BEDROCK_PARAMS: dict[str, set[str]] = {}
_DEGRADABLE_BEDROCK_PARAMS = ("reasoning_effort",)


def _bedrock_create(kwargs: dict) -> ModelTurn:
    unsupported = _UNSUPPORTED_BEDROCK_PARAMS.setdefault(kwargs["model"], set())
    for parameter in unsupported:
        kwargs.pop(parameter, None)
    while True:
        try:
            response = _chat_client().chat.completions.create(**kwargs)
            break
        except BadRequestError as error:
            body = _error_body(error)
            if _drop_unsupported_param(kwargs, body, _DEGRADABLE_BEDROCK_PARAMS, unsupported) is not None:
                continue
            logger.error("bedrock_bad_request_fail_closed model=%s body=%s", kwargs["model"], body)
            raise
    if response.choices and getattr(response.choices[0], "finish_reason", None) == "length":
        logger.error("bedrock_truncated model=%s max_tokens=%s", kwargs["model"], kwargs.get("max_tokens"))
    return _chat_to_turn(response, protocol="bedrock")


def _bedrock_turn(
    *,
    deployment: str,
    messages: list[dict],
    tools: list[dict] | None,
    structured_model: type[BaseModel] | None,
    reasoning_effort: str,
) -> ModelTurn:
    """Chat Completions on mantle. Unlike Azure's Chat surface, mantle accepts a
    forced function and a reasoning effort alongside tools, so neither the JSON
    envelope nor the plan-sentence workaround is the first resort here. The
    envelope survives as the one retry for a model that ignores a forced
    function -- gpt-oss does, GLM-5 and Kimi K2.5 honour it."""
    kwargs: dict[str, Any] = {
        "model": deployment,
        "messages": _to_chat_messages(messages),
        # Open-weight reasoning models spend hidden tokens before answering,
        # and mantle's default ceiling is far below what a patch tick needs.
        "max_tokens": settings.azure_responses_max_output_tokens,
    }
    if reasoning_effort and reasoning_effort != "none":
        kwargs["reasoning_effort"] = reasoning_effort
    if structured_model is not None:
        kwargs["tools"] = [_to_chat_tool(structured_model)]
        kwargs["tool_choice"] = {"type": "function", "function": {"name": structured_model.__name__}}
    elif tools:
        chat_tools = [converted for converted in (_to_chat_tool(tool) for tool in tools) if converted]
        if chat_tools:
            kwargs["tools"] = chat_tools
            kwargs["tool_choice"] = "auto"

    turn = _bedrock_create(kwargs)
    if structured_model is None or any(call.name == structured_model.__name__ for call in turn.tool_calls):
        return turn
    if "{" in (turn.content or ""):
        return turn  # _parse_structured reads the object out of the prose
    logger.warning("bedrock_forced_function_ignored model=%s function=%s", deployment, structured_model.__name__)
    retry = {key: value for key, value in kwargs.items() if key not in ("tools", "tool_choice")}
    retry["messages"] = _to_chat_messages([*messages, _json_envelope_instruction(structured_model)])
    return _bedrock_create(retry)


def _parse_structured(turn: ModelTurn, model: type[BaseModel]):
    """Forced-function first, then the model's own prose as a second chance.
    Never invents a value -- a verdict this cannot parse raises, and the graph
    already treats a failed Arbiter call as a failed call rather than a zero."""
    call = next((c for c in turn.tool_calls if c.name == model.__name__), None)
    if call is not None:
        return model.model_validate(call.arguments)
    text = (turn.content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return model.model_validate(json.loads(text))


# --------------------------------------------------------------------------- #
# Role-routed call sites. Deployment routing per plan.md §12; reasoning effort
# per the table in docs/plans/2026-09-20-azure-responses-and-apply-patch.md §4.5.
# --------------------------------------------------------------------------- #


def _messages(prefix: str, suffix: str) -> list[dict]:
    return [{"role": "system", "content": prefix}, {"role": "user", "content": suffix}]


def _chat(
    deployment: str,
    prefix: str,
    suffix: str,
    role: str = "unknown",
    cacheable: bool = False,
    reasoning_effort: str = "medium",
    prompt_cache_key: str = "",
) -> str:
    """Plain-text completion. Kept as the name the PRD council already imports."""
    turn = complete_turn(
        deployment=deployment,
        messages=_messages(prefix, suffix),
        role=role,
        reasoning_effort=reasoning_effort,
        cacheable=cacheable,
        prompt_cache_key=prompt_cache_key,
    )
    return turn.content


def _structured(
    deployment: str,
    prefix: str,
    suffix: str,
    *,
    role: str,
    model: type[BaseModel],
    reasoning_effort: str,
    repo_full_name: str = "",
):
    from app import llm_cache
    from app.prompts import partition_key

    messages = _messages(prefix, suffix)
    cache_prefix, cache_suffix = _cache_identity(messages)
    cache_key = llm_cache.cache_key_for(role=role, deployment=deployment, prefix=cache_prefix, suffix=cache_suffix)
    hit = llm_cache.get(cache_key)
    if hit is not None:
        try:
            return model.model_validate(json.loads(hit))
        except (ValueError, TypeError):
            logger.warning("discarding unparseable cached %s for role=%s", model.__name__, role)

    turn = complete_turn(
        deployment=deployment,
        messages=messages,
        role=role,
        reasoning_effort=reasoning_effort,
        structured_model=model,
        prompt_cache_key=partition_key(repo_full_name, role, prefix) if repo_full_name else "",
    )
    parsed = _parse_structured(turn, model)
    llm_cache.put(cache_key, parsed.model_dump_json(), role=role, deployment=deployment)
    return parsed


def call_skeptic(prefix: str, suffix: str) -> str:
    """Legacy plain-text form, kept for callers that don't need a confidence
    score (e.g. the retry-contract test)."""
    return _chat(settings.azure_fast_deployment, prefix, suffix, role="skeptic", cacheable=True, reasoning_effort="low")


def call_skeptic_opinion(prefix: str, suffix: str, repo_full_name: str = "") -> JurorOpinion:
    return _structured(
        settings.azure_fast_deployment, prefix + _JUROR_RUBRIC, suffix,
        role="skeptic", model=JurorOpinion, reasoning_effort="low", repo_full_name=repo_full_name,
    )


def call_corroborator_opinion(prefix: str, suffix: str, repo_full_name: str = "") -> JurorOpinion:
    """Corroborator: mid-tier model (needs to reason about code relevance and
    read mechanical evidence carefully — plan.md §12's routing table)."""
    return _structured(
        settings.azure_worker_deployment, prefix + _JUROR_RUBRIC, suffix,
        role="corroborator", model=JurorOpinion, reasoning_effort="medium", repo_full_name=repo_full_name,
    )


def call_verifier(prefix: str, suffix: str) -> str:
    return _chat(
        settings.azure_worker_deployment, prefix, suffix, role="verifier", cacheable=True, reasoning_effort="low"
    )


def call_fix_skeptic_opinion(prefix: str, suffix: str, repo_full_name: str = "") -> JurorOpinion:
    """Fix Council's "Skeptic-for-regressions" (plan.md §10.3) — argues the
    patch could break something else, run in parallel with the mechanical
    VerifierNode rather than after it."""
    return _structured(
        settings.azure_fast_deployment, prefix + _JUROR_RUBRIC, suffix,
        role="fix_skeptic", model=JurorOpinion, reasoning_effort="low", repo_full_name=repo_full_name,
    )


def call_patch_worker(prefix: str, suffix: str) -> str:
    # Deliberately NOT cacheable: an identical input here means the run is
    # repeating itself, and that repetition is the signal a loop tripwire
    # needs to see. See app/llm_cache.py.
    return _chat(settings.azure_worker_deployment, prefix, suffix, role="patch_worker", reasoning_effort="medium")


def call_meta_auditor(prefix: str, suffix: str, repo_full_name: str = "") -> ArbiterVerdict:
    """The meta-council's own Arbiter call, escalated to on jury disagreement
    (plan.md §10.2) — routed to the strongest deployment, same as the regular
    Arbiter, since this is spending MORE compute on a close call, not less."""
    return call_arbiter(prefix, suffix, role="meta_auditor", repo_full_name=repo_full_name)


# Semantics, not shape. The shape is the forced `ArbiterVerdict` function on the
# Responses path and a generated JSON-Schema envelope on the fallback; what a
# model cannot infer from either is what the numbers are supposed to MEAN, so
# that half stays in the prompt on both paths.
_ARBITER_RUBRIC = (
    "\n\nThe factors' weights must sum to exactly `score`.\n\n"
    "Use needs_clarification INSTEAD of forcing a score only when the ambiguity is about "
    "missing PRODUCT INTENT a human would have to supply (e.g. a hardcoded value that could "
    "be an intentional constant or could be a bug depending on a decision nobody documented; "
    "a behavior that might be deliberate for this specific deployment) -- never when the "
    "ambiguity is just missing EVIDENCE you could reason about yourself from what's given. "
    "Forcing a confident number when the missing piece is a human's own undocumented intent "
    "manufactures false confidence; that is worse than asking."
)

_JUROR_RUBRIC = (
    "\n\n`confidence` is how confident you are in YOUR OWN position, 0-100. "
    "`transcript` is your argument, several sentences."
)


def call_arbiter(prefix: str, suffix: str, role: str = "arbiter", repo_full_name: str = "") -> ArbiterVerdict:
    """Structured-output score — a forced function call validated against
    `ArbiterVerdict`, never free-text parsing of a model's prose (plan.md §11.9)."""
    return _structured(
        settings.azure_planner_deployment, prefix + _ARBITER_RUBRIC, suffix,
        role=role, model=ArbiterVerdict, reasoning_effort="medium", repo_full_name=repo_full_name,
    )

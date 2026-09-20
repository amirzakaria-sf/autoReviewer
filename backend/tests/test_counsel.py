"""Counsel's guarantees, as opposed to its prose.

Answer quality is not testable here and pretending otherwise produces tests
that assert a model said a particular sentence. What IS testable is the
contract around the model: that the tool surface cannot mutate anything,
that a failing tool does not end the turn, that the stream always terminates,
and that citations are extracted from what was actually written.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.azure_client import ModelTurn, ToolCall
from app.counsel import agent
from app.counsel.tools import ACTION_TOOLS, READ_TOOLS, TOOL_LABELS, TOOL_SCHEMAS, ToolContext, run_tool


@pytest.fixture
def context(tmp_path):
    (tmp_path / "app.js").write_text("function deleteItem(index) {\n  items.splice(index, 1);\n}\n")
    return ToolContext(
        repo_id="00000000-0000-0000-0000-000000000000",
        repo_full_name="acme/demo",
        worktree_path=str(tmp_path),
        user_email="dev@acme.test",
        is_admin=False,
    )


# --- the read-only contract --------------------------------------------------


def test_every_advertised_tool_has_an_implementation():
    """A schema the model can call with no handler behind it fails at the
    worst possible moment: mid-answer, after the user has waited."""
    advertised = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    implemented = set(READ_TOOLS) | set(ACTION_TOOLS)
    assert advertised == implemented, advertised ^ implemented


def test_every_tool_has_a_human_label():
    """The sidebar shows these while the tool runs. A missing label leaks a
    function name into the UI."""
    assert set(TOOL_LABELS) == set(READ_TOOLS) | set(ACTION_TOOLS)


def test_the_read_surface_exposes_nothing_that_mutates():
    """Counsel runs in the web process, which deliberately has no Docker
    socket and a read-only workspace. Any write tool appearing here would be
    handing back exactly the capability the security split removed."""
    forbidden = ("approve", "reject", "deploy", "write", "delete", "create", "trigger", "scan", "merge")
    offenders = [name for name in READ_TOOLS if any(word in name for word in forbidden)]
    assert not offenders, f"write-shaped tools in the read-only surface: {offenders}"


def test_counsel_can_never_approve_reject_deploy_or_merge():
    """The line the whole design rests on. Counsel dispatches work and gathers
    evidence; rendering a verdict and shipping code stay with a council and a
    human. A tool granting either would be the single most damaging thing to
    add here, and an agent that reads attacker-controlled repository content
    is exactly who must not hold it."""
    every_tool = set(READ_TOOLS) | set(ACTION_TOOLS)
    banned = ("approve", "reject", "deploy", "merge", "push", "rollback")
    offenders = [name for name in every_tool if any(word in name for word in banned)]
    assert not offenders, f"Counsel must not be able to: {offenders}"


def test_dispatch_tools_only_queue_work_and_never_execute_it():
    """The dispatch tools are the exception to read-only, and they stay safe
    for one reason: they add a row for the worker rather than running
    anything. If either ever called a detector or git directly, it would be
    executing untrusted repo code inside the web process."""
    import inspect

    from app.counsel import tools as tools_module

    for name in ACTION_TOOLS:
        source = inspect.getsource(getattr(tools_module, name))
        assert "_enqueue_job" in source, f"{name} should dispatch through the queue"
        for forbidden in ("subprocess", "run_in_sandbox", "docker"):
            assert forbidden not in source, f"{name} must not execute anything directly ({forbidden})"


def test_an_unknown_tool_returns_a_sentence_rather_than_raising(context):
    assert "No such tool" in run_tool(context, "rm_rf", {})


def test_a_failing_tool_returns_a_sentence_rather_than_raising(context):
    """A tool that raises kills the turn and the user sees nothing. One that
    explains itself lets the model recover and say so."""
    with patch.dict(READ_TOOLS, {"search_code": MagicMock(side_effect=RuntimeError("index is gone"))}):
        result = run_tool(context, "search_code", {"query": "anything"})
    assert "failed" in result.lower()
    assert "index is gone" in result


def test_wrong_arguments_are_reported_not_raised(context):
    assert "rejected those arguments" in run_tool(context, "read_file", {"nonexistent_arg": 1})


def test_read_file_refuses_to_escape_the_worktree(context):
    """The path comes from a model that has been reading attacker-controlled
    repository content."""
    assert "Could not read" in run_tool(context, "read_file", {"path": "../../../etc/passwd"})


# --- citations ---------------------------------------------------------------


def test_citations_are_extracted_from_the_answer():
    cited = agent._extract_citations(
        "The handler lives at `app.js:22` and its test at tests/delete.spec.ts:3-20."
    )
    assert "app.js:22" in cited
    assert "tests/delete.spec.ts:3" in cited


def test_prose_that_cites_nothing_yields_no_citations():
    """An uncited answer must not manufacture confidence by showing chips."""
    assert agent._extract_citations("I could not determine that from the code.") == []


# --- the stream contract -----------------------------------------------------


def _turn(content="", tool_calls=None, reasoning_items=None):
    return ModelTurn(
        content=content,
        tool_calls=list(tool_calls or []),
        reasoning_items=list(reasoning_items or []),
        protocol="responses",
    )


def _events(context, turns):
    """`turns` is what complete_turn returns, in order -- or a single turn to
    return every round, or an exception to raise."""
    if isinstance(turns, BaseException) or (isinstance(turns, type) and issubclass(turns, BaseException)):
        patcher = patch("app.azure_client.complete_turn", side_effect=turns)
    elif isinstance(turns, list):
        patcher = patch("app.azure_client.complete_turn", side_effect=turns)
    else:
        patcher = patch("app.azure_client.complete_turn", return_value=turns)
    with patcher:
        return list(agent.run(context, "how does deletion work?"))


def test_a_turn_with_no_tool_calls_still_streams_and_terminates(context):
    answer = "Deletion happens in `app.js:22`."
    events = _events(context, _turn(content=answer))
    kinds = [event["type"] for event in events]

    assert kinds[-1] == "done", "the stream must always terminate with a terminal event"
    assert "token" in kinds, "the answer must arrive in fragments, not one block"
    assert "".join(e["text"] for e in events if e["type"] == "token") == answer
    assert events[-1]["citations"] == ["app.js:22"]


def test_a_model_failure_ends_the_stream_with_an_error_not_an_exception(context):
    """The sidebar has no way to render an exception. It can render a
    sentence."""
    events = _events(context, RuntimeError("upstream is down"))
    assert events[-1]["type"] == "error"
    assert "Nothing was changed" in events[-1]["text"]


def test_tool_use_is_narrated_before_it_runs(context):
    """The whole point of the event stream: the reader learns a step is
    happening while it happens, not after it finished."""
    turns = [
        _turn(tool_calls=[ToolCall(name="search_code", arguments={"query": "deleteItem"}, id="call_1")]),
        _turn(content="Found it."),
    ]

    with patch.dict(READ_TOOLS, {"search_code": lambda ctx, query: "### app.js:1-3\n```\ncode\n```"}):
        events = _events(context, turns)

    kinds = [event["type"] for event in events]
    assert kinds.index("tool") < kinds.index("result"), "the tool event must precede its result"
    tool_event = next(event for event in events if event["type"] == "tool")
    assert tool_event["label"] == "Searching the codebase", "the UI shows a label, never a function name"
    assert tool_event["detail"] == "deleteItem"


def test_the_replayed_history_is_responses_shaped(context):
    """A tool result is a `function_call_output` item, never a
    `{"role": "tool"}` message -- the API rejects the latter outright."""
    histories = []
    turns = [
        _turn(
            content="Looking.",
            tool_calls=[ToolCall(name="search_code", arguments={"query": "deleteItem"}, id="c1")],
            reasoning_items=[{"type": "reasoning", "id": "rs_1", "summary": []}],
        ),
        _turn(content="Found it in app.js:22."),
    ]

    def fake(**kwargs):
        histories.append([dict(item) for item in kwargs["messages"]])
        return turns.pop(0)

    with patch.dict(READ_TOOLS, {"search_code": lambda ctx, query: "result text"}), \
         patch("app.azure_client.complete_turn", side_effect=fake):
        list(agent.run(context, "how does deletion work?"))

    second = histories[1]
    assert [item.get("type") or item.get("role") for item in second][-4:] == [
        "reasoning", "assistant", "function_call", "function_call_output",
    ]
    assert isinstance(second[-2]["arguments"], str), "arguments must be a JSON string"
    assert second[-1] == {"type": "function_call_output", "call_id": "c1", "output": "result text"}


def test_a_runaway_loop_is_cut_off_and_still_answers(context):
    """A model that keeps calling tools forever would otherwise spend the
    budget and return nothing at all."""
    looping = _turn(tool_calls=[ToolCall(name="search_code", arguments={}, id="c")])

    with patch.dict(READ_TOOLS, {"search_code": lambda ctx, **kw: "nothing"}):
        with patch.object(agent, "MAX_TOOL_ROUNDS", 2):
            events = _events(context, looping)

    assert events[-1]["type"] in {"done", "error"}
    tool_calls = [event for event in events if event["type"] == "tool"]
    assert len(tool_calls) <= 2, "the round cap must actually bound tool use"


def test_a_dispatch_tool_opens_a_job_card_and_hides_the_marker_from_the_model(context):
    """The job id is addressed to the interface, not to the reasoning. Left
    in the tool result, the model would helpfully repeat the raw marker back
    to the user."""
    turns = [
        _turn(tool_calls=[ToolCall(name="draft_prd", arguments={"requirements": "add presence"}, id="c1")]),
        _turn(content="Started it."),
    ]

    job_id = "3f2b1c4d-0000-0000-0000-000000000000"
    stub = lambda ctx, requirements: f"__JOB__{job_id}__PRD\nStarted a feasibility council."

    with patch.dict(ACTION_TOOLS, {"draft_prd": stub}):
        events = _events(context, turns)

    job_events = [event for event in events if event["type"] == "job"]
    assert len(job_events) == 1
    assert job_events[0]["job_id"] == job_id
    assert job_events[0]["kind"] == "counsel_prd"

    summary = next(event for event in events if event["type"] == "result")["summary"]
    assert "__JOB__" not in summary, "the marker must never reach the UI as prose"


def test_the_prd_council_survives_a_model_that_does_not_return_json():
    """Models wrap JSON in prose and fences. A parse failure must degrade to a
    thin document, never take down a job that already cost several calls."""
    from app.counsel import prd

    assert prd._parse_json("here you go:\n```json\n{\"coverage_score\": 70}\n```", {}) == {"coverage_score": 70}
    assert prd._parse_json("I cannot do that", {"coverage_score": 0}) == {"coverage_score": 0}


def test_requirements_split_prefers_the_users_own_bullets():
    from app.counsel import prd

    bulleted = prd._split_requirements("- presence indicators\n- read receipts\n- typing indicators")
    assert bulleted == ["presence indicators", "read receipts", "typing indicators"]

    prose = prd._split_requirements("We want presence. We also want read receipts.")
    assert len(prose) == 2


def test_the_prd_renders_its_own_ungrounded_list():
    """The most useful thing the arbiter produces: which parts of this
    document the reader should distrust."""
    from app.counsel.prd import PrdResult, _render_markdown

    result = PrdResult(
        title="Comms v2",
        summary="Adding presence and receipts.",
        requirements=[
            {"requirement": "presence", "bucket": "build-new", "reasoning": "nothing speaks WebSocket",
             "citations": ["notify.js:44"], "effort": "L"},
        ],
        coverage_score=55,
        ungrounded=["read receipts"],
    )
    markdown = _render_markdown(result, {"missed": [], "additional_risks": []})
    assert "Build new" in markdown
    assert "notify.js:44" in markdown
    assert "read receipts" in markdown
    assert "55/100" in markdown


def test_the_prd_renders_what_the_web_said_with_its_sources():
    """An external claim earns the same standard as a code claim: a reader can
    follow it back to where it came from."""
    from app.counsel.prd import PrdResult, _render_markdown

    result = PrdResult(
        title="Auth v2",
        summary="Replacing the session cookie.",
        requirements=[],
        coverage_score=80,
        research=[{
            "claim": "Clerk's Backend API requires a secret key sent as a Bearer token.",
            "source_url": "https://clerk.com/docs/reference/backend-api",
            "authority": "official",
            "recency": "current",
        }],
        research_gaps=["no source states the rate limit for the free tier"],
    )
    markdown = _render_markdown(result, {"missed": [], "additional_risks": []})
    assert "What the web says" in markdown
    assert "https://clerk.com/docs/reference/backend-api" in markdown
    assert "official, current" in markdown
    assert "rate limit for the free tier" in markdown


def test_a_prd_with_no_research_renders_no_research_heading():
    """An empty section is worse than no section: it reads as "we looked and
    found nothing" when in fact nothing needed looking up."""
    from app.counsel.prd import PrdResult, _render_markdown

    markdown = _render_markdown(PrdResult(title="t", summary="s"), {"missed": [], "additional_risks": []})
    assert "What the web says" not in markdown


def test_the_research_planner_asks_for_nothing_when_the_code_can_answer():
    """No keyword list: the planner decides per requirement, and an empty plan
    must cost zero searches rather than one 'just in case'."""
    from app.azure_client import ModelTurn, ToolCall
    from app.counsel import prd

    empty_plan = ModelTurn(tool_calls=[ToolCall(name="ResearchPlan", arguments={"questions": []}, id="c1")])
    with patch("app.azure_client.complete_turn", return_value=empty_plan):
        assert prd._plan_research(["rename the delete button"], "acme/demo") == []


def test_a_planner_failure_costs_the_research_not_the_prd():
    from app.counsel import prd

    with patch("app.azure_client.complete_turn", side_effect=RuntimeError("upstream is down")):
        assert prd._plan_research(["integrate Clerk"], "acme/demo") == []


# --- scoped detector runs ----------------------------------------------------


def test_a_scoped_detector_only_examines_its_subtree(tmp_path):
    """Counsel can ask for "just the orders service". Without scoping, every
    directed question re-scans the whole repository."""
    from app.detectors.security import SecurityDetector

    (tmp_path / "safe").mkdir()
    (tmp_path / "risky").mkdir()
    (tmp_path / "risky" / "keys.js").write_text('const k = "AKIA1234567890ABCDEF";')
    (tmp_path / "safe" / "clean.js").write_text("const x = 1;")

    detector = SecurityDetector()
    assert detector.run(str(tmp_path)).failed is True
    assert detector.run(str(tmp_path), path_scope="risky").failed is True
    assert detector.run(str(tmp_path), path_scope="safe").failed is False


def test_a_scope_cannot_escape_the_worktree(tmp_path):
    """The scope reaches these detectors from a model that has been reading
    attacker-controlled repository content. A traversal must fall back to the
    worktree, never walk the host."""
    from app.detectors.security import SecurityDetector

    (tmp_path / "app.js").write_text("const x = 1;")
    result = SecurityDetector().run(str(tmp_path), path_scope="../../../etc")
    assert "No secret-shaped strings" in result.assertion_text


def test_every_detector_accepts_a_scope_even_when_it_cannot_honour_one():
    """Interface parity. A caller must not have to know which detectors can
    be narrowed; the ones that cannot say so rather than silently ignoring
    the argument."""
    import inspect

    from app.detectors import get_detector
    from app.categories import CATEGORY_REGISTRY

    for key in CATEGORY_REGISTRY:
        parameters = inspect.signature(get_detector(key).run).parameters
        assert "path_scope" in parameters, f"{key} cannot be scoped"

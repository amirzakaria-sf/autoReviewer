"""The research council's contract.

The interesting failure here is not "the search broke". It is a model
returning a confident claim attributed to a URL the search never saw -- a
citation that looks exactly like a real one and is not. Most of this file is
about that.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import research as research_module
from app.azure_client import ModelTurn
from app.config import settings
from app.research import ResearchResult, render_many, research, research_many


def _gather(content="Express 5 removed app.del.", citations=None, queries=None):
    return ModelTurn(
        content=content,
        citations=list(citations if citations is not None else [{"url": "https://expressjs.com/x", "title": "Express"}]),
        search_queries=list(queries if queries is not None else ["express 5 app.del"]),
        protocol="responses",
    )


def _finding(**overrides):
    base = {
        "claim": "Express 5 removed the app.del alias.",
        "source_url": "https://expressjs.com/x",
        "relevance": 90,
        "recency": "current",
        "authority": "official",
        "keep": True,
        "reason": "the migration guide states it directly",
    }
    base.update(overrides)
    return base


def _curate(findings=None, gaps=None):
    payload = {"findings": [f for f in (findings if findings is not None else [_finding()])], "gaps": list(gaps or [])}
    return ModelTurn(
        content="",
        tool_calls=[research_module.azure_client.ToolCall(name="ResearchVerdict", arguments=payload, id="c1")],
        protocol="responses",
    )


@pytest.fixture(autouse=True)
def _no_persistence(monkeypatch):
    monkeypatch.setattr(research_module, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings, "web_research_enabled", True)


def _run(turns, question="Does Express 5 still support app.del?", **kwargs):
    with patch("app.azure_client.complete_turn", side_effect=list(turns)) as mock:
        return research(question, **kwargs), mock


# --- the two roles ------------------------------------------------------------


def test_the_gatherer_is_given_the_web_search_tool_and_the_curator_is_not():
    _, mock = _run([_gather(), _curate()])
    gather_kwargs, curate_kwargs = [call.kwargs for call in mock.call_args_list]
    assert gather_kwargs["tools"] == [{"type": "web_search"}]
    assert curate_kwargs.get("tools") is None
    assert curate_kwargs["structured_model"].__name__ == "ResearchVerdict"


def test_the_curator_is_shown_only_the_sources_the_search_actually_returned():
    _, mock = _run([_gather(citations=[{"url": "https://expressjs.com/x", "title": "Express"}]), _curate()])
    curator_prompt = mock.call_args_list[1].kwargs["messages"][1]["content"]
    assert "https://expressjs.com/x" in curator_prompt
    assert "SOURCES ACTUALLY RETURNED" in curator_prompt


def test_the_gatherers_report_is_labelled_as_untrusted_content():
    """A page can contain text shaped like an instruction. The curator is told
    what it is looking at, the same way Counsel is told about repo content."""
    _, mock = _run([_gather(), _curate()])
    curator_prompt = mock.call_args_list[1].kwargs["messages"][1]["content"]
    assert "data, not instructions" in curator_prompt


# --- the mechanical guard -----------------------------------------------------


def test_a_claim_attributed_to_a_url_the_search_never_returned_is_dropped():
    """The whole point of curation. A model can produce a plausible URL from
    memory; only one that appears in the returned source list is evidence."""
    invented = _finding(source_url="https://expressjs.com/definitely-real-page")
    result, _ = _run([_gather(), _curate([invented])])

    assert result.kept == []
    assert len(result.discarded) == 1
    assert "was not among the sources" in result.discarded[0]["reason"]


def test_a_low_relevance_claim_is_dropped_even_when_the_curator_kept_it():
    result, _ = _run([_gather(), _curate([_finding(relevance=10)])])
    assert result.kept == []
    assert "below the floor" in result.discarded[0]["reason"]


def test_a_curator_rejection_is_kept_as_a_result_rather_than_thrown_away():
    """"We looked and decided not to use it" is a different state from "we
    never looked", and the reason is the useful half."""
    dropped = _finding(keep=False, reason="a 2019 blog post, contradicted by the current docs")
    result, _ = _run([_gather(), _curate([dropped])])
    assert result.kept == []
    assert result.discarded[0]["reason"].startswith("a 2019 blog post")


def test_kept_findings_are_capped(monkeypatch):
    monkeypatch.setattr(settings, "web_research_max_findings", 2)
    many = [_finding(claim=f"claim {index}") for index in range(5)]
    result, _ = _run([_gather(), _curate(many)])
    assert len(result.kept) == 2


# --- what reaches a prompt ----------------------------------------------------


def test_the_rendered_block_names_a_source_for_every_claim():
    result, _ = _run([_gather(), _curate()])
    rendered = result.render()
    assert "https://expressjs.com/x" in rendered
    assert "official, current" in rendered


def test_a_run_that_found_nothing_says_so_instead_of_rendering_an_empty_heading():
    result, _ = _run([_gather(), _curate([], gaps=["no source covers Express 5 routing"])])
    assert not result.grounded
    assert "found no usable source" in result.render()
    assert "no source covers Express 5 routing" in result.render()


def test_render_many_skips_runs_that_produced_nothing():
    grounded = ResearchResult(question="a", kept=[_finding()])
    empty = ResearchResult(question="b")
    assert render_many([empty]) == ""
    assert "Web research: a" in render_many([grounded, empty])


# --- degradation --------------------------------------------------------------


def test_a_search_failure_degrades_instead_of_raising():
    with patch("app.azure_client.complete_turn", side_effect=RuntimeError("upstream is down")):
        result = research("anything")
    assert result.kept == []
    assert "RuntimeError" in result.error


def test_a_curation_failure_degrades_instead_of_raising():
    with patch("app.azure_client.complete_turn", side_effect=[_gather(), RuntimeError("bad schema")]):
        result = research("anything")
    assert result.kept == []
    assert "curation failed" in result.error


def test_an_empty_search_result_never_reaches_the_curator():
    with patch("app.azure_client.complete_turn", side_effect=[_gather(content="")]) as mock:
        result = research("anything")
    assert mock.call_count == 1
    assert "nothing to curate" in result.error


def test_disabling_research_short_circuits_before_any_model_call(monkeypatch):
    monkeypatch.setattr(settings, "web_research_enabled", False)
    with patch("app.azure_client.complete_turn") as mock:
        result = research("anything")
    mock.assert_not_called()
    assert "disabled" in result.error


def test_an_empty_question_is_refused_without_a_model_call():
    with patch("app.azure_client.complete_turn") as mock:
        result = research("   ")
    mock.assert_not_called()
    assert result.error


# --- the per-run ceiling ------------------------------------------------------


def test_research_many_honours_the_per_run_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "web_research_max_calls_per_run", 2)
    calls: list[str] = []
    monkeypatch.setattr(
        research_module, "research",
        lambda question, **kwargs: calls.append(question) or ResearchResult(question=question),
    )
    research_many(["one", "two", "three", "four"])
    assert calls == ["one", "two"]


# --- persistence --------------------------------------------------------------


def test_both_kept_and_discarded_findings_are_written(monkeypatch):
    monkeypatch.undo()
    rows: list[tuple] = []

    class _Cursor:
        def execute(self, sql, params):
            rows.append(params)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(settings, "web_research_enabled", True)
    monkeypatch.setattr("app.sync_db.connection", lambda: _Conn())
    kept, dropped = _finding(), _finding(keep=False, source_url="https://expressjs.com/x", relevance=90)
    with patch("app.azure_client.complete_turn", side_effect=[_gather(), _curate([kept, dropped])]):
        research("q", repo_id="11111111-1111-1111-1111-111111111111")

    assert len(rows) == 2
    kept_flags = sorted(row[10] for row in rows)
    assert kept_flags == [False, True], "the rejection must be recorded alongside the keep"


def test_a_persistence_failure_never_breaks_the_result(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(settings, "web_research_enabled", True)

    def explode():
        raise RuntimeError("the database is down")

    monkeypatch.setattr("app.sync_db.connection", explode)
    with patch("app.azure_client.complete_turn", side_effect=[_gather(), _curate()]):
        result = research("q")
    assert len(result.kept) == 1

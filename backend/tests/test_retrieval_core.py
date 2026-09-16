"""Hermetic tests for the context/retrieval core: token budgeting, BM25, and
RRF fusion's merge + scoring rules.

No database and no embedding calls -- the channels that need those are
injected, so what is under test is the fusion arithmetic and the span-merge
identity rather than Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.hybrid_retrieval import _Candidate, _absorb, _same_span, query_identifiers
from app.lexical_index import rank_chunk_metas, tokenize
from app.prompt_compiler import ContextChunk, Priority, compile_context, count_tokens, fit_text


# --- prompt compiler ---------------------------------------------------------


def test_mandatory_survives_a_budget_it_alone_exceeds():
    compiled = compile_context([ContextChunk(Priority.MANDATORY, "question", "word " * 500)], budget=50)
    assert compiled.kept == ["question"]
    assert compiled.total_tokens > 50, "mandatory content is included even when it overruns"


def test_low_priority_is_dropped_before_high_priority():
    compiled = compile_context(
        [
            ContextChunk(Priority.HIGH, "diff", "diff " * 40),
            ContextChunk(Priority.LOW, "repo-map", "map " * 4000),
        ],
        budget=120,
    )
    assert "diff" in compiled.kept
    assert "repo-map" in compiled.dropped or "repo-map" in compiled.truncated
    assert "diff" not in compiled.dropped


def test_truncation_is_reported_not_silent():
    compiled = compile_context(
        [ContextChunk(Priority.MEDIUM, "code", "x " * 5000)],
        budget=1000,
    )
    assert compiled.truncated == ["code"]
    assert compiled.total_tokens <= 1000


def test_fit_text_respects_a_token_ceiling():
    assert count_tokens(fit_text("alpha beta gamma " * 500, 60)) <= 60


# --- lexical index -----------------------------------------------------------


def test_identifier_splitting_makes_prose_queries_match_camel_case():
    assert tokenize("getUserProfile") == ["getuserprofile", "get", "user", "profile"]


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.js").write_text(
        "function deleteItem(index) {\n  items.splice(index, 1);\n}\n\n"
        "function renderList() {\n  return items.map(row => row.label);\n}\n",
    )
    (tmp_path / "README.md").write_text("A todo app. Nothing about splicing here.\n")
    # Generated noise that used to out-rank real source on every query.
    (tmp_path / "package-lock.json").write_text('{"name":"deleteItem splice index render list"}\n' * 200)
    return tmp_path


def test_bm25_ranks_the_defining_file_above_prose(sample_repo: Path):
    ranked = rank_chunk_metas(sample_repo, "deleteItem splice", limit=5)
    assert ranked, "expected at least one hit"
    assert ranked[0][0]["path"] == "app.js"


def test_lockfiles_are_never_indexed(sample_repo: Path):
    paths = {meta["path"] for meta, _ in rank_chunk_metas(sample_repo, "deleteItem splice index", limit=20)}
    assert "package-lock.json" not in paths


def test_index_rebuilds_when_a_file_changes(sample_repo: Path):
    assert not rank_chunk_metas(sample_repo, "brandNewSymbol", limit=3)
    (sample_repo / "app.js").write_text("function brandNewSymbol() { return 1; }\n")
    assert rank_chunk_metas(sample_repo, "brandNewSymbol", limit=3), "a rewritten file must invalidate the index"


# --- fusion ------------------------------------------------------------------


def test_query_identifiers_keeps_code_shaped_terms_and_drops_prose():
    terms = query_identifiers("deleteItem throws TypeError when the list is empty")
    assert "deleteItem" in terms
    assert "TypeError" in terms
    assert "when" not in terms and "the" not in terms


def test_overlapping_spans_in_the_same_file_are_one_candidate():
    left = _Candidate(path="app.js", start_line=20, end_line=40)
    right = _Candidate(path="app.js", start_line=22, end_line=37)
    assert _same_span(left, right)


def test_same_lines_in_different_files_are_not_merged():
    assert not _same_span(
        _Candidate(path="a.js", start_line=1, end_line=10),
        _Candidate(path="b.js", start_line=1, end_line=10),
    )


def test_absorb_unions_channels_and_widens_the_span():
    pool: list[_Candidate] = []
    _absorb(pool, _Candidate(path="app.js", start_line=22, end_line=37, ranks={"lexical": 1}))
    _absorb(pool, _Candidate(path="app.js", start_line=20, end_line=40, text="body", ranks={"dense": 3}))
    assert len(pool) == 1, "the same code found twice must not reach the model twice"
    assert pool[0].ranks == {"lexical": 1, "dense": 3}
    assert (pool[0].start_line, pool[0].end_line) == (20, 40)
    assert pool[0].text == "body"


def test_agreement_across_channels_beats_confidence_within_one():
    """The core claim of RRF, and the reason raw scores are not combined."""
    from app.hybrid_retrieval import _RRF_K, _W_DENSE, _W_LEXICAL, _W_STRUCTURAL

    agreed = (
        _W_LEXICAL / (_RRF_K + 4) + _W_DENSE / (_RRF_K + 5) + _W_STRUCTURAL / (_RRF_K + 3)
    )
    single_channel_rank_one = _W_STRUCTURAL / (_RRF_K + 1)
    assert agreed > single_channel_rank_one

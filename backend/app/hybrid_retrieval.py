"""One retriever, fusing every index this platform already pays for.

WhipGuard maintains three code indexes and, until this module, used exactly
one of them for retrieval: `app/retrieval.py`'s pgvector cosine search. The
BM25 index (`app/lexical_index.py`) and the call graph (`app/graph_index.py`)
were written and maintained on every push and read by nothing on the
retrieval path -- and the strongest signal in a bug-fixing agent's world (the
issue names an exact identifier, or pastes a literal error string) had no
channel of its own at all.

Why reciprocal-rank fusion rather than combining the raw scores: BM25 scores
and cosine similarities are not on the same scale, are not comparable between
queries, and are not even monotone in the same direction. Any weighting of
the raw numbers encodes a guess about their distributions that nobody can
defend. Rank is the one thing every channel agrees on, so the fusion uses
only rank -- `weight / (k + rank)` per channel, summed. `k = 60` is the value
the literature uses; its job is to flatten the gap between rank 1 and rank 2
so a single channel cannot win on its own confidence alone.

**Candidates are merged in Python on (file_path, span overlap), never by a
SQL join on row id.** The dense chunks and the BM25 chunks live in different
stores with independent identity; their ids line up by accident and mean
nothing.

Every channel is optional and every failure is silent. A retriever that
raises has taken a council run down to improve a ranking.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger("whipguard.hybrid_retrieval")

# Large relative to the ranks in play, which is the point: it compresses the
# gap between the top few results of any one channel so agreement ACROSS
# channels outweighs confidence WITHIN one.
_RRF_K = 60

# Deliberately close together. These express "an identifier the caller
# literally named is worth more than a graph neighbour, which is worth more
# than a dense hit, which is worth more than a lexical hit" -- not a
# calibrated model. Anything more precise would be fitted to a benchmark
# that does not exist yet.
_W_STRUCTURAL = 2.0
_W_GRAPH = 1.5
_W_DENSE = 1.2
_W_LEXICAL = 1.0

# How deep each channel goes before fusing. Deep enough that a result ranked
# poorly by one channel can still win on agreement; shallow enough that the
# dense channel's Postgres query stays cheap.
_CHANNEL_DEPTH = 50

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
# An exception class, a traceback line, a quoted message -- exactly what a
# failing assertion or a retry-with-feedback carries, and exactly the terms
# BM25 dilutes across a corpus that mentions them everywhere.
_ERRORISH_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b"
    r"|(?<=')[^']{4,80}(?=')"
    r"|(?<=\")[^\"]{4,80}(?=\")",
)
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "when", "then", "you",
        "your", "our", "add", "use", "make", "should", "must", "not", "but", "are", "was",
        "has", "have", "all", "any", "can", "will", "new", "old", "get", "set", "run",
        "file", "code", "test", "tests", "line", "page", "user", "users", "data", "task",
        "fix", "bug", "issue", "error", "fail", "failed", "expected", "actual",
    },
)


@dataclass(frozen=True)
class FusedChunk:
    """One retrieval result, with the evidence for why it is here.

    `channels` is kept because a fused score on its own is unfalsifiable:
    when a retrieval is wrong the only useful question is which channel put
    the result there, and the only way to answer it later is to have
    recorded it at the time.
    """

    path: str
    start_line: int
    end_line: int
    symbol: str
    text: str
    score: float
    channels: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        header = f"{self.path}:{self.start_line}-{self.end_line}"
        if self.symbol:
            header += f"  ({self.symbol})"
        return f"### {header}\n```\n{self.text}\n```"


@dataclass
class _Candidate:
    path: str
    start_line: int
    end_line: int
    symbol: str = ""
    text: str = ""
    meta: dict | None = None
    ranks: dict[str, int] = field(default_factory=dict)
    graph_depth: int | None = None


def _same_span(left: _Candidate, right: _Candidate) -> bool:
    """Whether two candidates are the same piece of code.

    Same file, and the overlap covers at least half of the SHORTER span.
    Half of the shorter one rather than of either: the dense index and the
    BM25 index chunk the same file at slightly different boundaries, so a
    function body found by one is routinely a sub-range of the enclosing
    block found by the other. Requiring half of the longer span would treat
    those as different results and hand the model the same code twice.
    """
    if left.path != right.path:
        return False
    start = max(left.start_line, right.start_line)
    end = min(left.end_line, right.end_line)
    if end < start:
        return False
    overlap = end - start + 1
    shorter = min(left.end_line - left.start_line + 1, right.end_line - right.start_line + 1)
    return shorter > 0 and overlap * 2 >= shorter


def _absorb(pool: list[_Candidate], incoming: _Candidate) -> None:
    for existing in pool:
        if _same_span(existing, incoming):
            for channel, rank in incoming.ranks.items():
                existing.ranks.setdefault(channel, rank)
            # Keep the widest span and whatever text/symbol we have, so the
            # surviving record is the most useful of the two rather than
            # whichever channel happened to run first.
            existing.start_line = min(existing.start_line, incoming.start_line)
            existing.end_line = max(existing.end_line, incoming.end_line)
            existing.symbol = existing.symbol or incoming.symbol
            existing.text = existing.text or incoming.text
            existing.meta = existing.meta or incoming.meta
            return
    pool.append(incoming)


def query_identifiers(text: str) -> list[str]:
    """Identifier-shaped and error-shaped terms worth matching LITERALLY.

    A bug report's query routinely contains the exact string that appears in
    the code (`deleteItem`, `TypeError`, `'connection refused'`), and
    term-frequency ranking actively dilutes those against a corpus that
    mentions them everywhere. Matching them literally is both cheaper and
    more precise than scoring them.
    """
    seen: list[str] = []
    for pattern in (_ERRORISH_RE, _IDENTIFIER_RE):
        for match in pattern.findall(text or ""):
            term = str(match).strip()
            if not term or term.lower() in _STOPWORDS:
                continue
            # A bare lowercase word is prose unless it is snake_case or
            # camelCase; those carry a shape the codebase actually uses.
            if pattern is _IDENTIFIER_RE and term.islower() and "_" not in term:
                continue
            if term not in seen:
                seen.append(term)
    return seen[:12]


def _lexical_channel(pool: list[_Candidate], *, worktree_path: str, query: str) -> None:
    from app import lexical_index

    for rank, (meta, _score) in enumerate(
        lexical_index.rank_chunk_metas(worktree_path, query, limit=_CHANNEL_DEPTH), start=1,
    ):
        _absorb(
            pool,
            _Candidate(
                path=str(meta.get("path") or ""),
                start_line=int(meta.get("start_line") or 1),
                end_line=int(meta.get("end_line") or 1),
                symbol=str(meta.get("symbol") or ""),
                meta=meta,
                ranks={"lexical": rank},
            ),
        )


def _dense_channel(pool: list[_Candidate], *, repo_id, query: str) -> None:
    from app.retrieval import similar_code_chunks

    for rank, row in enumerate(similar_code_chunks(repo_id, query, k=_CHANNEL_DEPTH), start=1):
        _absorb(
            pool,
            _Candidate(
                path=str(row.get("file_path") or ""),
                start_line=int(row.get("start_line") or 1),
                end_line=int(row.get("end_line") or 1),
                symbol=str(row.get("symbol_name") or ""),
                # Already stored, so this channel costs no disk read.
                text=str(row.get("content") or ""),
                ranks={"dense": rank},
            ),
        )


def _structural_channel(pool: list[_Candidate], *, worktree_path: str, terms: list[str]) -> None:
    """Literal identifier / path / error-string matches, ranked by specificity."""
    from app import lexical_index

    if not terms:
        return
    lowered = [term.lower() for term in terms]
    hits: list[tuple[int, dict]] = []
    for meta in lexical_index.all_chunk_metas(worktree_path):
        symbol_lower = str(meta.get("symbol") or "").lower()
        path_lower = str(meta.get("path") or "").lower()
        # 0 = the chunk IS the named symbol, 1 = its path names the term,
        # 2 = the term appears inside the symbol name. Lower sorts first.
        specificity: int | None = None
        for term in lowered:
            if symbol_lower and symbol_lower == term:
                specificity = 0
                break
            if term in path_lower:
                specificity = min(specificity if specificity is not None else 9, 1)
            elif symbol_lower and term in symbol_lower:
                specificity = min(specificity if specificity is not None else 9, 2)
        if specificity is not None:
            hits.append((specificity, meta))

    hits.sort(key=lambda item: item[0])
    for rank, (_specificity, meta) in enumerate(hits[:_CHANNEL_DEPTH], start=1):
        _absorb(
            pool,
            _Candidate(
                path=str(meta.get("path") or ""),
                start_line=int(meta.get("start_line") or 1),
                end_line=int(meta.get("end_line") or 1),
                symbol=str(meta.get("symbol") or ""),
                meta=meta,
                ranks={"structural": rank},
            ),
        )


def _graph_priors(*, repo_id, terms: list[str]) -> dict[str, int]:
    """`file_path -> shortest hop distance` from a symbol the query named.

    A FILE-level prior, not a candidate of its own. The graph knows which
    files are implicated by a change but not which span inside them answers
    the question -- so it boosts results the other channels already found in
    those files rather than inventing results of its own. That distinction is
    why a wrong edge here costs ranking rather than correctness.
    """
    priors: dict[str, int] = {}
    if not terms or not repo_id:
        return priors
    try:
        from app.graph_index import find_symbols, symbol_blast_radius

        for term in terms[:3]:
            for hit in find_symbols(repo_id, term, limit=2):
                path = str(hit.get("path") or "")
                if path:
                    priors[path] = 0
                for row in symbol_blast_radius(repo_id, str(hit.get("name") or term), max_depth=2):
                    dependent = str(row.get("path") or "")
                    depth = int(row.get("depth") or 1)
                    if dependent and depth < priors.get(dependent, 99):
                        priors[dependent] = depth
    except Exception as error:  # noqa: BLE001 - a prior is an optimisation, never a gate
        logger.warning("graph prior failed for repo=%s: %s", repo_id, error)
    return priors


def hybrid_retrieve(
    query: str,
    *,
    repo_id,
    worktree_path: str,
    limit: int | None = None,
    extra_terms: list[str] | None = None,
) -> list[FusedChunk]:
    """Fuse every available channel and return the best chunks. Never raises.

    `extra_terms` feeds the structural channel with identifiers the CALLER
    already knows are relevant -- the failing assertion from a prior attempt,
    a path the detector named -- which is the case pure query text cannot
    express.
    """
    started = time.monotonic()
    budget = max(float(getattr(settings, "hybrid_retrieval_timeout_seconds", 8.0) or 8.0), 0.5)
    cap = limit or max(int(getattr(settings, "hybrid_retrieval_max_chunks", 6) or 6), 1)
    max_chars = max(int(getattr(settings, "hybrid_retrieval_max_chars", 24000) or 24000), 1000)
    if not query or not worktree_path:
        return []

    terms = query_identifiers(query)
    for term in extra_terms or []:
        if term and term not in terms:
            terms.append(term)

    pool: list[_Candidate] = []

    def _within_budget() -> bool:
        return (time.monotonic() - started) < budget

    for name, run in (
        # Cheapest and most precise first, so a channel skipped for budget is
        # the least valuable one rather than whichever happened to be last.
        ("structural", lambda: _structural_channel(pool, worktree_path=worktree_path, terms=terms)),
        ("lexical", lambda: _lexical_channel(pool, worktree_path=worktree_path, query=query)),
        ("dense", lambda: _dense_channel(pool, repo_id=repo_id, query=query)),
    ):
        if not _within_budget():
            logger.info("skipped %s channel -- retrieval budget spent", name)
            continue
        try:
            run()
        except Exception as error:  # noqa: BLE001 - one dead channel must not lose the others
            logger.warning("%s channel failed: %s", name, error)

    if not pool:
        return []

    priors = _graph_priors(repo_id=repo_id, terms=terms) if _within_budget() else {}
    for candidate in pool:
        if candidate.path in priors:
            candidate.graph_depth = priors[candidate.path]

    weights = {"structural": _W_STRUCTURAL, "lexical": _W_LEXICAL, "dense": _W_DENSE}
    scored: list[tuple[float, _Candidate]] = []
    for candidate in pool:
        score = sum(weights[channel] / (_RRF_K + rank) for channel, rank in candidate.ranks.items())
        if candidate.graph_depth is not None:
            score += _W_GRAPH / (_RRF_K + candidate.graph_depth)
        scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line))

    from app import lexical_index

    results: list[FusedChunk] = []
    characters = 0
    for score, candidate in scored:
        if len(results) >= cap or characters >= max_chars:
            break
        text = candidate.text
        if not text and candidate.meta is not None:
            chunk = lexical_index.chunk_from_meta(worktree_path, candidate.meta)
            if chunk is None:
                continue
            text, candidate.symbol = chunk.text, candidate.symbol or chunk.symbol
        if not text:
            continue
        characters += len(text)
        channels = tuple(sorted(candidate.ranks))
        if candidate.graph_depth is not None:
            channels = (*channels, "graph")
        results.append(
            FusedChunk(
                path=candidate.path,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                symbol=candidate.symbol,
                text=text,
                score=round(score, 6),
                channels=channels,
            ),
        )
    logger.info(
        "repo=%s candidates=%s returned=%s elapsed_ms=%s",
        repo_id, len(pool), len(results), int((time.monotonic() - started) * 1000),
    )
    return results


def render(repo_name: str, query: str, chunks: list[FusedChunk]) -> str:
    if not chunks:
        return (
            f"No source in {repo_name} matched '{query}'. The code may not exist yet, or the "
            "wording may not appear in the codebase -- try naming a concrete file path, "
            "function, or the literal error text instead."
        )
    header = f"Source from {repo_name} matching '{query}' (real file contents, not a summary):"
    return "\n\n".join([header, *(chunk.render() for chunk in chunks)])

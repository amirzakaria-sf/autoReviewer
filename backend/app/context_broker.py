"""Pre-briefing a council so the frontier model does not pay to rediscover.

A council node that starts with only the finding text spends its reasoning
budget re-deriving what this system already knows: which symbol the failure
names, where that symbol lives, what calls it, what was already tried against
it and rejected. Each of those is a question the indexes answer for free, and
none of them needs a model.

So the briefing is assembled before the call, and it is **deterministic
first** -- fused retrieval, the call graph, the symbol index, this repo's own
failure history. A cheap-model compression pass runs ONLY if the assembled
text overflows its budget; spending a model call to shorten something already
inside its budget is pure loss.

Two things the briefing deliberately does NOT do:

- It never claims to be complete. It is labelled as a starting point, because
  a council that believes the listed files are the only relevant ones stops
  looking, and the one file retrieval missed is exactly the one that matters.
- It never carries a previous run's transcript, only its outcome. Passing
  transcripts between agents is how multi-agent systems quietly go quadratic
  in tokens.

Never raises. An empty briefing is a valid outcome -- the council falls back
to exactly the context it had before this module existed.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.prompt_compiler import ContextChunk, Priority, compile_context, fit_text

logger = logging.getLogger("whipguard.context_broker")

_MAX_SYMBOLS = 10
_MAX_CHUNKS = 4
_BRIEFING_BUDGET_TOKENS = 3000


def _located_symbols(repo_id, terms: list[str]) -> str:
    """Symbols the finding names, resolved to real file:line locations, so
    the model does not spend a turn discovering that `deleteItem` lives
    somewhere other than where it assumed."""
    try:
        from app.graph_index import find_symbols

        lines: list[str] = []
        for term in terms[:_MAX_SYMBOLS]:
            for hit in find_symbols(repo_id, term, limit=2):
                lines.append(f"- {hit['kind']} {hit['name']} -- {hit['path']}:{hit['line']}")
        if not lines:
            return ""
        return "SYMBOLS THIS FINDING MENTIONS (already located for you):\n" + "\n".join(dict.fromkeys(lines))
    except Exception as error:  # noqa: BLE001 - a briefing is an optimisation, never a gate
        logger.warning("symbol lookup failed: %s", error)
        return ""


def _blast_radius(repo_id, terms: list[str]) -> str:
    """What breaks if the fix changes what it says it will change."""
    try:
        from app.graph_index import find_symbols, symbol_blast_radius

        blocks: list[str] = []
        for term in terms[:3]:
            if not find_symbols(repo_id, term, limit=1):
                continue
            radius = symbol_blast_radius(repo_id, term, max_depth=2)
            if not radius:
                continue
            rows = sorted(radius, key=lambda item: (item["depth"], item["path"]))[:10]
            rendered = "\n".join(f"  - {r['name']} ({r['path']}:{r['line']}) at {r['depth']} hop(s)" for r in rows)
            blocks.append(f"Changing `{term}` is reachable from:\n{rendered}")
        return "\n\n".join(blocks)
    except Exception as error:  # noqa: BLE001
        logger.warning("blast radius failed: %s", error)
        return ""


def _retrieved_code(repo_name: str, repo_id, worktree_path: str, query: str, extra_terms: list[str]) -> str:
    try:
        if not getattr(settings, "hybrid_retrieval_enabled", True):
            return ""
        from app.hybrid_retrieval import hybrid_retrieve, render

        fused = hybrid_retrieve(
            query, repo_id=repo_id, worktree_path=worktree_path, limit=_MAX_CHUNKS, extra_terms=extra_terms,
        )
        return render(repo_name, "this finding", fused) if fused else ""
    except Exception as error:  # noqa: BLE001
        logger.warning("hybrid retrieval failed: %s", error)
        return ""


def _prior_attempts(repo_id, query: str, exclude_run_id) -> str:
    try:
        from app.memory_traces import render_history, search_history

        rows = search_history(repo_id, query, exclude_run_id=exclude_run_id)
        return render_history(query, rows) if rows else ""
    except Exception as error:  # noqa: BLE001
        logger.warning("history lookup failed: %s", error)
        return ""


def build_briefing(
    *,
    repo_id,
    repo_name: str,
    worktree_path: str,
    finding_text: str,
    target_files: list[str] | None = None,
    prior_feedback: str = "",
    exclude_run_id=None,
    budget_tokens: int = _BRIEFING_BUDGET_TOKENS,
) -> str:
    """Assemble the briefing. Never raises; "" is a valid result.

    `target_files` and `prior_feedback` exist to feed the retriever's
    structural channel with what the CALLER already knows and the finding
    text cannot say: the paths the detector named, and the exact identifiers
    a previous attempt was rejected over. On a retry those are the
    highest-signal terms in the whole system.
    """
    try:
        from app.hybrid_retrieval import query_identifiers
        from app.memory_traces import structural_terms

        terms = query_identifiers(finding_text)
        extra_terms: list[str] = []
        for path in target_files or []:
            text = str(path or "").strip()
            if text and text not in extra_terms:
                extra_terms.append(text)
        for term in query_identifiers(prior_feedback or ""):
            if term not in extra_terms:
                extra_terms.append(term)
        # Terms drawn from what this repo has historically broken on -- they
        # appear nowhere in the current finding's own text.
        for term in structural_terms(repo_id, limit=4):
            if term not in extra_terms:
                extra_terms.append(term)

        chunks = [
            ContextChunk(Priority.HIGH, "prior-attempts", _prior_attempts(repo_id, finding_text, exclude_run_id)),
            ContextChunk(Priority.HIGH, "symbols", _located_symbols(repo_id, terms + extra_terms)),
            ContextChunk(Priority.HIGH, "blast-radius", _blast_radius(repo_id, terms)),
            ContextChunk(
                Priority.MEDIUM,
                "retrieved-code",
                _retrieved_code(repo_name, repo_id, worktree_path, finding_text, extra_terms),
            ),
        ]
        compiled = compile_context(chunks, budget=budget_tokens)
        text = compiled.text.strip()
        if not text:
            return ""

        if compiled.dropped or compiled.truncated:
            logger.info(
                "briefing budget pressure: kept=%s truncated=%s dropped=%s",
                compiled.kept, compiled.truncated, compiled.dropped,
            )

        return (
            "CONTEXT BRIEFING (assembled from this repo's call graph, symbol index, "
            "retrieval indexes and its own failure history). It is a STARTING POINT, "
            "not a complete list -- read further if the finding needs it.\n\n" + text
        )
    except Exception as error:  # noqa: BLE001 - never fail a council over its own briefing
        logger.warning("briefing assembly failed: %s", error)
        return ""


def summarize_for_prompt(text: str, max_tokens: int = 800) -> str:
    """Budget an arbitrary blob for injection. Replaces the ad-hoc `[:2000]`
    character slices the detectors and councils use today, which measure the
    wrong unit and are each chosen with no idea what else is in the prompt."""
    return fit_text(text or "", max_tokens)

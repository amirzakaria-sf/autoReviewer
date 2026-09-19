"""Two questions the councils never had to ask, and Counsel cannot work without.

**"What shape is this project?"** — a ranked skeleton of the repository, so an
agent does not burn three turns on `list_directory` to learn what it is
looking at. Files are ranked by how much the rest of the codebase depends on
them, which is a far better proxy for importance than size or path depth: an
80-line module that thirty files import matters more than a 900-line leaf
nobody calls.

**"Which files ARE the payments system?"** — the harder one, and the thing
that gates explaining a subsystem at all. There is no table of subsystems and
there never will be, because a subsystem is not a directory: it is whatever
set of code cooperates to do one job, and it routinely spans folders.

So a subsystem is *derived*: retrieval finds seed symbols for the phrase, the
call graph expands outward from those seeds, and the result is scored by how
strongly each file connects back to the seeds. That produces a boundary an
engineer recognises, without anyone having to declare one.

Both are best-effort and cached alongside the BM25 index. An agent that
cannot orient is worse than one that answers slowly; an agent that crashes
while orienting is worse than both.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("whipguard.repo_map")

# Damping for the importance ranking, the standard PageRank value: the
# probability that the walk follows an edge rather than jumping at random.
_DAMPING = 0.85
_ITERATIONS = 20
_MAX_MAP_FILES = 40
_MAX_SUBSYSTEM_FILES = 25
# A bare identifier, not the "path.js:first line of the body" label the dense
# chunker produces.
_IDENTIFIER_ONLY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class FileRank:
    path: str
    score: float
    symbols: list[str] = field(default_factory=list)
    inbound: int = 0


def _edges_for_repo(repo_id) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """`(file -> files it depends on, file -> its symbol names)`.

    Read from the symbol/edge tables the graph indexer already maintains, so
    this costs one query rather than a re-parse of the tree.
    """
    import psycopg

    from app import sync_db

    depends_on: dict[str, set[str]] = defaultdict(set)
    symbols_by_file: dict[str, list[str]] = defaultdict(list)

    with sync_db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT path, name FROM symbols WHERE repo_id = %s", (str(repo_id),))
        for path, name in cur.fetchall():
            symbols_by_file[path].append(name)

        cur.execute(
            """
            SELECT s_from.path, s_to.path
            FROM edges e
            JOIN symbols s_from ON s_from.id = e.from_symbol_id
            JOIN symbols s_to   ON s_to.id   = e.to_symbol_id
            WHERE s_from.repo_id = %s AND s_to.repo_id = %s
            """,
            (str(repo_id), str(repo_id)),
        )
        for caller_path, callee_path in cur.fetchall():
            if caller_path != callee_path:
                depends_on[caller_path].add(callee_path)

    return depends_on, symbols_by_file


def _pagerank(depends_on: dict[str, set[str]], nodes: set[str]) -> dict[str, float]:
    """Rank by inbound dependency weight.

    Edges point caller -> callee, and rank flows the same way, so a file that
    many others call accumulates score. That is the definition of "important"
    that matters to someone trying to understand a codebase: the thing
    everything leans on.
    """
    if not nodes:
        return {}
    rank = {node: 1.0 / len(nodes) for node in nodes}
    for _ in range(_ITERATIONS):
        incoming: dict[str, float] = {node: 0.0 for node in nodes}
        leaked = 0.0
        for source in nodes:
            targets = depends_on.get(source, set()) & nodes
            if not targets:
                # A file that depends on nothing would otherwise trap its own
                # rank; spreading it keeps the totals stable.
                leaked += rank[source]
                continue
            share = rank[source] / len(targets)
            for target in targets:
                incoming[target] += share
        spread = leaked / len(nodes)
        rank = {
            node: (1 - _DAMPING) / len(nodes) + _DAMPING * (incoming[node] + spread)
            for node in nodes
        }
    return rank


def build_repo_map(repo_id, *, limit: int = _MAX_MAP_FILES) -> list[FileRank]:
    """The project's skeleton, most-depended-on first. Never raises."""
    try:
        depends_on, symbols_by_file = _edges_for_repo(repo_id)
        nodes = set(symbols_by_file) | {t for targets in depends_on.values() for t in targets}
        if not nodes:
            return []

        rank = _pagerank(depends_on, nodes)
        inbound: dict[str, int] = defaultdict(int)
        for targets in depends_on.values():
            for target in targets:
                inbound[target] += 1

        ranked = [
            FileRank(
                path=path,
                score=round(rank.get(path, 0.0), 6),
                symbols=sorted(symbols_by_file.get(path, []))[:8],
                inbound=inbound.get(path, 0),
            )
            for path in nodes
        ]
        ranked.sort(key=lambda item: (-item.score, item.path))
        return ranked[:limit]
    except Exception as error:  # noqa: BLE001 - orientation is never worth failing over
        logger.warning("repo map failed for repo=%s: %s", repo_id, error)
        return []


def render_repo_map(repo_name: str, ranked: list[FileRank]) -> str:
    if not ranked:
        return f"No indexed structure for {repo_name} yet — the repo may not have been scanned."
    lines = [
        f"STRUCTURE OF {repo_name}, most-depended-on first "
        "(ranked by how much the rest of the code leans on each file):",
    ]
    for item in ranked:
        suffix = f"  <- {item.inbound} caller(s)" if item.inbound else ""
        lines.append(f"- {item.path}{suffix}")
        if item.symbols:
            lines.append(f"    {', '.join(item.symbols)}")
    return "\n".join(lines)


@dataclass
class Subsystem:
    query: str
    files: list[str]
    seed_symbols: list[str]
    entry_points: list[str]


def find_subsystem(query: str, *, repo_id, worktree_path: str, limit: int = _MAX_SUBSYSTEM_FILES) -> Subsystem:
    """Derive the file set that implements `query`. Never raises.

    Three passes, because no single signal is enough: retrieval finds where
    the words appear, the graph finds what that code is connected to, and the
    ranking keeps whatever both agree on. Retrieval alone returns scattered
    mentions; the graph alone has no idea which component you meant.
    """
    seeds: list[str] = []
    scores: dict[str, float] = defaultdict(float)

    try:
        from app.hybrid_retrieval import hybrid_retrieve, query_identifiers

        # Pass 1 — where does this language actually appear in the code?
        for chunk in hybrid_retrieve(query, repo_id=repo_id, worktree_path=worktree_path, limit=12):
            scores[chunk.path] += chunk.score * 100
            # Only real identifiers. The dense index names some chunks
            # "path.js:function foo(bar) {" -- useful as a label, useless as
            # a graph lookup, and embarrassing when shown to a user as "the
            # symbol that anchored this".
            if chunk.symbol and _IDENTIFIER_ONLY.fullmatch(chunk.symbol):
                seeds.append(chunk.symbol)
        for term in query_identifiers(query):
            seeds.append(term)
    except Exception as error:  # noqa: BLE001
        logger.warning("subsystem retrieval failed: %s", error)

    try:
        from app.graph_index import find_symbols, symbol_blast_radius

        # Pass 2 — pull in what those seeds are wired to. Neighbours count
        # for less than the seeds themselves, and less again the further out
        # they sit, so an incidental two-hop utility does not join the
        # subsystem on the strength of one call.
        # dict.fromkeys keeps first-seen order while de-duplicating, which
        # matters: retrieval returns its seeds best-first.
        for seed in list(dict.fromkeys(seeds))[:8]:
            for hit in find_symbols(repo_id, seed, limit=2):
                scores[str(hit.get("path") or "")] += 3.0
            for row in symbol_blast_radius(repo_id, seed, max_depth=2):
                depth = max(int(row.get("depth") or 1), 1)
                scores[str(row.get("path") or "")] += 2.0 / depth
    except Exception as error:  # noqa: BLE001
        logger.warning("subsystem graph expansion failed: %s", error)

    scores.pop("", None)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    files = [path for path, _ in ranked]

    # Pass 3 — entry points: files in the subsystem that nothing else in the
    # subsystem calls. That is where an explanation should start.
    entry_points: list[str] = []
    try:
        depends_on, _ = _edges_for_repo(repo_id)
        inside = set(files)
        called_from_inside = {
            target for source, targets in depends_on.items() if source in inside for target in targets & inside
        }
        if called_from_inside:
            entry_points = [path for path in files if path not in called_from_inside][:5]
        else:
            # No internal edges (a small or un-indexed subsystem): claiming
            # every file is an entry point tells the reader nothing, so fall
            # through to the ranked head below instead.
            entry_points = []
    except Exception as error:  # noqa: BLE001
        logger.warning("entry-point detection failed: %s", error)

    return Subsystem(
        query=query,
        files=files,
        seed_symbols=list(dict.fromkeys(seeds))[:12],
        entry_points=entry_points or files[:3],
    )


def render_subsystem(repo_name: str, subsystem: Subsystem) -> str:
    if not subsystem.files:
        return (
            f"Nothing in {repo_name} matched '{subsystem.query}'. That vocabulary may not appear in "
            "the code — try naming a concrete file, function, or the literal text of an error."
        )
    lines = [
        f"FILES THAT IMPLEMENT '{subsystem.query}' IN {repo_name} "
        "(derived from retrieval + the call graph, not a declared boundary — treat it as a "
        "starting point, not a complete list):",
        "",
        "Start here:",
        *(f"  - {path}" for path in subsystem.entry_points),
    ]
    remainder = [path for path in subsystem.files if path not in subsystem.entry_points]
    if remainder:
        lines += ["", "Also involved:", *(f"  - {path}" for path in remainder)]
    if subsystem.seed_symbols:
        lines += ["", f"Symbols that anchored this: {', '.join(subsystem.seed_symbols)}"]
    return "\n".join(lines)

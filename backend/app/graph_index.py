"""The dependency graph, as edges in Postgres, not a dedicated graph database
(plan.md §5.3). Symbols are extracted with regex-based boundary detection --
the same pragmatic choice app/embeddings.py's chunk_file already makes, named
honestly rather than dressed up as a real parser. A real AST parser
(tree-sitter) is the correct upgrade if the questions asked of this graph
grow past "what calls this symbol" -- not needed for that question today.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import psycopg

from app.retrieval import _sync_dsn

logger = logging.getLogger("whipguard.graph_index")

_INDEXABLE_SUFFIXES = (".js", ".ts", ".jsx", ".tsx")
_SKIP_DIRS = {"node_modules", ".git", "test-results", "playwright-report"}

_SYMBOL_PATTERN = re.compile(
    r"^(?:function\s+(?P<fn_name>\w+)"
    r"|const\s+(?P<const_name>\w+)\s*=\s*(?:\([^)]*\)|[\w]+)\s*=>"
    r"|class\s+(?P<class_name>\w+))",
    re.MULTILINE,
)


def _extract_symbols(content: str) -> list[dict]:
    matches = list(_SYMBOL_PATTERN.finditer(content))
    symbols = []
    for i, m in enumerate(matches):
        name = m.group("fn_name") or m.group("const_name") or m.group("class_name")
        kind = "function" if m.group("fn_name") else ("class" if m.group("class_name") else "const")
        start_line = content[: m.start()].count("\n") + 1
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        end_line = content[:end_pos].count("\n") + 1
        symbols.append({"name": name, "kind": kind, "line_start": start_line, "line_end": end_line, "body": content[m.start():end_pos]})
    return symbols


def index_repo_symbols(repo_id, worktree_path: str) -> int:
    """Extracts symbols per file, then edges from each symbol's own body
    calling another KNOWN symbol's name (a real, if heuristic, call-graph --
    not every call expression, only ones this repo itself defines)."""
    root = Path(worktree_path)
    all_symbols: list[tuple[str, dict]] = []  # (relative_path, symbol_dict)

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _INDEXABLE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        relative = str(path.relative_to(root))
        try:
            content = path.read_text(errors="ignore")
        except Exception:
            continue
        for symbol in _extract_symbols(content):
            all_symbols.append((relative, symbol))

    name_to_id: dict[str, str] = {}
    symbol_count = 0

    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        for relative, symbol in all_symbols:
            cur.execute(
                """
                INSERT INTO symbols (id, repo_id, path, name, kind, line_start, line_end, updated_at)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT ON CONSTRAINT uq_symbol_identity
                DO UPDATE SET kind = EXCLUDED.kind, line_start = EXCLUDED.line_start,
                              line_end = EXCLUDED.line_end, updated_at = now()
                RETURNING id
                """,
                (str(repo_id), relative, symbol["name"], symbol["kind"], symbol["line_start"], symbol["line_end"]),
            )
            symbol_id = cur.fetchone()[0]
            name_to_id[symbol["name"]] = str(symbol_id)
            symbol_count += 1
        conn.commit()

        edge_count = 0
        for relative, symbol in all_symbols:
            from_id = name_to_id[symbol["name"]]
            for other_name, other_id in name_to_id.items():
                if other_name == symbol["name"]:
                    continue
                # A real call, not just a substring hit -- word-boundary
                # match against `name(`, the shape an actual call takes.
                if re.search(rf"\b{re.escape(other_name)}\s*\(", symbol["body"]):
                    cur.execute(
                        """
                        INSERT INTO edges (id, from_symbol_id, to_symbol_id, kind)
                        VALUES (gen_random_uuid(), %s, %s, 'calls')
                        ON CONFLICT ON CONSTRAINT uq_edge_identity DO NOTHING
                        """,
                        (from_id, other_id),
                    )
                    edge_count += 1
        conn.commit()

    logger.info("indexed %d symbols, %d edges for repo %s", symbol_count, edge_count, repo_id)
    return symbol_count


def find_symbols(repo_id, term: str, limit: int = 5) -> list[dict]:
    """Locate a named symbol. Exact matches first, then prefix -- an exact
    identifier is the highest-signal thing a query can contain, so a partial
    match must never outrank one."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, path, kind, line_start
            FROM symbols
            WHERE repo_id = %s AND (name = %s OR name ILIKE %s)
            ORDER BY (name = %s) DESC, length(name)
            LIMIT %s
            """,
            (str(repo_id), term, f"{term}%", term, limit),
        )
        rows = cur.fetchall()
    return [{"name": r[0], "path": r[1], "kind": r[2], "line": r[3]} for r in rows]


def symbol_blast_radius(repo_id, symbol_name: str, max_depth: int = 2) -> list[dict]:
    """Everything that transitively reaches `symbol_name`, with hop distance.

    `find_dependents` answers one hop, which is what the Fix Council asks.
    Retrieval wants the wider question -- "which FILES are implicated if this
    symbol changes" -- and a caller of a caller is still implicated. A
    recursive CTE answers it in one round trip instead of N queries per hop,
    and `cycle` handling is mandatory here rather than defensive: real call
    graphs have cycles, and without it this never terminates.
    """
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH RECURSIVE seed AS (
                SELECT id FROM symbols WHERE repo_id = %s AND name = %s
            ),
            reached(symbol_id, depth) AS (
                SELECT e.from_symbol_id, 1
                FROM edges e JOIN seed ON e.to_symbol_id = seed.id
                UNION
                SELECT e.from_symbol_id, r.depth + 1
                FROM edges e JOIN reached r ON e.to_symbol_id = r.symbol_id
                WHERE r.depth < %s
            )
            SELECT DISTINCT ON (s.id) s.name, s.path, s.line_start, MIN(r.depth) OVER (PARTITION BY s.id)
            FROM reached r JOIN symbols s ON s.id = r.symbol_id
            WHERE s.repo_id = %s AND s.name <> %s
            """,
            # The seed is excluded: real call graphs have cycles, so a symbol
            # routinely "reaches itself" through one, and "changing deleteItem
            # is reachable from deleteItem" is noise in a blast radius.
            (str(repo_id), symbol_name, max_depth, str(repo_id), symbol_name),
        )
        rows = cur.fetchall()
    return [{"name": r[0], "path": r[1], "line": r[2], "depth": int(r[3])} for r in rows]


def find_dependents(repo_id, symbol_name: str) -> list[dict]:
    """"What depends on this symbol, so I know what I might break?" -- the
    one query this graph exists to answer (plan.md §5.3)."""
    with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s_from.name, s_from.path, s_from.line_start, e.kind
            FROM edges e
            JOIN symbols s_to ON s_to.id = e.to_symbol_id
            JOIN symbols s_from ON s_from.id = e.from_symbol_id
            WHERE s_to.repo_id = %s AND s_to.name = %s
            """,
            (str(repo_id), symbol_name),
        )
        rows = cur.fetchall()
    return [{"name": r[0], "path": r[1], "line": r[2], "kind": r[3]} for r in rows]

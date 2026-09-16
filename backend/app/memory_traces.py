"""What this repo has already tried, and how it failed.

Two halves, and the first is what makes the second possible.

**Writing.** Every field of a trace is already computed somewhere in a
council run and then discarded -- the detector has the failing command and
its exit code, the Arbiter has the verdict text, the outcome check has the
mismatch. `record_trace` persists them as rows instead of log lines. Cheap
by construction, and the corpus the other half needs before it is worth
anything at all.

**Reading.** Postgres full-text search over those traces, plus the
identifiers a repo has historically broken on, fed to the retriever's
structural channel.

Two rules that are easy to get wrong and expensive to get wrong:

- **The calling run's own traces are excluded from search.** Otherwise a
  stuck run retrieves its own echo: the trace it wrote thirty seconds ago
  comes back as "what we tried before", and the loop it is stuck in becomes
  the evidence for staying in it.
- **This is history, never scheduling.** Nothing here decides what runs
  next. It only says what already happened, so a council can stop
  re-proposing a patch that was already rejected for a reason still true.

Every function is best-effort and returns an empty result on failure.
Bookkeeping must never fail a run.
"""

from __future__ import annotations

import logging

import psycopg

from app.config import settings
from app.retrieval import _sync_dsn

logger = logging.getLogger("whipguard.memory_traces")

_MAX_DETAIL_CHARS = 2000
_MAX_PATHS = 20
_MAX_RESULTS = 8


def _enabled() -> bool:
    return bool(getattr(settings, "memory_traces_enabled", True))


def record_trace(
    *,
    repo_id,
    outcome: str,
    issue_id=None,
    fix_id=None,
    council_run_id=None,
    category: str = "",
    stage: str = "",
    attempt: int = 1,
    failing_command: str = "",
    exit_code: int | None = None,
    touched_paths: list[str] | None = None,
    detail: str = "",
) -> None:
    """Persist one negative trace. Never raises."""
    if not _enabled() or not repo_id:
        return
    import json

    try:
        with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_traces
                    (id, repo_id, issue_id, fix_id, council_run_id, category, stage, outcome,
                     attempt, failing_command, exit_code, touched_paths, detail, created_at)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    str(repo_id),
                    str(issue_id) if issue_id else None,
                    str(fix_id) if fix_id else None,
                    str(council_run_id) if council_run_id else None,
                    category[:64],
                    stage[:32],
                    outcome[:32],
                    max(int(attempt or 1), 1),
                    # Bounded at WRITE time. Bounding at read time means the
                    # row is already an unbounded blob by the time a caller
                    # has to decide how much of it to inject, and by then the
                    # decision is made with no idea of the cost.
                    (failing_command or "")[:_MAX_DETAIL_CHARS],
                    exit_code,
                    json.dumps([str(path) for path in (touched_paths or [])][:_MAX_PATHS]),
                    (detail or "")[:_MAX_DETAIL_CHARS],
                ),
            )
            conn.commit()
    except Exception as error:  # noqa: BLE001
        logger.warning("could not record trace for repo=%s: %s", repo_id, error)


def structural_terms(repo_id, *, limit: int = 6) -> list[str]:
    """Identifiers and error strings this repo has actually failed on.

    Feeds the retriever's structural channel with terms drawn from HISTORY
    rather than from the current query -- the reason a bug-fixing agent's
    retrieval differs from a general memory system's: the identifier that
    broke the build last week is the highest-signal query term available,
    and it appears nowhere in the text of the new finding.
    """
    if not _enabled() or not repo_id:
        return []
    try:
        from app.hybrid_retrieval import query_identifiers

        with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT failing_command, detail FROM memory_traces
                WHERE repo_id = %s ORDER BY created_at DESC LIMIT 20
                """,
                (str(repo_id),),
            )
            rows = cur.fetchall()

        terms: list[str] = []
        for command, detail in rows:
            for term in query_identifiers(f"{command or ''}\n{detail or ''}"):
                if term not in terms:
                    terms.append(term)
                if len(terms) >= limit:
                    return terms
        return terms
    except Exception as error:  # noqa: BLE001
        logger.warning("structural terms failed for repo=%s: %s", repo_id, error)
        return []


def search_history(repo_id, query: str, *, exclude_run_id=None, limit: int = _MAX_RESULTS) -> list[dict]:
    """Full-text search over this repo's traces.

    `exclude_run_id` is not optional in spirit -- see this module's docstring
    for why a run must never retrieve its own traces.
    """
    if not _enabled() or not repo_id or not (query or "").strip():
        return []
    try:
        with psycopg.connect(_sync_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT created_at, category, stage, outcome, failing_command, exit_code, detail,
                       ts_rank(
                         to_tsvector('english', coalesce(failing_command,'') || ' ' || coalesce(detail,'')),
                         websearch_to_tsquery('english', %s)
                       ) AS rank
                FROM memory_traces
                WHERE repo_id = %s
                  AND (%s::uuid IS NULL OR council_run_id IS DISTINCT FROM %s::uuid)
                  AND to_tsvector('english', coalesce(failing_command,'') || ' ' || coalesce(detail,''))
                      @@ websearch_to_tsquery('english', %s)
                ORDER BY rank DESC, created_at DESC
                LIMIT %s
                """,
                (
                    query, str(repo_id),
                    str(exclude_run_id) if exclude_run_id else None,
                    str(exclude_run_id) if exclude_run_id else None,
                    query, limit,
                ),
            )
            rows = cur.fetchall()
        return [
            {
                "when": row[0].isoformat() if row[0] else "",
                "category": row[1], "stage": row[2], "outcome": row[3],
                "command": row[4], "exit_code": row[5], "detail": row[6],
            }
            for row in rows
        ]
    except Exception as error:  # noqa: BLE001 - history is optional context, never a gate
        logger.warning("history search failed for repo=%s: %s", repo_id, error)
        return []


def render_history(query: str, rows: list[dict]) -> str:
    if not rows:
        return (
            f"Nothing in this repo's history matches '{query}'. It may not have been tried "
            "before, or it may have been described differently at the time."
        )
    lines = [f"WHAT THIS REPO HAS ALREADY TRIED, matching '{query}' (history, not a plan):"]
    for row in rows:
        when = str(row.get("when") or "")[:19].replace("T", " ")
        head = f"- [{when}] {row.get('outcome')} during {row.get('stage') or 'a run'}"
        if row.get("category"):
            head += f" ({row['category']})"
        lines.append(head)
        if row.get("command"):
            exit_code = row.get("exit_code")
            lines.append(f"    ran: {row['command']}" + (f" -> exit {exit_code}" if exit_code is not None else ""))
        if row.get("detail"):
            lines.append(f"    {str(row['detail'])[:300]}")
    return "\n".join(lines)

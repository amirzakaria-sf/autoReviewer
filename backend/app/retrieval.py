"""Vector similarity search over the two embedding spaces in app/models.py
(CodeChunk, IssueEmbedding), backing plan.md §10.3's RetrievalNode alongside
(not instead of) the one-hop static import scan.

Uses a POOLED sync psycopg connection (app/sync_db.py), not the app's async
SQLAlchemy session --
deliberately: this module's callers are graph NODES (sync functions, run via
asyncio.to_thread from the async wrappers in bug_council.py/fix_council.py),
which never carry an AsyncSession into the graph at all (every other DB write
in this codebase happens in the async wrapper OUTSIDE the graph, not inside a
node). Threading an AsyncSession into a sync node would need its own event
loop inside a worker thread; a pooled sync connection here is far simpler and
matches how the sandbox/subprocess calls already work in these nodes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app import sync_db
from app.config import settings
from app.embeddings import chunk_file, embed_text

logger = logging.getLogger("whipguard.retrieval")

_INDEXABLE_SUFFIXES = (".js", ".ts", ".jsx", ".tsx", ".py")
_SKIP_DIRS = {"node_modules", ".git", "test-results", "playwright-report"}


def _sync_dsn() -> str:
    # settings.database_url is the asyncpg form (postgresql+asyncpg://...);
    # psycopg wants the plain postgresql:// scheme.
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _connect():
    """pgvector adapters are registered once per pooled connection in
    app/sync_db.py's configure hook, so this is now just the pool."""
    return sync_db.connection()


def index_repo_files(repo_id, worktree_path: str) -> int:
    """(Re-)embeds every indexable file in the worktree, upserting by
    (repo_id, file_path, symbol_name) identity -- a re-index after a push
    updates existing chunks' embeddings rather than accumulating duplicates."""
    root = Path(worktree_path)
    count = 0

    with _connect() as conn, conn.cursor() as cur:
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
            if not content.strip():
                continue

            for chunk in chunk_file(relative, content):
                try:
                    embedding = embed_text(chunk["content"])
                except Exception:
                    logger.exception("embedding failed for %s (%s)", relative, chunk["symbol_name"])
                    continue

                cur.execute(
                    """
                    INSERT INTO code_chunks
                        (id, repo_id, file_path, symbol_name, content, start_line, end_line, embedding, updated_at)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT ON CONSTRAINT uq_code_chunk_identity
                    DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding,
                                  start_line = EXCLUDED.start_line, end_line = EXCLUDED.end_line,
                                  updated_at = now()
                    """,
                    (
                        str(repo_id), relative, chunk["symbol_name"], chunk["content"],
                        chunk.get("start_line", 1), chunk.get("end_line", 1), embedding,
                    ),
                )
                count += 1
        conn.commit()

    logger.info("indexed %d code chunks for repo %s", count, repo_id)
    return count


def similar_code_chunks(repo_id, query_text: str, k: int = 5) -> list[dict]:
    """Cosine distance (pgvector's `<=>` operator) nearest-neighbor search,
    scoped to one repo. Returns [] on any failure rather than raising -- a
    retrieval miss degrades the Fix Council back to the one-hop static scan
    alone, never crashes the run over an unrelated API hiccup."""
    try:
        query_embedding = embed_text(query_text)
    except Exception:
        logger.exception("query embedding failed; retrieval falls back to the static scan alone")
        return []

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_path, symbol_name, content, start_line, end_line,
                       embedding <=> %s::vector AS distance
                FROM code_chunks
                WHERE repo_id = %s
                ORDER BY distance
                LIMIT %s
                """,
                (query_embedding, str(repo_id), k),
            )
            rows = cur.fetchall()
        return [
            {
                "file_path": r[0], "symbol_name": r[1], "content": r[2],
                "start_line": int(r[3] or 1), "end_line": int(r[4] or 1), "distance": float(r[5]),
            }
            for r in rows
        ]
    except Exception:
        logger.exception("similarity search failed; retrieval falls back to the static scan alone")
        return []


def find_similar_past_issue(repo_id, issue_text: str, distance_threshold: float = 0.15) -> dict | None:
    """"Have we raised this before" (plan.md §5.2) -- returns the closest past
    issue within `distance_threshold` cosine distance, or None. This
    ANNOTATES a new finding; it never suppresses it -- a repeat detection of a
    real, still-unfixed bug is legitimate, and deciding what to do about a
    repeat is a human/calibration-loop judgment, not a silent auto-dedupe."""
    try:
        query_embedding = embed_text(issue_text)
    except Exception:
        logger.exception("issue-dedupe embedding failed")
        return None

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.id, i.title, i.status, ie.embedding <=> %s::vector AS distance
                FROM issue_embeddings ie
                JOIN issues i ON i.id = ie.issue_id
                WHERE i.repo_id = %s
                ORDER BY distance
                LIMIT 1
                """,
                (query_embedding, str(repo_id)),
            )
            row = cur.fetchone()
    except Exception:
        logger.exception("issue-dedupe search failed")
        return None

    if row is None or row[3] > distance_threshold:
        return None
    return {"issue_id": str(row[0]), "title": row[1], "status": row[2], "distance": float(row[3])}


def store_issue_embedding(issue_id, text: str) -> None:
    try:
        embedding = embed_text(text)
    except Exception:
        logger.exception("could not store issue embedding for %s", issue_id)
        return
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO issue_embeddings (id, issue_id, text, embedding, created_at)
                VALUES (gen_random_uuid(), %s, %s, %s, now())
                ON CONFLICT (issue_id) DO UPDATE SET text = EXCLUDED.text, embedding = EXCLUDED.embedding
                """,
                (str(issue_id), text, embedding),
            )
            conn.commit()
    except Exception:
        logger.exception("could not persist issue embedding for %s", issue_id)

"""BM25 over symbol-boundary chunks -- the lexical retrieval channel.

This codebase had exactly one retrieval channel before this module: pgvector
cosine similarity (app/retrieval.py). Dense embeddings are good at "code that
means roughly this" and bad at the query a bug report actually carries --
an exact identifier, a file path, a literal error string. Those are lexical
questions, and a dense index answers them by accident at best.

Two design choices worth stating, both load-bearing:

**The index stores term frequencies, never chunk text.** Text is re-read from
the worktree when a chunk is actually selected. That keeps the index small
and, more importantly, means it can never serve a stale copy of a file that
has since been rewritten by a fix -- a retrieval index that lies about
current file contents is worse than no index, because the model has no way
to tell.

**Ranking returns metadata only.** Fusion (app/hybrid_retrieval.py) asks for
tens of candidates and keeps a handful. Reading fifty candidates' source off
disk to discard forty-four of them is most of the cost of a retrieval, so
`rank_chunk_metas` hands back just enough to fuse on and the survivors are
read afterwards.

The index lives OUTSIDE the worktree, in its own cache volume
(`settings.retrieval_cache_dir`), for two reasons: writing it inside the
worktree would show up as an untracked file in every `git status` the fix
pipeline runs and eventually in a diff, and the web-facing process mounts the
workspace READ-ONLY, so a cache under it could never be written there at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger("whipguard.lexical_index")

_CHUNK_INDEX_VERSION = 1
_INDEXABLE_SUFFIXES = {".js", ".ts", ".jsx", ".tsx", ".py", ".md", ".json", ".css", ".html"}
_SKIP_DIRS = {"node_modules", ".git", "test-results", "playwright-report", "dist", "build", ".next"}
# Generated files. A lockfile is enormous, mentions every package name in the
# dependency tree, and is never the answer to a question about behaviour --
# indexing it meant it out-ranked real source on almost every query (found by
# actually ranking against the fixture repo, where it took the top three hits
# for "AKIA secret key scan").
_SKIP_FILENAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "composer.lock", "poetry.lock"}
_SKIP_FILE_PATTERNS = (".min.js", ".min.css", ".map", ".bundle.js")
_MAX_FILE_BYTES = 400_000
_MAX_CHUNK_LINES = 120
_FALLBACK_WINDOW_LINES = 60

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Standard BM25 defaults: k1 controls term-frequency saturation, b controls
# length normalisation.
_BM25_K1 = 1.2
_BM25_B = 0.75


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    symbol: str
    text: str

    def render(self) -> str:
        header = f"{self.path}:{self.start_line}-{self.end_line}"
        if self.symbol:
            header += f"  ({self.symbol})"
        return f"### {header}\n```\n{self.text}\n```"


def _split_identifier(token: str) -> list[str]:
    """`getUserProfile` -> `[getuserprofile, get, user, profile]`.

    Without this, a query for "user profile" scores nothing against a
    codebase that spells it `getUserProfile` -- which is how identifiers are
    actually written. Both the whole token and its parts are kept so an
    exact-identifier query still outranks a prose one.
    """
    lowered = token.lower()
    parts = re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", token).replace("-", "_").lower())
    return [lowered] if len(parts) <= 1 else [lowered, *parts]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(text or ""):
        tokens.extend(_split_identifier(match))
    return tokens


def _iter_indexable_files(root: Path):
    """Walk with directory pruning. `rglob` descends into node_modules before
    filtering it out, which on a real repo costs more than the entire rest of
    the index build."""
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")]
        for filename in filenames:
            if filename in _SKIP_FILENAMES or filename.endswith(_SKIP_FILE_PATTERNS):
                continue
            if Path(filename).suffix.lower() in _INDEXABLE_SUFFIXES:
                yield Path(dirpath) / filename


def _chunks_for_file(root: Path, absolute: Path) -> list[Chunk]:
    """Split at real symbol boundaries where they exist, fixed windows where
    they don't -- a JSON file or a README has no functions but still answers
    questions."""
    try:
        if absolute.stat().st_size > _MAX_FILE_BYTES:
            return []
        text = absolute.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    if not lines:
        return []
    relative = absolute.relative_to(root).as_posix()

    from app.graph_index import _extract_symbols

    chunks: list[Chunk] = []
    if absolute.suffix.lower() in {".js", ".ts", ".jsx", ".tsx"}:
        for symbol in _extract_symbols(text):
            start = int(symbol["line_start"])
            end = min(int(symbol["line_end"]), start + _MAX_CHUNK_LINES)
            body = "\n".join(lines[start - 1 : end]).strip()
            if body:
                chunks.append(Chunk(relative, start, end, str(symbol["name"]), body))
    if chunks:
        return chunks

    for start in range(0, len(lines), _FALLBACK_WINDOW_LINES):
        window = "\n".join(lines[start : start + _FALLBACK_WINDOW_LINES]).strip()
        if window:
            chunks.append(Chunk(relative, start + 1, min(start + _FALLBACK_WINDOW_LINES, len(lines)), "", window))
    return chunks


def _cache_path(worktree_path: str | Path) -> Path:
    digest = hashlib.sha256(str(Path(worktree_path).resolve()).encode("utf-8")).hexdigest()[:32]
    return Path(settings.retrieval_cache_dir) / f"{digest}.json"


def _fingerprint(root: Path) -> str:
    """Cheap staleness check: every indexable file's path, size and mtime.
    A fix that rewrites one file changes this, so the next query rebuilds."""
    hasher = hashlib.sha256()
    for absolute in sorted(_iter_indexable_files(root)):
        try:
            stat = absolute.stat()
        except OSError:
            continue
        hasher.update(f"{absolute}:{stat.st_size}:{int(stat.st_mtime)}".encode())
    return hasher.hexdigest()


def build_chunk_index(worktree_path: str | Path) -> dict:
    root = Path(worktree_path).expanduser().resolve()
    postings: dict[str, dict[str, int]] = {}
    chunk_meta: list[dict] = []
    lengths: list[int] = []

    for absolute in _iter_indexable_files(root):
        for chunk in _chunks_for_file(root, absolute):
            # Path and symbol name join the chunk's own token stream so a
            # query naming a file matches even when the body never repeats
            # the file's name.
            terms = tokenize(chunk.text) + tokenize(chunk.path) + tokenize(chunk.symbol)
            if not terms:
                continue
            counts = Counter(terms)
            chunk_id = len(chunk_meta)
            for term, count in counts.items():
                postings.setdefault(term, {})[str(chunk_id)] = count
            chunk_meta.append(
                {
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "symbol": chunk.symbol,
                },
            )
            lengths.append(sum(counts.values()))

    payload = {
        "version": _CHUNK_INDEX_VERSION,
        "fingerprint": _fingerprint(root),
        "chunks": chunk_meta,
        "lengths": lengths,
        "postings": postings,
        "average_length": (sum(lengths) / len(lengths)) if lengths else 0.0,
    }
    try:
        target = _cache_path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as error:
        logger.warning("could not write chunk index for %s: %s", root, error)
    return payload


def ensure_chunk_index_fresh(worktree_path: str | Path) -> dict:
    root = Path(worktree_path).expanduser().resolve()
    try:
        cached = json.loads(_cache_path(root).read_text(encoding="utf-8"))
        if cached.get("version") == _CHUNK_INDEX_VERSION and cached.get("fingerprint") == _fingerprint(root):
            return cached
    except (OSError, ValueError):
        pass
    return build_chunk_index(root)


def rank_chunk_metas(worktree_path: str | Path, query: str, *, limit: int = 5) -> list[tuple[dict, float]]:
    """BM25-rank chunks, returning `(metadata, score)` highest first."""
    terms = tokenize(query)
    if not terms:
        return []

    index = ensure_chunk_index_fresh(worktree_path)
    chunks = index.get("chunks") or []
    postings = index.get("postings") or {}
    lengths = index.get("lengths") or []
    if not chunks or not postings:
        return []

    total_chunks = len(chunks)
    average_length = float(index.get("average_length") or 0.0) or 1.0
    scores: dict[int, float] = {}

    for term in set(terms):
        term_postings = postings.get(term)
        if not term_postings:
            continue
        document_frequency = len(term_postings)
        idf = math.log(1 + (total_chunks - document_frequency + 0.5) / (document_frequency + 0.5))
        for chunk_key, frequency in term_postings.items():
            chunk_id = int(chunk_key)
            if chunk_id >= total_chunks:
                continue
            length = lengths[chunk_id] if chunk_id < len(lengths) else average_length
            denominator = frequency + _BM25_K1 * (1 - _BM25_B + _BM25_B * (length / average_length))
            scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * (frequency * (_BM25_K1 + 1)) / denominator

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[: max(limit, 1)]
    return [(chunks[chunk_id], score) for chunk_id, score in ranked if chunk_id < total_chunks]


def all_chunk_metas(worktree_path: str | Path) -> list[dict]:
    return list(ensure_chunk_index_fresh(worktree_path).get("chunks") or [])


def read_file_slice(root: Path, relative_path: str, *, start_line: int, end_line: int) -> str:
    try:
        absolute = (root / relative_path).resolve()
        # A traversal-shaped path in an index built from untrusted repo
        # contents must not read outside the worktree.
        absolute.relative_to(root)
        lines = absolute.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return ""
    return "\n".join(lines[max(start_line - 1, 0) : end_line]).strip()


def chunk_from_meta(worktree_path: str | Path, meta: dict) -> Chunk | None:
    """Re-read one ranked chunk off disk. None when the file has since moved."""
    root = Path(worktree_path).expanduser().resolve()
    start_line = int(meta.get("start_line") or 1)
    end_line = int(meta.get("end_line") or 1)
    text = read_file_slice(root, str(meta.get("path") or ""), start_line=start_line, end_line=end_line)
    if not text:
        return None
    return Chunk(str(meta.get("path") or ""), start_line, end_line, str(meta.get("symbol") or ""), text)


def search_chunks(worktree_path: str | Path, query: str, *, limit: int = 5) -> list[Chunk]:
    results: list[Chunk] = []
    for meta, _score in rank_chunk_metas(worktree_path, query, limit=limit):
        chunk = chunk_from_meta(worktree_path, meta)
        if chunk:
            results.append(chunk)
    return results

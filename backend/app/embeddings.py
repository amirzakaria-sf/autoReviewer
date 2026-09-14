"""pgvector-backed retrieval (plan.md §5.2): two embedding spaces (a third,
documentation chunks, waits on the `documentation` category existing).

- Code chunks: what RetrievalNode adds ON TOP OF the one-hop static import
  scan (plan.md §16 already named "the one-hop scan is enough for THIS demo's
  retrieval need" as a deliberate Tier-0 cut -- this is that cut being
  reversed now that the scope calls for the real target architecture).
- Past issue+fix text: dedupe ("have we raised this before") and calibration
  (joined against CalibrationEvent once Phase 9 exists).

pgvector, in the same Postgres, not a separate vector database -- plan.md
§5.2's own reasoning: one fewer service, one fewer network hop, and at a
single connected repo's corpus size the extra machinery a dedicated vector DB
buys doesn't pay for itself yet.
"""

from __future__ import annotations

import re

from openai import AzureOpenAI

from app.config import settings

EMBEDDING_DIMENSIONS = 1536


def _client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=settings.azure_api_endpoint,
        api_key=settings.azure_api_key,
        api_version=settings.azure_openai_api_version,
    )


def embed_text(text: str) -> list[float]:
    client = _client()
    response = client.embeddings.create(model=settings.azure_embedding_deployment, input=text[:8000])
    return response.data[0].embedding


# A real AST parser (tree-sitter, etc.) is the honest long-term answer;
# regex-based boundary detection is what actually runs today. Good enough for
# the fixture repo's plain JS files -- named as a real limitation, not hidden.
_JS_BOUNDARY = re.compile(
    r"^(?:function\s+\w+|const\s+\w+\s*=\s*(?:\([^)]*\)|[\w]+)\s*=>|class\s+\w+)",
    re.MULTILINE,
)


def chunk_file(relative_path: str, content: str) -> list[dict]:
    """Splits at top-level function/const-arrow/class boundaries. A file with
    no such boundary (or a non-JS file) is returned as one whole-file chunk."""
    boundaries = [m.start() for m in _JS_BOUNDARY.finditer(content)]
    if not boundaries:
        return [{"symbol_name": relative_path, "content": content}]

    chunks = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        chunk_text = content[start:end].strip()
        if not chunk_text:
            continue
        first_line = chunk_text.splitlines()[0][:80]
        chunks.append({"symbol_name": f"{relative_path}:{first_line}", "content": chunk_text})
    return chunks or [{"symbol_name": relative_path, "content": content}]

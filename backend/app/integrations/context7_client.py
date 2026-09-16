"""Context7 library-docs lookup for the Fix Council's own ReAct loop --
gives the patch-generation agent real, current library documentation instead
of relying on the model's training data, which is exactly the kind of gap
that produces a plausible-looking but subtly-wrong fix for a fast-moving API.

Public REST API, unauthenticated, verified directly against the real
endpoints before being trusted here (plan.md §9.8's "a tool integration with
zero errors is not proof it works" lesson, applied on the way in this time
rather than discovered the hard way after a fix silently used it wrong):
  GET https://context7.com/api/v1/search?query=<term>
      -> {"results": [{"id": "/org/project", "title": ..., "trustScore": ...}]}
  GET https://context7.com/api/v1/{id}?type=txt&topic=<topic>&tokens=<n>
      -> plain-text/markdown documentation content
"""

from __future__ import annotations

import httpx

API_BASE = "https://context7.com/api/v1"


def search_library(query: str) -> list[dict]:
    resp = httpx.get(f"{API_BASE}/search", params={"query": query}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_docs(library_id: str, topic: str = "", tokens: int = 2000) -> str:
    library_id = library_id.lstrip("/")
    params = {"type": "txt", "tokens": tokens}
    if topic:
        params["topic"] = topic
    resp = httpx.get(f"{API_BASE}/{library_id}", params=params, timeout=20)
    resp.raise_for_status()
    return resp.text


def lookup(query: str, topic: str = "", tokens: int = 2000) -> str:
    """One-shot: resolve the best-matching library for `query`, then fetch its
    docs. Returns a human-readable "not found" string rather than raising, so
    a ReAct loop's tool-result handling stays uniform (a string back to the
    model either way, never an exception it has to special-case)."""
    # Library documentation for a fixed (library, topic) is the same bytes
    # every time, and this sits inside a ReAct loop that can ask for the same
    # docs on several consecutive ticks of the same run. Reusing the response
    # cache costs one indexed lookup and saves two network round-trips to a
    # third-party service that is not on our own availability budget.
    from app import llm_cache

    cache_key = llm_cache.cache_key_for(role="context7", deployment="rest", prefix=query, suffix=f"{topic}:{tokens}")
    cached = llm_cache.get(cache_key)
    if cached is not None:
        return cached

    results = search_library(query)
    if not results:
        return f"No Context7 library found matching {query!r}."

    # The search API's own ordering IS its relevance ranking -- re-sorting by
    # trustScore alone threw away that ranking and picked an unrelated
    # high-trust library over the actually-relevant top hit, found by
    # actually running a real query and reading the (wrong) result.
    best = results[0]
    docs = get_docs(best["id"], topic=topic, tokens=tokens)
    rendered = f"# {best.get('title', best['id'])} ({best['id']})\n\n{docs}"
    llm_cache.put(cache_key, rendered, role="context7", deployment="rest")
    return rendered

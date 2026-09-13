"""Stable prefix / volatile suffix contract (plan.md §9).

A byte that repeats across calls belongs in a stable, cached prefix. A byte that
changes this call belongs in the volatile suffix. Mixing the two forfeits the
provider's prompt cache for every remaining call in the attempt.
"""

from __future__ import annotations

import hashlib

CACHE_FLOOR_TOKENS = 1024
# Rough chars-per-token heuristic for a padding budget; good enough to clear the
# floor without a real tokenizer dependency in this hackathon build.
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def build_prefix(role: str, workspace_map: str, category_rules: str) -> str:
    """Assemble the stable prefix for one jury role.

    Byte-identical for identical (role, workspace_map, category_rules) inputs —
    callers must pass through the same workspace_map/category_rules on a retry
    rather than recomputing them, or the retry contract in plan.md §9.6 breaks.
    """
    return (
        f"[persona]\n{role}\n\n"
        f"[workspace_map]\n{workspace_map}\n\n"
        f"[category_rules]\n{category_rules}\n"
    )


def pad_to_cache_floor(prefix: str, floor_tokens: int = CACHE_FLOOR_TOKENS) -> str:
    """Pad a short prefix up to the provider's cache floor.

    Pads by repeating the prefix's OWN static content — never per-attempt content —
    so the padding itself stays part of the stable, cacheable bytes. If the prefix
    is empty there is nothing static to repeat, so it is returned unpadded rather
    than padding with placeholder text that would not represent real cached bytes.
    """
    if not prefix:
        return prefix

    current_tokens = _estimate_tokens(prefix)
    if current_tokens >= floor_tokens:
        return prefix

    padded = prefix
    marker = "\n\n[padding: repeated stable content to clear the provider cache floor]\n"
    while _estimate_tokens(padded) < floor_tokens:
        padded += marker + prefix

    return padded


def partition_key(repo: str, role: str, prefix: str) -> str:
    """Cache partition key: {repo}:{role}:{sha256(prefix)[:16]}.

    Two parallel attempts with the same persona and rules share one partition for
    free; a rules-version change produces a fresh key instead of colliding with a
    stale one.
    """
    digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]
    return f"{repo}:{role}:{digest}"


def build_volatile_suffix(
    task_instruction: str,
    prior_attempt_rejection: str | None = None,
    human_steer: str | None = None,
) -> str:
    """Everything that changes this call and nothing else (plan.md §9.6)."""
    parts = [f"[task]\n{task_instruction}"]
    if prior_attempt_rejection:
        parts.append(
            "[retry]\nThis is attempt 2. The prior attempt was rejected for this "
            f"reason: {prior_attempt_rejection}\nFix exactly this. Do not undo what "
            "was already correct."
        )
    if human_steer:
        parts.append(f"[human_steer]\n{human_steer}")
    return "\n\n".join(parts)

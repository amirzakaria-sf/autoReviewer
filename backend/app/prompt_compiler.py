"""Priority-weighted prompt assembly under one hard token budget.

Before this module every injection point in this codebase carried its own
hardcoded character cap -- `evidence[:2000]`, `stdout[-2000:]`,
`detail[:300]` -- each chosen in isolation, none aware of the others, and all
measuring CHARACTERS rather than tokens. The failure mode that produces is
the "donut": low-value bulk (a whole-file retrieval hit, an old council
transcript) survives intact while the one thing that mattered for this
particular call gets cut, because the two were capped independently and
never compared against each other.

This replaces that with a single budget and an explicit priority ordering.
MANDATORY chunks are always kept -- dropping the system prompt or the actual
question to satisfy an arithmetic limit is worse than the overrun.
Everything else is admitted in priority order until the budget runs out; the
first chunk that does not fit is truncated to whatever room remains, if that
is still a useful amount, and the rest are dropped.

Truncation is reported, never silent: `CompiledPrompt.dropped` and
`.truncated` name exactly what was cut, so a council run that degraded
because its budget was tight says so in its own transcript instead of
quietly getting worse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from functools import lru_cache

from app.config import settings

logger = logging.getLogger("whipguard.prompt_compiler")

# Headroom so the model's own reply still fits under the deployment's context
# window after our input is counted.
_RESPONSE_HEADROOM_TOKENS = 4000
# Truncating a chunk below this is worse than dropping it -- a 40-token
# fragment of a code chunk costs budget and teaches the model nothing.
_MIN_USEFUL_TRUNCATION_TOKENS = 200
_TRUNCATION_MARKER = "\n...[truncated to fit token budget]"

# o200k_base is what the gpt-5.x deployments this app calls actually
# tokenize with. cl100k_base (the older default) is materially less dense on
# code and JSON, so counting with it UNDER-reports a code-heavy prompt: the
# compiler would believe it had room it did not have, and the model would
# see more tokens than were admitted.
_DEFAULT_ENCODING = "o200k_base"


class Priority(IntEnum):
    """Lower value wins. The ordering is the whole point of this module."""

    MANDATORY = 1  # System prompt, the finding under review, this-call instructions
    HIGH = 2       # Live state: the diff, the failing assertion, the blast radius
    MEDIUM = 3     # Targeted lookups: retrieved code, located symbols
    LOW = 4        # Bulk background: repo map, library usage, history


@dataclass
class ContextChunk:
    priority: Priority
    label: str
    content: str

    def rendered(self) -> str:
        return f"--- {self.label} ---\n{self.content}"


@dataclass
class CompiledPrompt:
    text: str
    total_tokens: int
    budget: int
    kept: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    # (label, rendered_text) per admitted block, in admission order. `text`
    # is just these joined; the pairs are kept because a caller splitting the
    # result across messages (a cacheable stable prefix plus a volatile
    # suffix, plan.md §11.3) cannot recover the boundaries from the joined
    # string afterwards.
    blocks: list[tuple[str, str]] = field(default_factory=list)

    def as_payload(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "budget": self.budget,
            "kept": self.kept,
            "truncated": self.truncated,
            "dropped": self.dropped,
        }


@lru_cache(maxsize=2)
def _encoder_named(encoding_name: str):
    """Load one BPE table exactly once. Constructing it per chunk re-reads
    the whole encoding table and dominates the cost of compiling a prompt."""
    import tiktoken

    return tiktoken.get_encoding(encoding_name)


def _encoder():
    configured = str(getattr(settings, "prompt_tokenizer_encoding", "") or _DEFAULT_ENCODING)
    try:
        return _encoder_named(configured)
    except Exception as error:  # noqa: BLE001 - an unknown table must not break counting
        logger.warning("encoding %r unavailable (%s); falling back to cl100k_base", configured, error)
        return _encoder_named("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_encoder().encode(text, disallowed_special=()))
    except Exception as error:  # noqa: BLE001 - never fail a run over a token estimate
        logger.warning("token count failed (%s); approximating", error)
        return max(1, len(text) // 4)  # ~4 chars/token, only if tiktoken breaks


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate so the result INCLUDING the marker fits. Appending the marker
    after truncating to the limit silently overshoots by its own cost."""
    if max_tokens <= 0:
        return ""
    try:
        encoder = _encoder()
        tokens = encoder.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text
        marker_tokens = len(encoder.encode(_TRUNCATION_MARKER, disallowed_special=()))
        return encoder.decode(tokens[: max(0, max_tokens - marker_tokens)]) + _TRUNCATION_MARKER
    except Exception:  # noqa: BLE001
        return text[: max(0, (max_tokens * 4) - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER


def default_budget() -> int:
    configured = int(getattr(settings, "prompt_budget_tokens", 32000) or 32000)
    return max(1000, configured - _RESPONSE_HEADROOM_TOKENS)


def compile_context(chunks, budget: int | None = None) -> CompiledPrompt:
    """Assemble `chunks` honouring priority order and the token budget."""
    effective_budget = budget if budget is not None else default_budget()

    usable = [chunk for chunk in chunks if chunk and chunk.content and chunk.content.strip()]
    mandatory = [chunk for chunk in usable if chunk.priority == Priority.MANDATORY]
    optional = sorted(
        (chunk for chunk in usable if chunk.priority != Priority.MANDATORY),
        key=lambda chunk: chunk.priority,
    )

    selected: list[str] = []
    result = CompiledPrompt(text="", total_tokens=0, budget=effective_budget)

    for chunk in mandatory:
        rendered = chunk.rendered()
        selected.append(rendered)
        result.blocks.append((chunk.label, rendered))
        result.total_tokens += count_tokens(rendered)
        result.kept.append(chunk.label)

    if result.total_tokens > effective_budget:
        logger.warning(
            "mandatory content is %s tokens, over the %s budget; including it anyway",
            result.total_tokens, effective_budget,
        )

    for chunk in optional:
        rendered = chunk.rendered()
        chunk_tokens = count_tokens(rendered)
        remaining = effective_budget - result.total_tokens

        if chunk_tokens <= remaining:
            selected.append(rendered)
            result.blocks.append((chunk.label, rendered))
            result.total_tokens += chunk_tokens
            result.kept.append(chunk.label)
        elif remaining >= _MIN_USEFUL_TRUNCATION_TOKENS:
            trimmed = _truncate_to_tokens(rendered, remaining)
            selected.append(trimmed)
            result.blocks.append((chunk.label, trimmed))
            result.total_tokens += count_tokens(trimmed)
            result.truncated.append(chunk.label)
        else:
            result.dropped.append(chunk.label)

    result.text = "\n\n".join(selected)
    return result


def fit_text(text: str, max_tokens: int) -> str:
    """Budget a single blob. Drop-in replacement for the ad-hoc `text[:N]`
    character slices scattered through the detectors and councils."""
    if not text:
        return ""
    if count_tokens(text) <= max_tokens:
        return text
    return _truncate_to_tokens(text, max_tokens)

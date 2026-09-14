"""Role-routed Azure OpenAI client (plan.md §10).

Detector has no entry here — it is mechanical (a Playwright run), not a model call.
"""

from __future__ import annotations

import json

from openai import AzureOpenAI
from pydantic import BaseModel

from app.config import settings


class ArbiterFactor(BaseModel):
    factor: str
    weight: int
    note: str


class ArbiterVerdict(BaseModel):
    score: int
    factors: list[ArbiterFactor]
    verdict: str
    needs_clarification: dict | None = None


class JurorOpinion(BaseModel):
    """A structured jury-role response: the free-text argument PLUS a
    confidence score, so a meta-council can measure disagreement mechanically
    (plan.md §10.2: |Skeptic_confidence - Corroborator_confidence| >
    disagreement_threshold) instead of trying to infer it from two paragraphs
    of prose."""

    confidence: int
    transcript: str


def _client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=settings.azure_api_endpoint,
        api_key=settings.azure_api_key,
        api_version=settings.azure_openai_api_version,
    )


def _chat(deployment: str, prefix: str, suffix: str) -> str:
    client = _client()
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": prefix},
            {"role": "user", "content": suffix},
        ],
    )
    return response.choices[0].message.content or ""


_JUROR_SCHEMA_INSTRUCTION = (
    "\n\nRespond with ONLY a JSON object matching this shape, no prose outside it:\n"
    '{"confidence": <0-100 int, how confident you are in YOUR OWN position>, "transcript": <your argument, several sentences>}'
)


def _call_juror(deployment: str, prefix: str, suffix: str) -> JurorOpinion:
    raw = _chat(deployment, prefix + _JUROR_SCHEMA_INSTRUCTION, suffix)
    data = json.loads(raw)
    return JurorOpinion.model_validate(data)


def call_skeptic(prefix: str, suffix: str) -> str:
    """Legacy plain-text form, kept for callers that don't need a confidence
    score (e.g. the retry-contract test)."""
    return _chat(settings.azure_fast_deployment, prefix, suffix)


def call_skeptic_opinion(prefix: str, suffix: str) -> JurorOpinion:
    return _call_juror(settings.azure_fast_deployment, prefix, suffix)


def call_corroborator_opinion(prefix: str, suffix: str) -> JurorOpinion:
    """Corroborator: mid-tier model (needs to reason about code relevance and
    read mechanical evidence carefully — plan.md §12's routing table)."""
    return _call_juror(settings.azure_worker_deployment, prefix, suffix)


def call_verifier(prefix: str, suffix: str) -> str:
    return _chat(settings.azure_worker_deployment, prefix, suffix)


def call_fix_skeptic_opinion(prefix: str, suffix: str) -> JurorOpinion:
    """Fix Council's "Skeptic-for-regressions" (plan.md §10.3) — argues the
    patch could break something else, run in parallel with the mechanical
    VerifierNode rather than after it."""
    return _call_juror(settings.azure_fast_deployment, prefix, suffix)


def call_patch_worker(prefix: str, suffix: str) -> str:
    return _chat(settings.azure_worker_deployment, prefix, suffix)


def call_meta_auditor(prefix: str, suffix: str) -> ArbiterVerdict:
    """The meta-council's own Arbiter call, escalated to on jury disagreement
    (plan.md §10.2) — routed to the strongest deployment, same as the regular
    Arbiter, since this is spending MORE compute on a close call, not less."""
    return call_arbiter(prefix, suffix)


def call_arbiter(prefix: str, suffix: str) -> ArbiterVerdict:
    """Structured-output score. Asks for strict JSON and validates it — no free-text
    parsing of a model's prose, per plan.md §11.9."""
    schema_instruction = (
        "\n\nRespond with ONLY a JSON object matching this shape, no prose outside it:\n"
        '{"score": <0-100 int>, "factors": [{"factor": str, "weight": int, "note": str}, ...], '
        '"verdict": <one-sentence str>, "needs_clarification": null or '
        '{"question": str, "options": [str, ...]}}\n'
        "The factors' weights must sum to exactly `score`.\n\n"
        "Use needs_clarification INSTEAD of forcing a score only when the ambiguity is about "
        "missing PRODUCT INTENT a human would have to supply (e.g. a hardcoded value that could "
        "be an intentional constant or could be a bug depending on a decision nobody documented; "
        "a behavior that might be deliberate for this specific deployment) -- never when the "
        "ambiguity is just missing EVIDENCE you could reason about yourself from what's given. "
        "Forcing a confident number when the missing piece is a human's own undocumented intent "
        "manufactures false confidence; that is worse than asking."
    )
    raw = _chat(settings.azure_planner_deployment, prefix + schema_instruction, suffix)
    data = json.loads(raw)
    return ArbiterVerdict.model_validate(data)

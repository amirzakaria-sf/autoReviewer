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


def call_skeptic(prefix: str, suffix: str) -> str:
    return _chat(settings.azure_fast_deployment, prefix, suffix)


def call_verifier(prefix: str, suffix: str) -> str:
    return _chat(settings.azure_worker_deployment, prefix, suffix)


def call_patch_worker(prefix: str, suffix: str) -> str:
    return _chat(settings.azure_worker_deployment, prefix, suffix)


def call_arbiter(prefix: str, suffix: str) -> ArbiterVerdict:
    """Structured-output score. Asks for strict JSON and validates it — no free-text
    parsing of a model's prose, per plan.md §11.9."""
    schema_instruction = (
        "\n\nRespond with ONLY a JSON object matching this shape, no prose outside it:\n"
        '{"score": <0-100 int>, "factors": [{"factor": str, "weight": int, "note": str}, ...], '
        '"verdict": <one-sentence str>, "needs_clarification": null or '
        '{"question": str, "options": [str, ...]}}\n'
        "The factors' weights must sum to exactly `score`."
    )
    raw = _chat(settings.azure_planner_deployment, prefix + schema_instruction, suffix)
    data = json.loads(raw)
    return ArbiterVerdict.model_validate(data)

"""LLM_PROVIDER validation and per-provider role models (app/config.py)."""

import pytest
from pydantic import ValidationError

from app.config import Settings

_ENV = ("LLM_PROVIDER", "LLM_FAST_MODEL", "LLM_WORKER_MODEL", "LLM_PLANNER_MODEL", "LLM_MECHANICAL_MODEL",
        "NVIDIA_MODEL", "NVIDIA_WORKER_MODEL", "OPENROUTER_MODEL", "OPENROUTER_FAST_MODEL")


@pytest.fixture
def env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_FAST_MODEL", "moonshotai.kimi-k2.5")
    monkeypatch.setenv("LLM_WORKER_MODEL", "zai.glm-5")
    return monkeypatch


def test_an_unknown_provider_refuses_to_start(env):
    env.setenv("LLM_PROVIDER", "nvidai")
    with pytest.raises(ValidationError):
        Settings()


def test_provider_is_case_insensitive(env):
    env.setenv("LLM_PROVIDER", "NVIDIA")
    assert Settings().llm_provider == "nvidia"


def test_bedrock_keeps_the_role_values_from_env(env):
    env.setenv("LLM_PROVIDER", "bedrock")
    s = Settings()
    assert (s.azure_fast_deployment, s.azure_worker_deployment) == ("moonshotai.kimi-k2.5", "zai.glm-5")


def test_nvidia_and_openrouter_never_receive_a_mantle_id(env):
    env.setenv("LLM_PROVIDER", "nvidia")
    env.setenv("NVIDIA_WORKER_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    s = Settings()
    assert s.azure_worker_deployment == "nvidia/nemotron-3-super-120b-a12b"
    assert s.azure_fast_deployment == s.azure_planner_deployment == s.azure_mechanical_deployment == (
        "nvidia/nemotron-3-ultra-550b-a55b"
    )
    env.setenv("LLM_PROVIDER", "openrouter")
    env.setenv("OPENROUTER_FAST_MODEL", "z-ai/glm-5")
    s = Settings()
    assert s.azure_fast_deployment == "z-ai/glm-5"
    assert s.azure_worker_deployment == "nvidia/nemotron-3-ultra-550b-a55b:free"

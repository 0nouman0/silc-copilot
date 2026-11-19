"""Central server-side configuration. Secrets come only from environment variables.

INTEGRATION_MODE
  demo        sample data + mock applicant API, clearly labelled everywhere.
  production  requires real adapters; NEVER silently falls back to demo data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

BRAND_PRODUCT = "CHRIST Admissions Copilot (unofficial)"
BRAND_UNIVERSITY = os.getenv("UNIVERSITY_NAME", "CHRIST (Deemed to be University)")

_PROVIDER_DEFAULTS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-oss-20b:free"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}


def integration_mode() -> str:
    return "production" if os.getenv("INTEGRATION_MODE", "demo").lower() == "production" else "demo"


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    api_key: str | None
    model: str
    base_url: str
    timeout_s: float


def llm_config() -> LlmConfig:
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    d = _PROVIDER_DEFAULTS.get(provider, {"base_url": "", "model": ""})
    return LlmConfig(
        provider=provider,
        api_key=os.getenv("LLM_API_KEY") or None,
        # Free-tier models change often and not all support tool calling.
        # Override with LLM_MODEL and verify tool-calling support.
        model=os.getenv("LLM_MODEL") or d["model"],
        base_url=os.getenv("LLM_BASE_URL") or d["base_url"],
        timeout_s=float(os.getenv("LLM_TIMEOUT_S", "45")),
    )


def agent_mode() -> str:
    """decisions: a typed-answer 'decider' model classifies; code writes answers (no free-text LLM).
    tools: a chat model with tool calling writes answers. Auto-selects decisions for *decider* models."""
    explicit = os.getenv("AGENT_MODE", "").lower()
    if explicit in ("decisions", "tools"):
        return explicit
    return "decisions" if "decider" in llm_config().model.lower() else "tools"


def decisions_url() -> str:
    return os.getenv("LLM_DECISIONS_URL", "https://openrouter.ai/api/alpha/decisions")


def session_secret() -> str:
    secret = os.getenv("SESSION_SECRET")
    if secret:
        return secret
    if integration_mode() == "production":
        raise RuntimeError("SESSION_SECRET must be set in production mode.")
    return "dev-only-insecure-session-secret"  # never used in production mode


DEFAULT_COMPOSE_MODELS = ("nvidia/nemotron-3.5-lightning:free,inclusionai/ling-3.2-flash:free,"
                          "google/gemma-4-31b-it:free,openrouter/free")


def compose_models() -> list[str]:
    """Chat models that rewrite verified answers in natural language (tried in order; free models come and go).
    COMPOSE=off disables the step. COMPOSE_MODELS overrides the list."""
    if os.getenv("COMPOSE", "on").lower() in ("off", "0", "false", "no"):
        return []
    raw = os.getenv("COMPOSE_MODELS") or DEFAULT_COMPOSE_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]

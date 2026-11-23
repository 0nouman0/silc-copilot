from __future__ import annotations

import os

from ..config import compose_models, decisions_url, llm_config
from .base import LLMError, LLMProvider, LLMResponse, Message, ToolCall, ToolDef
from .decisions import DecisionsClient
from .openai_compatible import OpenAICompatibleProvider

__all__ = ["get_interpreter_providers", "get_decider", "DecisionsClient", "get_llm_provider", "get_composer", "LLMError", "LLMProvider", "LLMResponse", "Message", "ToolCall", "ToolDef"]


def get_decider() -> DecisionsClient:
    """Typed-decision client (e.g. perplexity/pplx-decider-flash-v1.3:free). Key stays server-side."""
    cfg = llm_config()
    if not cfg.api_key:
        raise LLMError("not_configured", "No AI provider key is configured. Set LLM_API_KEY (see .env.example).")
    return DecisionsClient(api_key=cfg.api_key, model=cfg.model, url=decisions_url(), timeout_s=cfg.timeout_s,
                           extra_headers={"HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
                                          "X-OpenRouter-Title": "SILC Student Applicant Copilot"})


def get_llm_provider(agent: str | None = None) -> LLMProvider:
    """Build the configured provider (optionally with the model chosen for `agent`). To add a vendor with a different wire
    format (e.g. native Gemini) implement LLMProvider and add a branch here; the agent, tools and UI do not change."""
    cfg = llm_config(agent)
    if not cfg.api_key:
        raise LLMError("not_configured", "No AI provider key is configured. Set LLM_API_KEY (see .env.example).")
    if cfg.provider not in ("openrouter", "openai"):
        raise LLMError("not_configured", f'Unsupported LLM_PROVIDER "{cfg.provider}".')
    extra = {}
    if cfg.provider == "openrouter":
        extra = {"HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
                 "X-Title": "SILC Student Applicant Copilot"}
    return OpenAICompatibleProvider(name=cfg.provider, api_key=cfg.api_key, model=cfg.model,
                                    base_url=cfg.base_url, timeout_s=cfg.timeout_s, extra_headers=extra)


def get_composer():
    """Natural-language rewriter for verified decision answers, or None when disabled / no key."""
    from ..agent.composer import Composer
    cfg = llm_config()
    models = compose_models()
    if not cfg.api_key or not models or cfg.provider != "openrouter":
        return None
    extra = {"HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"), "X-Title": "CHRIST Admissions Copilot"}
    return Composer([OpenAICompatibleProvider(name="openrouter", api_key=cfg.api_key, model=m, base_url=cfg.base_url,
                                              timeout_s=8.0, extra_headers=extra) for m in models])


def get_interpreter_providers() -> list[OpenAICompatibleProvider]:
    """Chat models for the language-understanding step (spelling, intent, entities as JSON). Empty when no key, UNDERSTANDING_MODE=local, or
    no chat model is available (a typed-decision model cannot return free-form JSON). The local interpreter then does the work."""
    cfg = llm_config()
    if not cfg.api_key or (os.getenv("UNDERSTANDING_MODE") or "auto").lower() == "local":
        return []
    if cfg.provider == "openrouter":
        models = [m for m in ((os.getenv("UNDERSTANDING_MODELS") or "").split(",") or []) if m.strip()] or compose_models()[:2]
        extra = {"HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"), "X-Title": "CHRIST Admissions Copilot"}
    elif cfg.provider == "openai":
        models, extra = [cfg.model], {}
    else:
        return []
    return [OpenAICompatibleProvider(name=cfg.provider, api_key=cfg.api_key, model=m.strip(), base_url=cfg.base_url, timeout_s=7.0, extra_headers=extra) for m in models if m.strip()]

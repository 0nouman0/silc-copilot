from __future__ import annotations

import os

from ..config import decisions_url, llm_config
from .base import LLMError, LLMProvider, LLMResponse, Message, ToolCall, ToolDef
from .decisions import DecisionsClient
from .openai_compatible import OpenAICompatibleProvider

__all__ = ["get_decider", "DecisionsClient", "get_llm_provider", "LLMError", "LLMProvider", "LLMResponse", "Message", "ToolCall", "ToolDef"]


def get_decider() -> DecisionsClient:
    """Typed-decision client (e.g. perplexity/pplx-decider-flash-v1.3:free). Key stays server-side."""
    cfg = llm_config()
    if not cfg.api_key:
        raise LLMError("not_configured", "No AI provider key is configured. Set LLM_API_KEY (see .env.example).")
    return DecisionsClient(api_key=cfg.api_key, model=cfg.model, url=decisions_url(), timeout_s=cfg.timeout_s,
                           extra_headers={"HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
                                          "X-OpenRouter-Title": "SILC Student Applicant Copilot"})


def get_llm_provider() -> LLMProvider:
    """Build the configured provider. To add a vendor with a different wire format
    (e.g. native Gemini) implement LLMProvider and add a branch here; the agent,
    tools and UI do not change."""
    cfg = llm_config()
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

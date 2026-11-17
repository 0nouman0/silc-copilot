"""Client for OpenRouter's /alpha/decisions API.

A decisions model never writes free text: it answers narrow typed questions
(noul = probability yes, choice = pick one with probabilities, score). Our code
owns the workflow. Because every output is constrained to options WE define,
prompt injection can at worst mis-route a question; it cannot make the system
say anything.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .base import LLMError


class DecisionsClient:
    def __init__(self, *, api_key: str, model: str, url: str, timeout_s: float,
                 extra_headers: dict[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, retry_delay_s: float = 1.5):
        self.retry_delay_s = retry_delay_s
        self.name, self.model = "openrouter-decisions", model
        self._key, self._url, self._timeout = api_key, url, timeout_s
        self._headers = extra_headers or {}
        self._transport = transport  # injectable for tests

    async def decide(self, state: list[str], questions: dict[str, Any]) -> dict[str, dict]:
        """One automatic retry for transient failures (free tiers throttle bursts)."""
        try:
            return await self._decide_once(state, questions)
        except LLMError as e:
            if e.code not in ("rate_limited", "unavailable", "timeout"):
                raise
            await asyncio.sleep(self.retry_delay_s)
            return await self._decide_once(state, questions)

    async def _decide_once(self, state: list[str], questions: dict[str, Any]) -> dict[str, dict]:
        body = {"model": self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", **self._headers}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                res = await c.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException:
            raise LLMError("timeout", "The AI provider took too long to respond.")
        except httpx.HTTPError:
            raise LLMError("unavailable", "Could not reach the AI provider.")
        if res.status_code in (401, 403):
            raise LLMError("not_configured", "The AI provider rejected the API key.")
        if res.status_code == 429:
            raise LLMError("rate_limited", "The AI provider is rate-limited right now. Please retry shortly.")
        if res.status_code >= 400:
            raise LLMError("unavailable", f"AI provider error (HTTP {res.status_code}).")
        try:
            answers = res.json()["answers"]
            if not isinstance(answers, dict):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise LLMError("bad_response", "The AI provider returned an unreadable response.")
        return answers

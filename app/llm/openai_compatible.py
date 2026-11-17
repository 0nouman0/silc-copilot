from __future__ import annotations

import httpx

from .base import LLMError, LLMResponse, Message, ToolCall, ToolDef


class OpenAICompatibleProvider:
    """One adapter for any OpenAI-style /chat/completions API.

    OpenRouter (testing) and OpenAI (final target) differ only in base URL,
    key and a couple of headers, so switching providers is a config change.
    """

    def __init__(self, *, name: str, api_key: str, model: str, base_url: str,
                 timeout_s: float, extra_headers: dict[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.name, self.model = name, model
        self._key, self._base, self._timeout = api_key, base_url.rstrip("/"), timeout_s
        self._headers = extra_headers or {}
        self._transport = transport  # injectable for tests

    @staticmethod
    def _wire(m: Message) -> dict:
        if m.role == "assistant" and m.tool_calls:
            return {"role": "assistant", "content": m.content, "tool_calls": [
                {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                for t in m.tool_calls]}
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
        return {"role": m.role, "content": m.content or ""}

    async def complete(self, messages, tools=None, temperature=0.2) -> LLMResponse:
        body: dict = {"model": self.model, "temperature": temperature,
                      "messages": [self._wire(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", **self._headers}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                res = await c.post(f"{self._base}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException:
            raise LLMError("timeout", "The AI provider took too long to respond.")
        except httpx.HTTPError:
            raise LLMError("unavailable", "Could not reach the AI provider.")

        if res.status_code in (401, 403):
            raise LLMError("not_configured", "The AI provider rejected the API key.")
        if res.status_code == 429:
            raise LLMError("rate_limited", "The AI provider is rate-limited right now. Please retry shortly.")
        if res.status_code == 404 and "tool" in res.text.lower():
            raise LLMError("tools_unsupported",
                           f'Model "{self.model}" does not support tool calling. Choose another LLM_MODEL.')
        if res.status_code >= 400:
            raise LLMError("unavailable", f"AI provider error (HTTP {res.status_code}).")

        try:
            data = res.json()
        except ValueError:
            raise LLMError("bad_response", "The AI provider returned an unreadable response.")
        if data.get("error"):  # OpenRouter may return HTTP 200 with an error body
            raise LLMError("unavailable", "The AI provider returned an error.")
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("bad_response", "The AI provider returned an empty response.")
        calls = [ToolCall(id=t.get("id") or f"call_{i}", name=t["function"]["name"],
                          arguments=t["function"].get("arguments") or "{}")
                 for i, t in enumerate(msg.get("tool_calls") or []) if t.get("function", {}).get("name")]
        return LLMResponse(content=msg.get("content"), tool_calls=calls)

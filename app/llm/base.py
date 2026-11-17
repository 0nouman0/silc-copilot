"""Vendor-neutral LLM types. Agent, tools and RAG depend only on these."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as returned by the model


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]


class LLMError(Exception):
    """code: not_configured | rate_limited | unavailable | timeout | tools_unsupported | bad_response"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self, messages: list[Message], tools: list[ToolDef] | None = None, temperature: float = 0.2
    ) -> LLMResponse: ...

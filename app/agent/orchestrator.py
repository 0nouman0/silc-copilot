"""Agent orchestrator: LLM + tool loop. Vendor-neutral (talks to LLMProvider only)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..applicants.adapter import Application
from ..data.models import SourceRef
from ..llm.base import LLMProvider, Message
from ..tools import ToolContext, execute, tool_defs
from .prompt import build_system_prompt

MAX_STEPS = 5
EMPTY_FALLBACK = ("I wasn't able to put together an answer from the available information. "
                  "Please rephrase your question, or contact the Admissions Office.")

_FOLLOW_UPS = {
    "get_fee_structure": ["What other charges apply?", "Are scholarships available?"],
    "get_eligibility_criteria": ["Help me check my eligibility", "Which documents do I need?"],
    "assess_preliminary_eligibility": ["Which documents do I need?", "What are the application deadlines?"],
    "get_admission_dates": ["What is the application fee?", "How do I apply?"],
    "get_programme_details": ["What is the eligibility for this programme?", "What are the fees?"],
    "search_programmes": ["Compare these programmes", "Help me check my eligibility"],
    "get_scholarships": ["How do I apply for a scholarship?", "What are the fees?"],
    "get_required_documents": ["What are the application deadlines?", "How do I apply?"],
    "get_application_status": ["What should I do next?", "What documents are required?"],
    "get_hostel_information": ["What are the campus facilities?", "What are the fees?"],
}
_DEFAULT_FOLLOW_UPS = ["Show me the programmes available", "How do I contact the Admissions Office?"]


@dataclass
class AgentResult:
    reply: str
    sources: list[SourceRef] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    demo: bool = False
    intent: str | None = None


async def run_agent(provider: LLMProvider, history: list[dict], *, user_id: str | None,
                    active_application: Application | None) -> AgentResult:
    ctx = ToolContext(user_id=user_id,
                      active_application_id=active_application.application_id if active_application else None)
    system = build_system_prompt(
        authenticated=bool(user_id),
        active_application=active_application.model_dump() if active_application else None)
    messages = [Message("system", system)] + [Message(m["role"], m["content"]) for m in history]
    tools = tool_defs(ctx)

    sources: dict[str, SourceRef] = {}
    used: list[str] = []
    demo = False

    for _ in range(MAX_STEPS):
        resp = await provider.complete(messages, tools)
        if not resp.tool_calls:
            reply = (resp.content or "").strip() or EMPTY_FALLBACK
            return AgentResult(reply, list(sources.values()), used, _follow_ups(used), demo)
        messages.append(Message("assistant", resp.content, tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            out = execute(call.name, call.arguments, ctx)
            if call.name not in used:
                used.append(call.name)
            demo = demo or bool(out.data.get("demo"))
            for s in out.sources:
                sources[f"{s.doc_id}|{s.title}"] = s
            messages.append(Message("tool", json.dumps(out.data, default=str), tool_call_id=call.id))

    return AgentResult(EMPTY_FALLBACK, list(sources.values()), used, _follow_ups(used), demo)


def _follow_ups(used: list[str]) -> list[str]:
    for t in reversed(used):
        if t in _FOLLOW_UPS:
            return _FOLLOW_UPS[t]
    return _DEFAULT_FOLLOW_UPS

"""Agent orchestrator: LLM + tool loop. Vendor-neutral (talks to LLMProvider only)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..models import SourceRef
from ..llm.base import LLMProvider, Message
from ..tools import ToolContext, execute, tool_defs
from .prompt import build_system_prompt
from .skills import SKILLS, select_skill

MAX_STEPS = 5
EMPTY_FALLBACK = ("I wasn't able to put together an answer from the available information. "
                  "Please rephrase your question, or contact the Admissions Office.")

_FOLLOW_UPS = {
    "get_fee_structure": ["Are scholarships available?", "What are the application dates?"],
    "get_eligibility_criteria": ["Help me check my eligibility", "Which documents do I need?"],
    "assess_preliminary_eligibility": ["Which documents do I need?", "What are the application dates?"],
    "get_admission_dates": ["How do I apply?", "What are the fees?"],
    "get_programme_details": ["What is the eligibility for this programme?", "What are the fees?"],
    "search_programmes": ["Compare these programmes", "Help me check my eligibility"],
    "get_curriculum": ["What is the eligibility for this programme?", "What are the fees?"],
    "get_admission_process": ["What are the application dates?", "Show me the programmes available"],
    "get_campus_details": ["Show me the programmes available", "How do I apply?"],
    "get_campus_facilities": ["Which campus has the most facilities?", "Show me the programmes available"],
    "check_eligibility": ["Which documents do I need?", "How do I apply?"],
    "get_fees": ["How do I contact the Admissions Office?", "What is the eligibility for this programme?"],
    "get_application_dates": ["How do I apply?", "What is the eligibility for this programme?"],
    "get_topic_info": ["Show me the programmes available", "How do I contact the Admissions Office?"],
    "get_specializations": ["Show the curriculum", "What is the eligibility for this programme?"],
    "compare_programs": ["What is the eligibility for these programmes?", "How do I apply?"],
    "create_handoff": ["How do I apply?", "Show me the programmes available"],
}
_RECOMMEND_FOLLOW_UPS = ["Show me more options", "What is the eligibility for the first one?", "What are its fees?"]
_DEFAULT_FOLLOW_UPS = ["Show me the programmes available", "How do I contact the Admissions Office?"]


@dataclass
class AgentResult:
    reply: str
    sources: list[SourceRef] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    intent: str | None = None
    agent: str = "info"  # which agent produced the reply: the next turn is routed with this as context
    cards: list[dict] = field(default_factory=list)  # programme cards (recommendations) shown under the reply
    composable: bool = False  # the reply is verified prose that an AI voice may reword (it is checked against the facts and falls back to this text)
    state: dict | None = None  # conversation state after this turn (degree, topic, campus, programme under discussion); the client sends it back
    skill: str | None = None  # the skill that handled the turn (discover, program_qa, apply_guide, fees_deadlines, eligibility, recommend, ...)


async def run_agent(provider: LLMProvider, history: list[dict], *, agent: str = "info", skill: str | None = None, profile=None,
                    last_skill: str | None = None, context: str = "") -> AgentResult:
    """One tool-calling loop for one skill. The skill (chosen by a light rule layer unless given) fixes the instruction bundle and the
    tool set; tool calls outside that set are refused. `agent` ("info" or "recommend") is the API-level label of the skill's family."""
    if skill is None:
        skill = select_skill(history, last_skill or ("recommend" if agent == "recommend" else None))
    agent = SKILLS[skill].agent
    ctx = ToolContext(profile=profile)
    system = build_system_prompt(agent=agent, skill=skill)
    if context:
        system += ("\n## Conversation state (decided by the backend)\n" + context + "\nThe backend, not you, decides which programmes exist and which are shown: "
                   "never list programmes of your own, and never contradict these filters.\n")
    messages = [Message("system", system)] + [Message(m["role"], m["content"]) for m in history]
    tools = tool_defs(ctx, agent, skill)

    sources: dict[str, SourceRef] = {}
    used: list[str] = []
    cards: list[dict] = []   # structured programme suggestions the UI shows as cards

    for _ in range(MAX_STEPS):
        resp = await provider.complete(messages, tools)
        if not resp.tool_calls:
            reply = (resp.content or "").strip() or EMPTY_FALLBACK
            return AgentResult(reply, list(sources.values()), used, _follow_ups(used, agent), agent=agent, cards=cards, skill=skill)
        messages.append(Message("assistant", resp.content, tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            out = execute(call.name, call.arguments, ctx, agent, skill)
            if call.name not in used:
                used.append(call.name)
            if call.name == "recommend_programmes":
                cards += out.data.get("recommendations", [])
            elif call.name == "rank_programs":
                cards += [{"programme_id": r["programme_id"], "name": r["name"], "level": r["level"], "department": r["department"], "duration_years": r["duration_years"],
                           "campuses": r["campuses"], "matched_interests": r.get("matched_words") or r["matched_on"], "career_paths": r["career_paths"], "eligibility": r["eligibility"]}
                          for r in out.data.get("recommendations", [])]
            for s in out.sources:
                sources[f"{s.doc_id}|{s.title}"] = s
            messages.append(Message("tool", json.dumps(out.data, default=str), tool_call_id=call.id))

    return AgentResult(EMPTY_FALLBACK, list(sources.values()), used, _follow_ups(used, agent), agent=agent, cards=cards, skill=skill)


def _follow_ups(used: list[str], agent: str = "info") -> list[str]:
    if agent == "recommend":
        return _RECOMMEND_FOLLOW_UPS
    for t in reversed(used):
        if t in _FOLLOW_UPS:
            return _FOLLOW_UPS[t]
    return _DEFAULT_FOLLOW_UPS

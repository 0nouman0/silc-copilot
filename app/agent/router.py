"""Which agent handles this turn: the INFO agent (fetches and reports facts) or the RECOMMEND agent (helps choose a programme).

Pure keyword routing, so it costs nothing and cannot be prompt-injected into a different tool set. The previous turn's agent is
sticky: while the applicant is answering the recommender's questions ("I like computers", "undergraduate") the recommender keeps
the conversation, until they ask for a specific fact (fees, eligibility, dates...), which goes to the info agent.
"""
from __future__ import annotations

import re

RECOMMEND_RX = re.compile(
    r"\b(recommend|suggest|which (course|programme|program|degree|stream)|what (course|programme|program|degree|stream)s? (should|can|would|do)|"
    r"what should i (study|take|do|pursue|choose|pick|join)|suit(s|able)? me|best for me|right (course|programme|program|degree|fit) for me|"
    r"(career|course|programme|program) (advice|guidance|options?|suggestions?)|help me (choose|pick|decide|select|find)|"
    r"i('m| am) (interested|confused|not sure|unsure|thinking)|i (like|love|enjoy|want to (study|learn|become|be))|my (interest|passion|goal)s?|"
    r"interested in|not sure what|don'?t know what|after (my )?(12th|10th|\+2|class 12|graduation|degree)|good (course|programme|program|career)|something (with|in|like))\b", re.I)
INFO_RX = re.compile(
    r"\b(fees?|tuition|eligib\w*|criteria|curriculum|syllabus|dates?|deadline|last date|how (do|to|can) i apply|apply|application (fee|status|process)|"
    r"my application|status|selection|entrance|admit card|documents?|compare|brochure|campuses|duration|intake|payment|contact)\b", re.I)


def route(history: list[dict], last_agent: str | None = None) -> str:
    """'recommend' or 'info' for the latest user message."""
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if INFO_RX.search(last) and not RECOMMEND_RX.search(last):
        return "info"
    if RECOMMEND_RX.search(last):
        return "recommend"
    return "recommend" if last_agent == "recommend" else "info"

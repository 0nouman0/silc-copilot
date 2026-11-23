"""General conversation: small talk beyond greetings, answered like a person would, with no database search.

Recognised kinds: how_are_you, doing_well, exploring, identity, capabilities, confused (undecided), unsupported (nothing to do with admissions).
Replies are fixed phrasings chosen so that the same line is never used twice in a conversation; none of them states a university fact.
"""
from __future__ import annotations

import re

from .smalltalk import pick

_HOW_ARE_YOU = re.compile(r"\bhow (are|r) (you|u)\b|\bhow'?s it going\b|\bhow do you do\b|\bwhat'?s up\b|\bhow have you been\b", re.I)
_DOING_WELL = re.compile(r"^\s*(i\s*('?m|am|m)\s+)?(doing\s+)?(very\s+|really\s+)?(fine|good|great|well|okay|ok|alright|not bad|awesome)\b(\s+(thanks|thank you|and you|how about you))*\s*[.!]?\s*$|\ball good\b|\bdoing (good|great|well|fine)\b", re.I)
_EXPLORING = re.compile(r"\b(just\s+)?(exploring|looking around|browsing|checking (it )?out|having a look|just looking|no idea yet|not sure yet)\b", re.I)
_IDENTITY = re.compile(r"\bwho are you\b|\bwhat are you\b|\byour name\b|\bare you (a )?(bot|human|ai|real|robot)\b", re.I)
_CAPABILITIES = re.compile(r"\bwhat can you do\b|\bhow can you help\b|\bwhat do you do\b|\bwhat can i ask\b|\bwhat (all )?can you (tell|help)\b", re.I)
_CONFUSED = re.compile(r"\b(confused|unsure|can'?t decide|cannot decide|not sure what to (choose|pick|do|study)|don'?t know what to (choose|pick|do|study)|help me (choose|decide|pick))\b", re.I)
ADMISSION_WORDS = re.compile(r"\b(programmes?|programs?|courses?|degrees?|admission|admissions|apply|application|fees?|eligib\w*|campus(es)?|christ|university|college|btech|b\.?tech|bsc|mba|mtech|phd|bca|"
                             r"engineering|science|arts|commerce|law|hostel|scholarship|lateral|diploma|semester|syllabus|curriculum|placement|intake|seat|entrance|exam|study|studying|learn|join|enrol|enroll)\b", re.I)


def converse_kind(text: str) -> str | None:
    t = text.strip()
    if not t:
        return None
    if _HOW_ARE_YOU.search(t) and len(t.split()) <= 8:
        return "how_are_you"
    if _IDENTITY.search(t):
        return "identity"
    if _CAPABILITIES.search(t):
        return "capabilities"
    if _DOING_WELL.search(t) and len(t.split()) <= 8:
        return "doing_well"
    if _EXPLORING.search(t) and len(t.split()) <= 6:
        return "exploring"
    if _CONFUSED.search(t) and not re.search(r"\b(between|vs|versus|and)\b", t, re.I):
        return "confused"
    return None


def looks_unrelated(text: str) -> bool:
    """No admissions vocabulary at all and long enough to be a real sentence: say what this assistant is for instead of guessing a search."""
    return len(text.split()) >= 3 and not ADMISSION_WORDS.search(text)


HOW_ARE_YOU = ["I'm doing well, thank you for asking! How about you?", "All good here, thanks! How are you doing today?"]
DOING_WELL = ["Glad to hear that! Are you exploring admission options at CHRIST, or is there something specific I can help with?",
              "Good to hear. Is there something about CHRIST admissions I can help you with today?"]
EXPLORING = ["That's great! CHRIST has undergraduate, postgraduate and doctoral programmes across several campuses. Are you planning to pursue an undergraduate degree?",
             "Sounds good, take your time. If you tell me a subject you like or a degree you're curious about, I can tell you what CHRIST offers."]
IDENTITY = ["I'm the CHRIST admissions assistant, an unofficial AI helper. I use the university's own programme records to explain programmes, eligibility, fees where they are published, lateral entry and how admission works."]
CAPABILITIES = ["I can explain what CHRIST offers, check eligibility and published fees for a programme, tell you where lateral entry is listed, describe how admission works, and compare degrees. Just ask in your own words."]
CONFUSED = ["No problem, choosing a course is a big decision. What subjects or kinds of work do you enjoy most? If you tell me a little about your interests, I can point you to options at CHRIST.",
            "That's completely normal. Do you like building and technology, understanding how things work in a science, business, or something creative? I'll help you narrow it down."]
UNSUPPORTED = ["That's outside what I can help with. I focus on CHRIST admissions: programmes, eligibility, fees, lateral entry and how to apply. Is there something about CHRIST I can help you with?",
               "I'm only able to help with CHRIST University admissions, so I can't help with that one. Ask me about programmes, eligibility, fees or how to apply."]

_BY_KIND = {"how_are_you": HOW_ARE_YOU, "doing_well": DOING_WELL, "exploring": EXPLORING, "identity": IDENTITY, "capabilities": CAPABILITIES, "confused": CONFUSED, "unsupported": UNSUPPORTED}
_FOLLOW = {"exploring": ["Undergraduate", "Postgraduate"], "capabilities": ["Tell me about B.Tech", "How many programmes are there?"], "confused": []}


def reply(kind: str, history: list[dict]) -> tuple[str, list[str]]:
    return pick(_BY_KIND[kind], history), list(_FOLLOW.get(kind, []))


def asked_how_are_you(history: list[dict]) -> bool:
    """The assistant's last message asked the applicant how they are, so a bare 'fine' / 'good' is an answer to it."""
    last = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
    return bool(re.search(r"how about you\?|how are you( doing)?( today)?\?", last, re.I))

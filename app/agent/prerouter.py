"""Rule pre-router: runs in code BEFORE any model, for the cases that must never depend on a model's judgement.

1. Injection patterns and abuse are blocked.
2. Refund, complaint, payment-problem and "my application" cases go straight to a human (Escalate), with the right campus contact.
3. An eligibility CHECK ("can I apply with 72%?") with no qualification or marks known triggers one clarifying question.

Everything else returns None and the orchestrator picks a skill. The reply text here is fixed, so it cannot be steered by user text.
"""
from __future__ import annotations

import re

from ..live import admissions_contact
from .orchestrator import AgentResult

INJECTION_RX = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+|my\s+)*(previous|prior|above|earlier|system|safety)?\s*(instructions?|prompts?|rules|guidelines)|"
    r"(reveal|show|print|repeat|leak|tell me)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions|rules|api key|keys?|secrets?)|"
    r"\byou are now\b|\bpretend (to be|you are)\b|\bact as (an? )?(unrestricted|dan|developer)|\bdeveloper mode\b|\bjailbreak\b|\bdo anything now\b|"
    r"<\s*/?\s*(system|assistant|tool)\s*>|\bBEGIN (SYSTEM|PROMPT)\b", re.I)
ABUSE_RX = re.compile(r"\b(fuck\w*|bitch\w*|bastard|asshole|motherfucker|cunt)\b", re.I)
OTHER_APPLICANT_RX = re.compile(r"\b(status|result|marks|offer|payment|details?)\b.{0,40}\b(of|for)\b.{0,25}\b(my (friend|brother|sister|cousin|neighbou?r|classmate)|another|someone|somebody|other (student|applicant)|him|her|them)\b|"
                                r"\b(someone|somebody|another (student|applicant)|other (student|applicant)|my (friend|brother|sister|cousin))'?s? (application|status|result|marks|payment)", re.I)
ESCALATE_RX = re.compile(
    r"\b(refund|money back|cancel(?:lation)? (of )?(my )?(admission|seat|application)|withdraw (my )?(admission|application)|complaint|complain|grievance|dispute|"
    r"payment (failed|issue|problem|deducted|not (received|updated|reflect\w*))|(payment|amount|money|fee|fees)\b.{0,25}\b(deducted|debited|charged)|(deducted|debited|charged) (twice|two times|double)|charged twice|double (charged|payment)|transaction (failed|id)|"
    r"my application|application (number|no\.?|id|status)|status of my|my (offer|result|admit card|hall ticket|selection)|did i (get|receive) (selected|admission|an offer)|"
    r"call me|call back|callback|document verification|my documents?)\b", re.I)
CHECK_RX = re.compile(r"\b(can i (apply|get|join|do|take)|am i eligible|do i (qualify|meet)|will i (get|be able)|i (have|got|scored|secured)\b.{0,30}\b(marks|percent|%|cgpa)|my (marks|percentage|cgpa|score))\b|\b\d{2}(\.\d+)?\s?%", re.I)
INFO_ONLY_RX = re.compile(r"\bwhat (is|are)\b.{0,25}\b(eligibility|criteria)\b|\beligibility (criteria|requirements?)\b", re.I)
HAS_MARKS_RX = re.compile(r"\b\d{2}(\.\d+)?\s?(%|percent|per cent)|\bcgpa\b.{0,15}\d|\b\d(\.\d+)?\s*(/|out of)\s*(10|4)\b", re.I)
QUAL_RX = re.compile(r"\b(i|i'm|i am|i have|i've|completed|finished|passed|done|doing|my|after)\b.{0,30}\b(12th|class 12|class xii|10\+2|\+2|puc|plus two|bachelor|degree|graduat\w*|master)", re.I)

BLOCK_REPLY = ("I can only help with CHRIST admissions: programmes, eligibility, how to apply, and campuses. I can't follow instructions that ask me to change how I work "
               "or to share internal details. What would you like to know about CHRIST?")
ABUSE_REPLY = "I'd like to help, but please keep the conversation respectful. Ask me about CHRIST programmes, eligibility, applying or campuses."
OTHER_REPLY = ("I can't look up or share anyone else's application, results or payments. Each applicant can see their own on the university's applicant portal after logging in. "
               "If you need help on someone's behalf, they should contact the admissions office directly.")


def contact_block(campus: str | None = None) -> str:
    rows = admissions_contact().get("campuses", [])
    if campus:
        rows = [r for r in rows if campus.lower() in r["campus"].lower()] or rows
    lines = []
    for r in rows[:6]:
        bits = [x for x in ([r["email"]] if r["email"] else []) + list(r["phones"][:2]) if x]
        if bits:
            lines.append(f"- **{r['campus']}**: " + " · ".join(bits))
    return "\n".join(lines) if lines else "Please contact the Admissions Office of the campus you applied to."


def _escalate_reply(text: str) -> str:
    kind = ("a refund or cancellation" if re.search(r"refund|money back|cancel|withdraw", text, re.I) else
            "a payment problem" if re.search(r"payment|deduct|debit|charged|transaction", text, re.I) else
            "a complaint" if re.search(r"complain|grievance|dispute", text, re.I) else "your own application")
    return (f"This is {kind}, which needs a person who can see your records. I can't see applications, payments or documents, and I can't change them.\n\n"
            f"Please contact the admissions office of your campus. For your own application status, log in to the applicant portal.\n\n{contact_block()}\n\n"
            "Please don't share card details, OTPs or passwords in this chat. When you contact them, mention what happened and when, and keep your application number ready.")


def prerouter(history: list[dict], profile=None) -> AgentResult | None:
    """A finished reply when a rule applies, else None (the orchestrator then picks a skill and calls the model)."""
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    follow = ["Show me the programmes available", "How do I apply?"]
    if INJECTION_RX.search(last):
        return AgentResult(BLOCK_REPLY, [], [], follow, intent="blocked", skill="blocked")
    if ABUSE_RX.search(last):
        return AgentResult(ABUSE_REPLY, [], [], follow, intent="blocked", skill="blocked")
    if OTHER_APPLICANT_RX.search(last):
        return AgentResult(OTHER_REPLY, [], [], ["How do I apply?", "How do I contact the Admissions Office?"], intent="escalate", skill="escalate")
    if ESCALATE_RX.search(last):
        return AgentResult(_escalate_reply(last), [], ["get_campus_contacts"], ["How do I apply?", "Show me the programmes available"], intent="escalate", skill="escalate")
    q = getattr(profile, "qualification", None) if profile is not None else None
    marks = bool(profile is not None and (getattr(profile, "percent", None) is not None or getattr(profile, "cgpa", None) is not None))
    if CHECK_RX.search(last) and not INFO_ONLY_RX.search(last):
        said_qual = bool(q) or bool(QUAL_RX.search(" ".join(m["content"] for m in history if m["role"] == "user")))
        said_marks = marks or bool(HAS_MARKS_RX.search(" ".join(m["content"] for m in history if m["role"] == "user")))
        if not said_qual and not said_marks:
            return AgentResult("Happy to check. First, what is the highest qualification you have completed (Class 12 / 10+2, a bachelor's degree or a master's degree), and what were your marks?",
                               [], [], ["I completed Class 12", "I have a bachelor's degree"], intent="clarify", skill="eligibility")
    return None

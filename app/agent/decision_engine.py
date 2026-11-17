"""Decision-driven agent for typed 'decider' models (no free-text generation) — CHRIST University only.

Per message:
  1. ONE typed call: the decider classifies intent, programme level and applicant category.
     Answers are constrained to options WE define, so injected text cannot become output.
  2. Code resolves any programme mentioned (asking when genuinely ambiguous), runs the registry
     tools, and renders the reply from the scraped CHRIST content, quoting the university's own
     text with source links. Anything not published is reported as unavailable — never guessed.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..applicants.adapter import Application
from ..christ.eligibility_hint import hint
from ..christ.models import Programme, SourceRef
from ..christ.store import DataNotReady, Resolution, get_corpus
from ..llm.decisions import DecisionsClient
from ..tools import ToolContext, ToolOutput, execute
from .orchestrator import AgentResult

CONF_MIN = 0.45
UNI = "CHRIST (Deemed to be University)"

INTENTS = {
    "greeting": "Greeting or small talk such as hello, hi, thanks",
    "programmes_overview": "Asks which programmes/courses/degrees CHRIST offers, optionally by level, subject or campus",
    "programme_details": "Asks about one programme: duration, mode, medium, specializations, campus, overview, careers",
    "curriculum": "Asks about syllabus, subjects, semester-wise curriculum, course structure, core or elective subjects",
    "compare_programmes": "Asks to compare or differentiate two programmes",
    "eligibility_criteria": "Asks what the eligibility, qualification, minimum marks, age limit or required subjects are for a programme",
    "eligibility_check": "Gives their own marks/qualification or asks whether THEY are eligible / can apply (final year, backlog, stream, other country)",
    "dates": "Asks about application open/close dates, deadlines, last date, result dates, admission calendar",
    "commencement": "Asks when classes/programme/semester start, orientation, reporting date, academic calendar",
    "application_help": "Asks how to apply, the application portal, registering, submitting, application process, what a status means, login/password/OTP problems",
    "payment_help": "Payment problems: failed payment, money debited, receipt, refund, payment pending, how to pay fees",
    "application_fee": "Asks the amount of the application/registration fee for applying",
    "programme_fees": "Asks tuition/programme fee, total fee, yearly fee, other charges, caution deposit, instalments, loans",
    "entrance_exam": "Asks about entrance exam, selection process, tests, interview, merit list, admit card, centres, CUET",
    "offer_joining": "Asks about admission offer, selection result, accepting an offer, confirmation fee, joining, original documents, student ID",
    "documents": "Asks which documents/certificates are required, upload format, verification",
    "scholarships": "Asks about scholarships, freeships, concessions, financial assistance",
    "hostel": "Asks about hostel or accommodation, rooms, mess, hostel fees and rules",
    "campus": "Asks about campuses, location, facilities, library, sports, transport, student life, campus visit",
    "placements": "Asks about placements, recruiters, salary packages, internships, career support",
    "international": "Asks about international/foreign/NRI applicants, visa, passport, IELTS/TOEFL, equivalence",
    "contact_support": "Asks how to contact admissions, phone, email, counsellor, helpline, brochure, support",
    "about_university": "Asks about CHRIST University itself: history, rankings, accreditation, NAAC, NIRF, leadership, vision, recognition",
    "my_applications": "Asks how many applications they have submitted or to list their own applications",
    "my_application_status": "Asks about the status or outcome of THEIR OWN specific application: have I been selected, is mine shortlisted/rejected, what next (NOT what a status term means in general)",
    "guarantee_request": "Asks the assistant to guarantee or promise admission, a scholarship, a seat or a job, or to make the admission decision",
    "other_person_data": "Asks about another person's application, status, marks or personal data",
    "off_topic": "Unrelated to CHRIST University admissions",
}

PHRASE = {"programme_fees": "What are the fees for", "eligibility_criteria": "What is the eligibility for", "eligibility_check": "Check my eligibility for",
          "dates": "Application dates for", "programme_details": "Tell me about", "curriculum": "Show the curriculum of",
          "entrance_exam": "Selection process for", "application_fee": "Application fee for"}
NEXT = ["What is the eligibility for {n}?", "What are the fees for {n}?", "Application dates for {n}", "Show the curriculum of {n}"]
GENERIC = {
    "programmes_overview": ["Tell me about the MBA programme", "What are the application dates?"],
    "dates": ["How do I apply?", "What are the fees for the MBA?"],
    "application_help": ["Which documents do I need?", "What are the application dates?"],
    "payment_help": ["How do I contact the Admissions Office?", "How do I apply?"],
    "documents": ["How do I apply?", "What are the application dates?"],
    "scholarships": ["What are the fees for the MBA?", "How do I apply?"],
    "hostel": ["What are the campus facilities?", "Are scholarships available?"],
    "campus": ["What are the hostel facilities?", "Show me the programmes available"],
    "placements": ["Show me the programmes available", "How do I contact the Admissions Office?"],
    "international": ["Which documents do I need?", "What are the application dates?"],
    "about_university": ["Show me the programmes available", "How do I apply?"],
    "my_applications": ["What is the status of my application?"],
    "my_application_status": ["What are the fees for this programme?", "What is the eligibility for this programme?"],
}
DEFAULT_SUGGEST = ["Show me the programmes available", "How do I apply?", "How do I contact the Admissions Office?"]


# ----------------------------------------------------------------------------- understanding
@dataclass
class Understanding:
    intent: str = "off_topic"
    confidence: float = 0.0
    level: str | None = None
    category: str | None = None


def _choice(a: dict, key: str) -> tuple[str | None, float]:
    x = a.get(key) or {}
    return x.get("choice"), float(x.get("confidence") or 0.0)


def build_questions() -> dict[str, Any]:
    return {
        "intent": {"type": "choice", "criteria": INTENTS,
                   "instructions": f"What is the user's latest message mainly asking, about {UNI}? Use the conversation for context."},
        "level": {"type": "choice", "instructions": "Which programme level does the user ask about?",
                  "criteria": {"undergraduate": "Undergraduate / bachelor / UG", "postgraduate": "Postgraduate / master / PG",
                               "doctoral": "Doctoral / PhD", "any": "Not specified"}},
        "category": {"type": "choice", "instructions": "Is the applicant international/foreign/NRI or domestic Indian?",
                     "criteria": {"domestic": "Domestic / Indian applicant", "international": "International, foreign, NRI/OCI or from another country",
                                  "unspecified": "Not stated"}},
    }


def build_state(history: list[dict], *, signed_in: bool, active: Application | None) -> list[str]:
    ctx = [f"Assistant for {UNI}, Bangalore", f"Signed in: {'yes' if signed_in else 'no'}"]
    if active:
        ctx.append(f"Active application: {active.application_id} ({active.programme_name})")
    lines = [f'{m["role"].upper()}: {m["content"][:300]}' for m in history[:-1][-6:]]
    return ["Context: " + "; ".join(ctx), "Conversation so far:\n" + ("\n".join(lines) or "(none)"),
            "Latest user message: " + history[-1]["content"][:1000]]


def understand(answers: dict[str, dict]) -> Understanding:
    u = Understanding()
    i, u.confidence = _choice(answers, "intent")
    u.intent = i if i in INTENTS else "off_topic"
    u.level, _ = _choice(answers, "level")
    u.category, _ = _choice(answers, "category")
    return u


# ----------------------------------------------------------------------------- turn + request
@dataclass
class Turn:
    ctx: ToolContext
    used: list[str] = field(default_factory=list)
    sources: dict[str, SourceRef] = field(default_factory=dict)
    demo: bool = False
    followups: list[str] | None = None

    def run(self, tool: str, **args) -> ToolOutput:
        out = execute(tool, json.dumps({k: v for k, v in args.items() if v is not None}), self.ctx)
        if tool not in self.used:
            self.used.append(tool)
        self.demo = self.demo or bool(out.data.get("demo"))
        for s in out.sources:
            self.sources[f"{s.url or s.doc_id}|{s.title}"] = s
        return out


@dataclass
class Req:
    text: str
    history: list[dict]
    u: Understanding
    signed_in: bool
    active: Application | None
    res: Resolution | None


def _err(out: ToolOutput) -> str | None:
    d = out.data
    if "error" in d:
        return d.get("message") or "This information cannot be retrieved right now."
    if d.get("found") is False:
        return d["message"]
    return None


CONTACT_HINT = "Please confirm with the CHRIST Admissions Office."


def _fmt_date(s: str | None) -> str:
    if not s:
        return "Not published"
    try:
        d = date.fromisoformat(s[:10])
        return f"{d.day} {d.strftime('%B %Y')}"
    except ValueError:
        return s


def _na(v) -> str:
    return "Not listed" if v in (None, "", []) else str(v)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers), *["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]])


def _label(p: Programme) -> str:
    return f"{p.name} ({p.campus})" if p.campus else p.name


# ----------------------------------------------------------------------------- programme resolution
def resolve_programme(history: list[dict], active: Application | None) -> Resolution:
    c = get_corpus()
    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    for msg in [user_msgs[-1]] + list(reversed(user_msgs[:-1]))[:4]:
        r = c.resolve(msg)
        if r.best:
            return _collapse_campuses(r, msg)
    if active:
        r = c.resolve(active.programme_name)
        if r.best:
            return _collapse_campuses(r, active.programme_name)
    return Resolution(None, [])


def _collapse_campuses(r: Resolution, msg: str) -> Resolution:
    """Same programme listed on several campuses is NOT ambiguous: pick the campus named in the message, else the first."""
    if not r.ambiguous and len(r.candidates) > 1:
        q = set(re.findall(r"[a-z]+", msg.lower()))
        pick = next((p for p in r.candidates if p.campus and {t for t in re.findall(r"[a-z]+", p.campus.lower()) if t not in ("bangalore", "campus")} & q), next((p for p in r.candidates if p.campus and "central" in p.campus.lower()), r.candidates[0]))
        return Resolution(pick, [pick] + [p for p in r.candidates if p is not pick])
    return r


def need_programme(t: Turn, q: Req, intent: str, what: str) -> str | None:
    """Return a clarification reply if there's no single programme yet, else None."""
    res = q.res
    if res and res.best and not res.ambiguous:
        return None
    phrase = PHRASE.get(intent, "Tell me about")
    pool = list(res.candidates) if res and res.candidates else get_corpus().search_programmes(q.text, limit=60)
    seen, opts = set(), []
    for p in pool:  # the same programme on several campuses is one option
        if p.name not in seen:
            seen.add(p.name)
            opts.append(p)
    opts = opts[:6]
    if opts:
        t.followups = [f"{phrase} {p.name}" for p in opts]
        return f"Several CHRIST programmes match. Which one would you like {what} for?\n\n" + "\n".join(f"- {p.name}" for p in opts)
    t.followups = [f"{phrase} MBA", f"{phrase} BBA", f"{phrase} B.Com", f"{phrase} B.Tech"]
    return (f"Which CHRIST programme would you like {what} for? Tell me the name (for example *MBA*, *BBA*, *B.Com*, *B.Tech Computer Science*), "
            "or browse them on the **Explore Programmes** page.")


def other_campuses(q: Req) -> str:
    if q.res and len(q.res.candidates) > 1 and not q.res.ambiguous:
        return "\n\n_Also listed at: " + ", ".join(p.campus or "" for p in q.res.candidates[1:]) + ". Fees and criteria are shown for " + (q.res.best.campus or "the first listing") + "._"
    return ""


# ----------------------------------------------------------------------------- generic knowledge-base rendering
def kb_reply(t: Turn, query: str, cats: set[str] | None, *, intro: str = "", limit: int = 3, none_msg: str | None = None,
             tool: str = "search_knowledge_base") -> str:
    out = t.run(tool, query=query) if tool != "search_knowledge_base" else t.run("search_knowledge_base", query=query)
    if _err(out):
        return none_msg or (f"I couldn't find that in the information I collected from the CHRIST website. {CONTACT_HINT}")
    body = "\n\n".join(f"**{p['section']}** — _{p['document']}_\n{p['text']}" for p in out.data["passages"][:limit])
    return (intro + "\n\n" if intro else "") + body


# ----------------------------------------------------------------------------- handlers
def h_greeting(t, q):
    return (f"Hello! I'm the unofficial admissions assistant for **{UNI}**. I can help with programmes, eligibility, fees, application dates, "
            "the application process, scholarships, hostel, campus and international admission. What would you like to know?")


def h_programmes(t, q):
    c = get_corpus()
    words = re.sub(r"\b(show|me|all|list|programmes?|courses?|degrees?|undergraduate|postgraduate|doctoral|ug|pg|phd|available|offer|offered|do|you|have)\b", " ", q.text, flags=re.I)
    level = q.u.level if q.u.level in ("undergraduate", "postgraduate", "doctoral") else None
    out = t.run("search_programmes", query=words.strip() or None, level=level)
    rows = out.data.get("programmes", [])
    counts = Counter(p.level for p in c.programmes)
    head = f"CHRIST lists **{len(c.programmes)} programmes** ({counts['undergraduate']} undergraduate, {counts['postgraduate']} postgraduate, {counts['doctoral']} doctoral"
    head += f", {counts['other']} other).\n\n" if counts["other"] else ").\n\n"
    if not words.strip() or not rows:
        return head + "Tell me a subject or name (for example *computer science*, *law*, *MBA*) and I'll list matching programmes, or use the **Explore Programmes** page to filter all of them."
    shown = rows[:15]
    return head + f"Programmes matching your request ({len(rows)}{'+' if len(rows) >= 25 else ''}):\n\n" + _table(
        ["Programme", "Level", "Campus", "Apply by"], [[p["name"], p["level"].title(), _na(p["campus"]), _fmt_date(p["application_close"])] for p in shown])


def _about_overview(p: Programme) -> str:
    ov = p.overview or ""
    i = ov.find("About the Programme")
    seg = ov[i + len("About the Programme"):] if i >= 0 else ov
    seg = re.sub(r"\n+", " ", seg).strip()
    return seg[:700].rsplit(" ", 1)[0] + "…" if len(seg) > 700 else seg


def h_details(t, q):
    if (m := need_programme(t, q, "programme_details", "details")):
        return m
    p = q.res.best
    out = t.run("get_programme_details", programme_id=p.id)
    if (e := _err(out)):
        return e
    d, qf = out.data["programme"], out.data["programme"]["quick_facts"]
    rows = [["Level", p.level.title()], ["Campus", _na(p.campus)], ["Duration", _na(qf.get("duration"))], ["Semesters", _na(qf.get("semesters"))],
            ["Mode", _na(qf.get("mode"))], ["Medium", _na(qf.get("medium"))],
            ["Applications", f"{_fmt_date(p.application_open)} → {_fmt_date(p.application_close)}"]]
    links = [f"[Programme page]({p.url})"] + ([f"[Apply online]({p.apply_url})"] if p.apply_url else []) + ([f"[Brochure]({p.brochure_url})"] if p.brochure_url else [])
    sec = ("\n\nI can also show: " + ", ".join(f"*{s}*" for s in d["sections_available"][:8])) if d["sections_available"] else ""
    t.followups = [x.format(n=p.name) for x in NEXT]
    return f"**{p.name}**\n\n" + _table(["Item", "Detail"], rows) + (f"\n\n{_about_overview(p)}" if _about_overview(p) else "") + "\n\n" + " · ".join(links) + sec + other_campuses(q)


def h_curriculum(t, q):
    if (m := need_programme(t, q, "curriculum", "the curriculum")):
        return m
    p = q.res.best
    out = t.run("get_programme_section", programme_id=p.id, topic="curriculum syllabus course structure semester")
    t.followups = [x.format(n=p.name) for x in NEXT if "curriculum" not in x]
    if _err(out):
        return (f"The detailed curriculum for **{p.name}** isn't in the text I collected. See the [programme page]({p.url})" +
                (f" and the [brochure]({p.brochure_url})." if p.brochure_url else ".") + f" {CONTACT_HINT}")
    d = out.data
    if "section" in d:
        return f"**{p.name} — {d['section']}**\n\n{d['text']}"
    return f"**{p.name}**\n\n" + "\n\n".join(f"**{s['title']}**\n{s['text']}" for s in d["sections"])


def _split_two(text: str) -> list[str]:
    return [s for s in re.split(r"\bvs\.?\b|\bversus\b|\band\b|\bor\b|\bbetween\b|\bwith\b|\bcompared? to\b|,|&", text, flags=re.I) if s.strip()]


def h_compare(t, q):
    c = get_corpus()
    found: list[Programme] = []
    for seg in _split_two(q.text):
        r = c.resolve(seg)
        if r.best and r.best.id not in {x.id for x in found}:
            found.append(r.best)
    if len(found) < 2:
        t.followups = ["Compare MBA and M.Com", "Compare BBA and B.Com"]
        return "Which two CHRIST programmes would you like to compare? For example: *Compare MBA and M.Com*."
    a, b = found[:2]
    t.run("get_programme_details", programme_id=a.id)
    t.run("get_programme_details", programme_id=b.id)
    row = lambda k, f: [k, _na(f(a)), _na(f(b))]
    return f"**{a.name} vs {b.name}**\n\n" + _table(["", _label(a), _label(b)], [
        row("Level", lambda p: p.level.title()), row("Duration", lambda p: p.quick_facts.get("duration")),
        row("Mode", lambda p: p.quick_facts.get("mode")), row("Medium", lambda p: p.quick_facts.get("medium")),
        row("Applications close", lambda p: _fmt_date(p.application_close))]) + "\n\nAsk for the eligibility or fees of either to compare further."


def h_eligibility(t, q, check=False):
    if (m := need_programme(t, q, "eligibility_check" if check else "eligibility_criteria", "eligibility")):
        return m
    p = q.res.best
    out = t.run("get_eligibility_criteria", programme_id=p.id)
    if (e := _err(out)):
        return f"{e} See the [programme page]({p.url}). {CONTACT_HINT}"
    body = f"**Eligibility — {p.name}**{' · ' + p.campus if p.campus else ''} (as published on the CHRIST website)\n\n{out.data['eligibility']}"
    t.followups = [x.format(n=p.name) for x in NEXT if "eligibility" not in x]
    if not check:
        return body + "\n\nShare your percentage and qualification if you'd like a **preliminary** comparison." + other_campuses(q)
    pcts = [float(x) for m in q.history if m["role"] == "user" for x in re.findall(r"(\d{2}(?:\.\d+)?)\s*(?:%|percent|per cent)", m["content"], re.I)]
    asked = any("percentage" in m["content"].lower() for m in q.history if m["role"] == "assistant")
    last = q.history[-1]["content"].strip()
    if not pcts and asked and re.fullmatch(r"\d{2}(?:\.\d+)?", last):
        pcts = [float(last)]
    res = t.run("assess_preliminary_eligibility", programme_id=p.id, percentage=pcts[-1] if pcts else None).data["result"]
    return (f"**{res['label']}** — {p.name}\n\n{res['detail']}\n\n" + body + f"\n\n_{res['disclaimer']}_")


STATUS_LABEL = {"open": "Open now", "upcoming": "Opens soon", "closed": "Closed", "unknown": "—"}


def h_dates(t, q, only: str | None = None):
    res = q.res
    if res and res.best and not res.ambiguous:
        p = res.best
        t.run("get_admission_dates", programme_id=p.id)
        t.followups = [x.format(n=p.name) for x in NEXT if "Application dates" not in x]
        listings = res.candidates
        rows = [[_label(x), _fmt_date(x.application_open), _fmt_date(x.application_close), STATUS_LABEL[_status(x)]] for x in listings]
        return (f"**Application dates — {p.name}**\n\n" + _table(["Campus", "Opens", "Closes", "Status"], rows) +
                (f"\n\n[Apply online]({p.apply_url})" if p.apply_url and any(_status(x) != "closed" for x in listings) else "") +
                "\n\n_Dates are from the CHRIST programme catalogue; confirm on the official page._")
    out = t.run("get_admission_dates")
    w = out.data["windows"]
    if not w:
        return f"No application dates were found in the collected catalogue. {CONTACT_HINT}"
    current = sorted((x for x in w if x["status"] in ("open", "upcoming")), key=lambda x: (x["close"] or "9", x["level"]))
    closed = len(w) - len(current)
    if not current:
        return f"Every application window in the collected CHRIST catalogue has closed ({closed} earlier windows). Please check the CHRIST website for the next cycle. {CONTACT_HINT}"
    rows = [[x["level"].title(), _na(x["campus"]), _fmt_date(x["open"]), _fmt_date(x["close"]), STATUS_LABEL[x["status"]], str(x["programmes"])] for x in current]
    return ("**Application windows in the CHRIST catalogue** (open or upcoming)\n\n" + _table(["Level", "Campus", "Opens", "Closes", "Status", "Programmes"], rows[:30]) +
            (f"\n\n_{closed} earlier window(s) have closed and are not shown._" if closed else "") + "\n\nName a programme (for example *MBA*) for its exact dates.")


def _status(p: Programme) -> str:
    from ..tools.registry import window_status
    return window_status(p.application_open, p.application_close)


def h_fees(t, q, fee_only=False):
    if (m := need_programme(t, q, "application_fee" if fee_only else "programme_fees", "fees")):
        return m
    p = q.res.best
    out = t.run("get_fee_structure", programme_id=p.id)
    t.followups = [x.format(n=p.name) for x in NEXT if "fees" not in x]
    if (e := _err(out)):
        return f"{e} See the [programme page]({p.url}). {CONTACT_HINT}"
    fees = out.data["fees"]
    if fee_only:
        lines = [ln for ln in fees.split("\n") if re.search(r"registration fee|application fee|caution deposit|non-refundable", ln, re.I)]
        if lines:
            return f"**Application / registration fee — {p.name}**\n\n" + "\n".join(f"- {ln}" for ln in lines) + f"\n\n[Source]({p.url})" + other_campuses(q)
    return f"**Fees — {p.name}**{' · ' + p.campus if p.campus else ''} (as published on the CHRIST website)\n\n{fees}" + other_campuses(q)


def h_selection(t, q):
    res = q.res
    if res and res.best and not res.ambiguous:
        p = res.best
        out = t.run("get_programme_section", programme_id=p.id, topic="selection process entrance examination")
        if not _err(out) and "section" in out.data and re.search(r"select|entrance|exam|test", out.data["section"], re.I):
            return f"**{out.data['section']} — {p.name}**\n\n{out.data['text']}"
    return kb_reply(t, q.text + " selection process entrance examination", {"admissions", "examination"}, limit=3)


def h_apply(t, q):
    c = get_corpus()
    apply_url = Counter(p.apply_url for p in c.programmes if p.apply_url).most_common(1)
    link = f"\n\n**Apply online:** {apply_url[0][0]}" if apply_url else ""
    body = kb_reply(t, q.text + " application process online apply", {"admissions"}, limit=2)
    return body + link + "\n\n_I can explain the process, but I can't submit, edit or withdraw an application for you._"


def h_payment(t, q):
    body = kb_reply(t, q.text + " payment fee", {"fees", "admissions"}, limit=2)
    return body + ("\n\nFor transaction verification, refunds or debited-but-unconfirmed payments, contact the CHRIST Admissions Office / payment support. "
                   "_I can't verify payments, and I will never ask for your PIN, CVV, OTP or password._")


def h_contact(t, q):
    out = t.run("get_contact_information")
    if _err(out):
        return "I couldn't find contact details in the pages I collected. Please use the contact options on https://christuniversity.in ."
    body = "\n\n".join(f"**{p['section']}** — _{p['document']}_\n{p['text']}" for p in out.data["passages"][:3])
    return body + "\n\n_I can't create support tickets or book callbacks myself._"


def h_offer(t, q):
    if q.signed_in and q.active:
        return h_my_status(t, q)
    body = kb_reply(t, q.text + " selection result offer admission confirmation", {"admissions"}, limit=2)
    return body + ("\n\n" + ("Select an application to see its status." if q.signed_in else "Sign in to see the status of your own application."))


def h_topic(cats: set[str], extra: str, tool: str):
    def fn(t, q):
        out = t.run(tool, query=q.text + " " + extra)
        if _err(out):
            return f"I couldn't find that in the information I collected from the CHRIST website. {CONTACT_HINT}"
        return "\n\n".join(f"**{p['section']}** — _{p['document']}_\n{p['text']}" for p in out.data["passages"][:3])
    return fn


def h_commence(t, q):
    return kb_reply(t, q.text + " academic calendar classes commence orientation reporting", {"admissions", "programmes", "general"}, limit=2,
                    none_msg=f"Programme commencement and academic-calendar dates aren't in the pages I collected. {CONTACT_HINT}")


def h_about(t, q):
    return kb_reply(t, q.text + " CHRIST University", {"about", "general"}, limit=3)


SIGN_IN = "Please **sign in** (top right) so I can look up your own applications. I can't share personal application details otherwise."


def _apps(t):
    return t.run("get_my_applications")


def h_my_apps(t, q):
    if not q.signed_in:
        return SIGN_IN
    out = _apps(t)
    if (e := _err(out)):
        return e
    apps = out.data["applications"]
    if not apps:
        return "I don't see any applications associated with your account."
    return (f"I found **{len(apps)} application{'s' if len(apps) != 1 else ''}** associated with your account _(demo applicant records — no live CHRIST applicant system is connected)_:\n\n" +
            _table(["Reference", "Programme", "Status", "Last updated"], [[a["application_id"], a["programme_name"], a["status"], _na(a["last_updated"])] for a in apps]) +
            "\n\nUse **Switch application** below the message box to pick one to discuss.")


def h_my_status(t, q):
    if not q.signed_in:
        return SIGN_IN
    app_id = q.active.application_id if q.active else None
    if not app_id:
        out = _apps(t)
        if (e := _err(out)):
            return e
        apps = out.data["applications"]
        if len(apps) != 1:
            return "Which application would you like to discuss? " + ("Use **Switch application** below the message box." if apps else "I don't see any applications on your account.")
        app_id = apps[0]["application_id"]
    out = t.run("get_application_status", application_id=app_id)
    if (e := _err(out)):
        return e
    a = out.data["application"]
    offer = a["offer_status"]
    return (f"**{a['programme_name']}** ({a['application_id']}) _(demo applicant record)_\n\n- **Status:** {a['status']}\n"
            f"- **Offer status:** {offer if offer else 'No offer information has been returned for this application'}\n"
            f"- **Last updated:** {_na(a['last_updated'])}\n\n" + ("" if offer else f"_“{a['status']}” does not mean you have been selected or offered a seat._"))


def h_guarantee(t, q):
    return ("I can't guarantee or promise admission, a scholarship, a seat or a job, and I don't make admission decisions — the CHRIST Admissions Office does. "
            "I can share the published criteria and give you a *preliminary* indication.")


def h_other(t, q):
    return "I can't share another person's application or personal information. I can only discuss your own applications after you sign in."


def h_off(t, q):
    return f"I can only help with admissions at **{UNI}**: programmes, eligibility, fees, dates, applications, scholarships, hostel, campus and placements."


HANDLERS = {
    "greeting": h_greeting, "programmes_overview": h_programmes, "programme_details": h_details, "curriculum": h_curriculum,
    "compare_programmes": h_compare, "eligibility_criteria": lambda t, q: h_eligibility(t, q, False), "eligibility_check": lambda t, q: h_eligibility(t, q, True),
    "dates": h_dates, "commencement": h_commence, "application_help": h_apply, "payment_help": h_payment,
    "application_fee": lambda t, q: h_fees(t, q, True), "programme_fees": lambda t, q: h_fees(t, q, False), "entrance_exam": h_selection,
    "offer_joining": h_offer, "documents": h_topic({"admissions"}, "documents required certificates admission", "get_required_documents"),
    "scholarships": h_topic({"scholarships"}, "scholarship financial assistance", "get_scholarships"),
    "hostel": h_topic({"hostel"}, "hostel accommodation", "get_hostel_information"),
    "campus": h_topic({"campus"}, "campus facilities", "get_campus_information"),
    "placements": h_topic({"placements"}, "placement recruiters", "get_placement_information"),
    "international": lambda t, q: kb_reply(t, q.text + " international students NRI foreign nationals admission", {"international", "admissions"}, limit=3),
    "contact_support": h_contact, "about_university": h_about, "my_applications": h_my_apps, "my_application_status": h_my_status,
    "guarantee_request": h_guarantee, "other_person_data": h_other, "off_topic": h_off,
}
PROG_INTENTS = {"programme_details", "curriculum", "eligibility_criteria", "eligibility_check", "dates", "application_fee", "programme_fees", "entrance_exam"}
LOW_CONFIDENCE = ("I'm not sure I understood that. Could you rephrase, or pick a topic?\n\n"
                  "- Programmes and curriculum\n- Eligibility\n- Application dates\n- Fees and scholarships\n- Applying, documents and hostel")


# ----------------------------------------------------------------------------- entry point
async def run_decision_agent(decider: DecisionsClient, history: list[dict], *, user_id: str | None,
                             active_application: Application | None) -> AgentResult:
    ctx = ToolContext(user_id=user_id, active_application_id=active_application.application_id if active_application else None)
    t = Turn(ctx)
    answers = await decider.decide(build_state(history, signed_in=bool(user_id), active=active_application), build_questions())
    u = understand(answers)

    if u.confidence < CONF_MIN and u.intent not in ("greeting", "guarantee_request", "other_person_data"):
        return AgentResult(LOW_CONFIDENCE, [], t.used, DEFAULT_SUGGEST, t.demo, intent="unclear")

    try:
        res = resolve_programme(history, active_application) if (u.intent in PROG_INTENTS or u.intent == "compare_programmes") else None
        q = Req(history[-1]["content"], history, u, bool(user_id), active_application, res)
        reply = HANDLERS[u.intent](t, q)
    except DataNotReady as e:
        return AgentResult(f"The CHRIST website data has not been loaded yet. {e}", [], t.used, [], False, intent="data_not_ready")
    follow = t.followups or ([x.format(n=res.best.name) for x in NEXT[:3]] if res and res.best and not res.ambiguous else GENERIC.get(u.intent, DEFAULT_SUGGEST))
    return AgentResult(reply, list(t.sources.values()), t.used, follow, t.demo, intent=u.intent)

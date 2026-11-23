"""Decision-driven agent for typed 'decider' models (no free-text generation) — CHRIST University only.

Per message:
  1. ONE typed call: the decider classifies intent, programme level and applicant category.
     Answers are constrained to options WE define, so injected text cannot become output.
  2. Code resolves any programme mentioned (asking when genuinely ambiguous), runs the registry
     tools over the CHRIST programme database, and renders the reply from the database text, quoting
     the university's own words. Anything the database does not hold is reported as unavailable — never guessed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..catalogue import Programme, Resolution, detect_level, get_catalogue
from ..llm.decisions import DecisionsClient
from ..models import SourceRef
from ..sources import SourceUnavailable
from ..sources.text import tidy
from ..tools import ToolContext, ToolOutput, execute
from ..catalog.refresh import current_catalog
from .orchestrator import AgentResult
from .prerouter import contact_block as office_contacts
from .router import INFO_RX

CONF_MIN = 0.45
UNI = "CHRIST (Deemed to be University)"

INTENTS = {
    "greeting": "Greeting or small talk such as hello, hi, thanks",
    "recommend_programmes": "Wants help CHOOSING a programme: asks for suggestions or recommendations, says what they are interested in or enjoy, is unsure or confused what to study, asks what suits them or what to do after 12th/graduation, or answers the assistant's questions about their interests, level or campus",
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
    "application_status": "Asks about the status or outcome of an application they already submitted: have I been selected, is mine shortlisted/rejected, what is my status, how many applications do I have, did my application go through (NOT what a status term means in general)",
    "guarantee_request": "Asks the assistant to guarantee or promise admission, a scholarship, a seat or a job, or to make the admission decision",
    "other_person_data": "Asks about another person's application, status, marks or personal data",
    "off_topic": "Unrelated to CHRIST University admissions",
}

PHRASE = {"programme_fees": "What are the fees for", "eligibility_criteria": "What is the eligibility for", "eligibility_check": "Check my eligibility for",
          "dates": "Application dates for", "programme_details": "Tell me about", "curriculum": "Show the curriculum of",
          "entrance_exam": "Selection process for", "application_fee": "Application fee for"}
NEXT = ["What is the eligibility for {n}?", "What are the fees for {n}?", "Application dates for {n}", "Show the curriculum of {n}"]
GENERIC = {
    "recommend_programmes": ["Which programmes suit me after 12th?", "Suggest postgraduate programmes", "I'm not sure what to study"],
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
    "application_status": ["How do I apply?", "Which programmes would suit me?"],
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


def build_state(history: list[dict]) -> list[str]:
    lines = [f'{m["role"].upper()}: {m["content"][:300]}' for m in history[:-1][-6:]]
    return [f"Context: Assistant for {UNI}, Bangalore", "Conversation so far:\n" + ("\n".join(lines) or "(none)"),
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
    followups: list[str] | None = None
    cards: list[dict] = field(default_factory=list)   # structured programme suggestions for the UI
    agent: str = "info"   # the tool set this turn may use; the recommendation handler switches to "recommend"

    def run(self, tool: str, **args) -> ToolOutput:
        out = execute(tool, json.dumps({k: v for k, v in args.items() if v is not None}), self.ctx, self.agent)
        if tool not in self.used:
            self.used.append(tool)
        for s in out.sources:
            self.sources[f"{s.url or s.doc_id}|{s.title}"] = s
        return out


@dataclass
class Req:
    text: str
    history: list[dict]
    u: Understanding
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
def resolve_programme(history: list[dict]) -> Resolution:
    c = get_catalogue()
    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    for msg in [user_msgs[-1]] + list(reversed(user_msgs[:-1]))[:4]:
        r = c.resolve(msg)
        if r.best:
            return _collapse_campuses(r, msg)
    return Resolution(None, [])


def _collapse_campuses(r: Resolution, msg: str) -> Resolution:
    """Same programme listed on several campuses is NOT ambiguous: pick the campus named in the message, else Central, else the first."""
    if not r.ambiguous and len(r.candidates) > 1:
        q = set(re.findall(r"[a-z]+", msg.lower()))
        named = next((p for p in r.candidates if p.campus and {t for t in re.findall(r"[a-z]+", p.campus.lower()) if t not in ("bangalore", "campus")} & q), None)
        pick = named or next((p for p in r.candidates if p.campus and "central" in p.campus.lower()), r.candidates[0])
        return Resolution(pick, [pick] + [p for p in r.candidates if p is not pick])
    return r


def need_programme(t: Turn, q: Req, intent: str, what: str) -> str | None:
    """Return a clarification reply if there's no single programme yet, else None."""
    res = q.res
    if res and res.best and not res.ambiguous:
        return None
    phrase = PHRASE.get(intent, "Tell me about")
    pool = list(res.candidates) if res and res.candidates else get_catalogue().search(q.text, limit=60)
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
        return "\n\n_Also listed at: " + ", ".join(p.campus or "" for p in q.res.candidates[1:]) + ". Shown for " + (q.res.best.campus or "the first listing") + "._"
    return ""


# ----------------------------------------------------------------------------- readable formatting of database text
def _sections(rows: list[dict], limit: int = 3) -> str:
    """One or more named sections. A single section is shown as plain text; several get their own headings."""
    rows = rows[:limit]
    if len(rows) == 1:
        return tidy(rows[0]["text"])
    return "\n\n".join(f"#### {r['section']}\n\n{tidy(r['text'])}" for r in rows)


def _finish(reply: str) -> str:
    """Turn a leading **Bold title** into a real heading with a muted subtitle, so every answer has the same structure."""
    m = re.match(r"^\*\*(.+?)\*\*([^\n]*)\n", reply)
    if not m:
        return reply
    sub = re.sub(r"^[\s·—-]+", "", m.group(2)).strip()
    return f"### {m.group(1).strip()}\n\n" + (f"~ {sub}\n\n" if sub else "") + reply[m.end():].lstrip("\n")


def _dur(v) -> str | None:
    if v in (None, "", 0):
        return None
    try:
        n = float(v)
        return f"{int(n) if n == int(n) else n} year{'s' if n != 1 else ''}"
    except (TypeError, ValueError):
        return str(v)


def _missing(what: str) -> str:
    return f"{what} isn't in the CHRIST programme database that I read, so I can't answer that reliably. {CONTACT_HINT}"


# ----------------------------------------------------------------------------- handlers
def h_greeting(t, q):
    """Fallback only (the dialog manager normally answers greetings first, in code). No database call, and never the welcome message again."""
    from .dialog import greeting_reply, smalltalk_kind
    t.followups = ["Show me the programmes available", "How do I apply?"]
    if not smalltalk_kind(q.text):                       # the decider labelled something else a greeting: say so honestly instead of greeting back
        return ("I'm not sure what you mean by that. Tell me a programme or a degree (for example *B.Tech* or *BSc*), or ask about eligibility, the curriculum or how to apply.")
    return greeting_reply(q.text, q.history)


def h_programmes(t, q):
    c = get_catalogue()
    words = re.sub(r"\b(show|me|all|list|programmes?|courses?|degrees?|undergraduate|postgraduate|doctoral|ug|pg|phd|available|offer|offered|do|you|have)\b", " ", q.text, flags=re.I)
    level = q.u.level if q.u.level in ("undergraduate", "postgraduate", "doctoral") else None
    out = t.run("search_programmes", query=words.strip() or None, level=level)
    rows = out.data.get("programmes", [])
    counts = c.by_level()
    head = f"The CHRIST database lists **{len(c.programmes)} programme listings** ({counts['undergraduate']} undergraduate, {counts['postgraduate']} postgraduate, {counts['doctoral']} doctoral"
    head += f", {counts['other']} other).\n\n" if counts["other"] else ").\n\n"
    if not words.strip() or not rows:
        return head + "Tell me a subject or name (for example *computer science*, *law*, *MBA*) and I'll list matching programmes, or use the **Explore Programmes** page to filter all of them."
    shown = rows[:15]
    return head + f"Programmes matching your request ({len(rows)}{'+' if len(rows) >= 25 else ''}):\n\n" + _table(
        ["Programme", "Level", "Campus", "Duration"], [[p["name"], p["level"].title(), _na(p["campus"]), _na(_dur(p["duration_years"]))] for p in shown])


def _facts(d: dict) -> list[list[str]]:
    return [["Level", _na(d.get("level"))], ["Campus", _na(d.get("campus"))], ["Department", _na(d.get("department"))],
            ["Duration", _na(_dur(d.get("duration_years")))], ["Term type", _na(d.get("term_type"))]]


def _summary(d: dict) -> str:
    seg = re.sub(r"\s+", " ", d.get("summary") or d.get("overview") or "").strip()
    return seg[:700].rsplit(" ", 1)[0] + "…" if len(seg) > 700 else seg


def h_details(t, q):
    if (m := need_programme(t, q, "programme_details", "details")):
        return m
    p = q.res.best
    out = t.run("get_programme_details", programme_id=p.id)
    if (e := _err(out)):
        return e
    d = out.data["programme"]
    rows = _facts(d)
    t.followups = [x.format(n=p.name) for x in NEXT]
    note = "\n\n_Fees, application dates and seats are live admissions data: ask me for them and I'll tell you what is available._"
    return f"**{p.name}**\n\n" + _table(["Item", "Detail"], rows) + (f"\n\n{_summary(d)}" if _summary(d) else "") + note + other_campuses(q)


def h_curriculum(t, q):
    if (m := need_programme(t, q, "curriculum", "the curriculum")):
        return m
    p = q.res.best
    ym = re.search(r"\byear\s*(\d)\b", q.text, re.I)
    out = t.run("get_curriculum", programme_id=p.id, year=f"Year {ym.group(1)}" if ym else None)
    t.followups = [x.format(n=p.name) for x in NEXT if "curriculum" not in x]
    if _err(out):
        alt = t.run("get_programme_section", programme_id=p.id, section="structure")
        if not _err(alt):
            return f"**{p.name} — course structure**\n\n{_sections(alt.data['sections'], 1)}"
        return _missing(f"A detailed curriculum for **{p.name}**")
    terms = out.data["terms"]
    if not ym:
        first = terms[0]["year"]
        shown = [x for x in terms if x["year"] == first]
    else:
        shown = terms
    parts = [f"**{p.name} — curriculum**{' · ' + (shown[0]['year'] if shown else '')}"]
    for term in shown:
        title = f"#### {term['year']} · Term {term['term']}" + (f" — {term['focus']}" if term.get("focus") else "")
        subs = term["subjects"][:40]
        parts.append(title + "\n\n" + (_table(["Subject", "Category", "Credits"], [[s["subject"], _na(s["category"]), _na(s["credits"])] for s in subs]) if subs else "_No subjects listed._"))
    years = sorted({x["year"] for x in terms})
    if not ym and len(years) > 1:
        parts.append("_Ask for another year, for example “" + years[1] + " curriculum”, to see its terms._")
    return "\n\n".join(parts) + other_campuses(q)


def _split_two(text: str) -> list[str]:
    return [s for s in re.split(r"\bvs\.?\b|\bversus\b|\band\b|\bor\b|\bbetween\b|\bwith\b|\bcompared? to\b|,|&", text, flags=re.I) if s.strip()]


def h_compare(t, q):
    c = get_catalogue()
    found: list[Programme] = []
    for seg in _split_two(q.text):
        r = c.resolve(seg)
        if r.best:
            r = _collapse_campuses(r, seg)
        if r.best and r.best.id not in {x.id for x in found}:
            found.append(r.best)
    if len(found) < 2:
        t.followups = ["Compare MBA and M.Com", "Compare BBA and B.Com"]
        return "Which two CHRIST programmes would you like to compare? For example: *Compare MBA and M.Com*."
    a, b = found[:2]
    da, db = (t.run("get_programme_details", programme_id=x.id).data.get("programme") or {} for x in (a, b))
    fa, fb = dict(_facts(da)), dict(_facts(db))
    return f"**{a.name} vs {b.name}**\n\n" + _table(["", _label(a), _label(b)], [[k, fa[k], fb[k]] for k in fa if k != "Campus"]) + \
        "\n\nAsk for the eligibility or fees of either to compare further."


def h_eligibility(t, q, check=False):
    if (m := need_programme(t, q, "eligibility_check" if check else "eligibility_criteria", "eligibility")):
        return m
    p = q.res.best
    out = t.run("get_eligibility_criteria", programme_id=p.id)
    if (e := _err(out)):
        return f"{e} {CONTACT_HINT}"
    body = f"**Eligibility — {p.name}**{' · ' + p.campus if p.campus else ''} (as published in the CHRIST database)\n\n{_sections(out.data['sections'])}"
    t.followups = [x.format(n=p.name) for x in NEXT if "eligibility" not in x]
    if not check:
        return body + "\n\nShare your percentage and qualification if you'd like a **preliminary** comparison." + other_campuses(q)
    pcts = [float(x) for m in q.history if m["role"] == "user" for x in re.findall(r"(\d{2}(?:\.\d+)?)\s*(?:%|percent|per cent)", m["content"], re.I)]
    asked = any("percentage" in m["content"].lower() for m in q.history if m["role"] == "assistant")
    last = q.history[-1]["content"].strip()
    if not pcts and asked and re.fullmatch(r"\d{2}(?:\.\d+)?", last):
        pcts = [float(last)]
    v = t.run("check_eligibility", programme_id=p.id, percent=pcts[-1] if pcts else None).data.get("verdict")
    if v and v["outcome"] in ("eligible", "not_eligible"):
        why = "\n".join(f"- {c['condition']}: {c['detail']}" for c in v["conditions_checked"]) or "\n".join(f"- {r}" for r in v["reasons"])
        label = "You meet the published criteria" if v["outcome"] == "eligible" else "You do not meet a published criterion"
        return f"**{label}** — {p.name}\n\n{why}\n\n" + body + f"\n\n_{v['disclaimer']}_"
    if v and v["outcome"] == "need_info" and v.get("question"):
        return f"{v['question']}\n\n" + body
    res = t.run("assess_preliminary_eligibility", programme_id=p.id, percentage=pcts[-1] if pcts else None).data["result"]
    return (f"**{res['label']}** — {p.name}\n\n{res['detail']}\n\n" + body + f"\n\n_{res['disclaimer']}_")


STATUS_LABEL = {"open": "Open", "upcoming": "Opens soon", "closed": "Closed", "unknown": "—"}
DATES_CAVEAT = "_These are the dates recorded in the CHRIST database and they may be out of date. Please confirm the current dates with the Admissions Office._"


def _live_unavailable(t, q, intent: str, tool_name: str, title: str, what: str) -> str:
    """Fees, dates and seats are ERP-only. Until the ERP is connected the honest answer is 'not available right now' plus the contact."""
    if (m := need_programme(t, q, intent, what)):
        return m
    res = q.res
    if res and res.best and not res.ambiguous:
        p = res.best
        t.run(tool_name, programme_id=p.id)
        t.followups = [x.format(n=p.name) for x in NEXT if what not in x.lower()]
        head = f"**{title} — {p.name}**{' · ' + p.campus if p.campus else ''}"
    else:
        head = f"**{title}**"
    lead = "Fee information for this programme is not available in the current university database" if what == "fees" else f"The {what} for this programme are not available in the current university database"
    return (f"{head}\n\n{lead}: they come from the university's live admissions system, which is not connected to this assistant yet, so I can't give you a figure "
            f"and I won't guess one.\n\nPlease ask the admissions office:\n\n{office_contacts()}" + other_campuses(q))


def h_dates(t, q):
    res = q.res
    if not (res and res.candidates):      # no programme named: one general answer, no need to ask which
        t.run("get_application_dates")
        return (f"**Application dates**\n\nApplication and entrance-test dates come from the university's live admissions system, which is not connected to this assistant yet, "
                f"so I can't give you a date and I won't guess one.\n\nPlease ask the admissions office:\n\n{office_contacts()}")
    return _live_unavailable(t, q, "dates", "get_application_dates", "Application dates", "application dates")


def h_fees(t, q, fee_only=False):
    intent = "application_fee" if fee_only else "programme_fees"
    if (m := need_programme(t, q, intent, "fees")):
        return m
    p = q.res.best
    fact = (t.run("get_fees", programme_id=p.id).data.get("live_fact") or {})
    if not fact.get("available"):
        return _live_unavailable(t, q, intent, "get_fees", "Application / registration fee" if fee_only else "Fees", "fees")
    v = fact["value"]
    t.followups = [x.format(n=p.name) for x in NEXT if "fees" not in x]
    title = "Application / registration fee" if fee_only else "Fees"
    where = " \u00b7 " + p.campus if p.campus else ""
    head = f"**{title} \u2014 {p.name}**{where}"
    text = v["text"]
    if fee_only:
        lines = [ln for ln in text.split("\n") if re.search(r"registration fee|application fee|caution deposit|non-refundable", ln, re.I) and not ln.startswith("|")]
        if lines:
            text = "\n".join(f"- {ln.strip()}" for ln in lines)
    return f"{head}\n\n{tidy(text)}\n\n_{fact['note']}_" + other_campuses(q)


def h_selection(t, q):
    res = q.res
    if res and res.best and not res.ambiguous:
        p = res.best
        found = []
        for kind in ("selection", "entrance_test"):
            out = t.run("get_programme_section", programme_id=p.id, section=kind)
            if not _err(out):
                found += out.data["sections"]
        if found:
            return f"**Selection process — {p.name}**\n\n{_sections(found, 4)}" + other_campuses(q)
        det = t.run("get_programme_details", programme_id=p.id)
        sel = (det.data.get("programme") or {}).get("selection_process")
        if sel:
            return f"**Selection process — {p.name}**\n\n{tidy(sel)}"
        return _missing(f"A selection process for **{p.name}**")
    return _process(t, q, "selection", "Selection process")


def _group_rows(t: Turn, q: Req) -> list[dict]:
    """Admission-process rows for the applicant's level (ug / pg / phd groups), or every group when the level is unknown."""
    rows = t.run("get_admission_process").data.get("processes", [])
    level = (q.res.best.level if q.res and q.res.best else None) or q.u.level
    prefix = {"undergraduate": "ug", "postgraduate": "pg", "doctoral": "phd"}.get(level or "")
    return [r for r in rows if r["group"].lower().startswith(prefix)] if prefix and any(r["group"].lower().startswith(prefix) for r in rows) else rows


def _process(t: Turn, q: Req, field_: str, title: str, extra_fields: tuple[str, ...] = ()) -> str:
    """General admission text (one field of `adm_links`), de-duplicated across the groups that share the same wording."""
    variants: dict[str, list[str]] = {}
    for r in _group_rows(t, q):
        text = (r.get(field_) or "").strip()
        if text:
            variants.setdefault(text, []).append(r["group"])
    if not variants:
        return _missing(f"{title} for this programme group")
    parts = []
    for text, groups in list(variants.items())[:2]:
        label = f"#### {', '.join(groups[:4])}\n\n" if len(variants) > 1 else ""
        parts.append(label + tidy(text))
    return f"**{title}** (as published in the CHRIST database)\n\n" + "\n\n".join(parts)


def h_apply(t, q):
    body = _process(t, q, "how_to_apply", "How to apply")
    return body + "\n\n_I can explain the process, but I can't submit, edit or withdraw an application for you._"


def _office(t: Turn) -> str:
    """The admissions office's phone and email as recorded for the main campus (first active campus in the database)."""
    rows = t.run("get_campus_details").data.get("campuses") or []
    if not rows:
        return ""
    c = rows[0]
    bits = [b for b in [", ".join(c.get("phones") or []), ", ".join(c.get("emails") or [])] if b]
    return f"**Contact the Admissions Office:** {' · '.join(bits)} ({c['name']})" if bits else ""


def _contact_block(c: dict, heading: bool = True) -> str:
    lines = [f"#### {c['name']}"] if heading else []
    if c.get("address"):
        lines.append(f"- **Address:** {c['address']}")
    if c.get("phones"):
        lines.append(f"- **Phone:** {', '.join(c['phones'])}")
    if c.get("emails"):
        lines.append(f"- **Email:** {', '.join(c['emails'])}")
    if c.get("website"):
        lines.append(f"- **Website:** {c['website']}")
    return "\n".join(lines)


def h_payment(t, q):
    office = _office(t)
    return ("Payment records (failed or pending payments, receipts, refunds) aren't in the CHRIST programme database I read, so I can't check or fix a payment. "
            "For transaction verification, refunds or an amount debited without confirmation, please contact the CHRIST Admissions Office with your transaction reference.\n\n"
            + (office + "\n\n" if office else "") + "_I will never ask for your PIN, CVV, OTP or password._")


def h_contact(t, q):
    campus = _campus_named(q.text, get_catalogue())
    out = t.run("get_campus_details", campus=campus)
    if (e := _err(out)):
        return e
    rows = out.data["campuses"]
    t.followups = ["Which programmes would suit me?", "How do I apply?"]
    return ("**Contact details**" + (f" · {rows[0]['name']}" if len(rows) == 1 else "") + "\n\n" + "\n\n".join(_contact_block(c, len(rows) > 1) for c in rows) +
            "\n\n_As recorded in the CHRIST database. I can't create support tickets or book callbacks myself._")


def h_offer(t, q):
    body = _process(t, q, "admission_dates", "Results and offers")
    return body + "\n\n_For the status of your own application or offer, please use the application status page or contact the Admissions Office. I can't see individual applications._"


def h_application_status(t, q):
    """The data holds only public programme information, never applicant records, so say that plainly and point to the real channels."""
    portal = None
    for r in t.run("get_admission_process").data.get("processes", []):
        m = re.search(r"https?://\S*appstat\S*", r.get("admission_dates") or "")
        if m:
            portal = m.group(0).rstrip(".,;)")
            break
    office = _office(t)
    t.followups = ["How do I apply?", "Which programmes would suit me?"]
    return ("I can't see individual applications. I only have CHRIST's public programme information, not applicant records, so I can't tell you your application status, "
            "whether you've been shortlisted or selected, or the state of a payment.\n\n"
            + (f"Your status is published on the application status page: {portal}\n\n" if portal else "") + (office + "\n\n" if office else "")
            + "_Please never share passwords, OTPs or card details with me._")


def h_hostel(t, q):
    campus = _campus_named(q.text, get_catalogue())
    out = t.run("get_campus_facilities", campus=campus, facility="hostel")
    t.followups = ["Which campuses does CHRIST have?", "Which programmes would suit me?"]
    if _err(out):
        office = _office(t)
        where = f" for {campus}" if campus else ""
        return f"Hostel and accommodation details{where} aren't available to me right now. " + (office if office else CONTACT_HINT)
    return "**Hostel and accommodation**\n\n" + _facility_blocks(out.data["facilities"])


def _facility_blocks(rows: list[dict]) -> str:
    out = []
    for r in rows:
        out.append(f"#### {r['campus']} · {r['facility']}\n\n" + tidy(r["text"]) + (f"\n\nMore: {r['link']}" if r.get("link") else ""))
    return "\n\n".join(out)


FACILITY_WORDS = re.compile(r"\b(facilit\w*|library|libraries|transport\w*|bus|labs?|laborator\w*|auditorium|sports?|canteen|cafeteria|medical|infrastructure|wi-?fi|it services|research facilities)\b", re.I)


def h_scholarship(t, q):
    if (m := need_programme(t, q, "scholarships", "scholarship information")):
        return m
    p = q.res.best
    out = t.run("get_programme_section", programme_id=p.id, section="scholarship")
    if _err(out):
        return _missing(f"Scholarship information for **{p.name}**")
    return f"**Scholarships — {p.name}**\n\n{_sections(out.data['sections'], 2)}" + other_campuses(q)


def h_placements(t, q):
    if (m := need_programme(t, q, "placements", "placement information")):
        return m
    p = q.res.best
    out = t.run("get_programme_section", programme_id=p.id, section="placements")
    careers = [h for h in t.run("get_programme_highlights", programme_id=p.id).data.get("highlights", []) if h["kind"] == "careers"]
    if _err(out) and not careers:
        return _missing(f"Placement information for **{p.name}**")
    parts = [f"**Placements and careers — {p.name}**"]
    if not _err(out):
        parts.append(_sections(out.data["sections"], 2))
    if careers:
        parts.append("#### Career paths\n\n" + "\n".join(f"- **{h['title']}**" + (f": {h['text']}" if h["text"] else "") for h in careers[:8]))
    return "\n\n".join(parts) + other_campuses(q)


def h_campus(t, q):
    cat = get_catalogue()
    campus = _campus_named(q.text, cat)
    if FACILITY_WORDS.search(q.text):
        word = FACILITY_WORDS.search(q.text).group(0)
        generic = word.lower().startswith(("facilit", "infrastructure"))
        out = t.run("get_campus_facilities", campus=campus, facility=None if generic else word)
        if not _err(out):
            t.followups = ["How can I contact the campus?", "Which programmes would suit me?"]
            return "**Campus facilities**\n\n" + _facility_blocks(out.data["facilities"])
        facilities_note = f"\n\n_{_err(out)}_"
    else:
        facilities_note = ""
    out = t.run("get_campus_details", campus=campus)
    if (e := _err(out)):
        return e
    rows = out.data["campuses"]
    t.followups = ["How can I contact the Admissions Office?", "Tell me about hostel facilities", "Which programmes would suit me?"]
    return ("**CHRIST campuses**\n\n" + "\n\n".join(f"#### {c['name']}\n" + "\n".join(x for x in (f"- {c['address']}" if c.get("address") else "",
            f"- Website: {c['website']}" if c.get("website") else "") if x) for c in rows) + facilities_note +
            "\n\n_Ask me for a campus's phone and email, its hostel or other facilities, or the programmes offered there._")


def _campus_named(text: str, cat) -> str | None:
    words = set(re.findall(r"[a-z]+", text.lower()))
    for c in cat.campuses():
        if {w for w in re.findall(r"[a-z]+", c.lower()) if w not in ("bangalore", "campus")} & words:
            return c
    return None


def _profile_questions(t: Turn, q: Req, cat, level: str | None = None, campus: str | None = None, has_interest: bool = False) -> str:
    """No usable match yet: ask ONLY for what is still missing, and never repeat a question that was already asked. Nothing was looked up,
    so no source is attached."""
    t.sources.clear()
    t.followups = [f"I'm interested in {d}" for d in cat.departments(5)] + ["I'm not sure what to study"]
    asked = any("glad to help you choose" in m["content"] or "couldn't match" in m["content"] for m in q.history if m["role"] == "assistant")
    todo = []
    if not has_interest:
        todo.append("What subjects or fields do you enjoy, or what would you like to become? (for example *computers*, *AI*, *business*, *law*)" if not asked
                    else "Try a field or subject word (for example *computer science*, *data science*, *commerce*) or the programme's full name.")
    if not level:
        todo.append("Are you looking at an **undergraduate**, **postgraduate** or **PhD** programme?")
    if not campus:
        todo.append("Do you prefer a particular campus?")
    head = ("I couldn't match that to a CHRIST programme yet." if asked else "I'd be glad to help you choose.")
    return head + (" To narrow it down:\n\n" + "\n".join(f"- {x}" for x in todo) if todo else " Tell me a little more about what you enjoy.") + \
        "\n\nAnswer in your own words, even just one of these."


def h_recommend(t, q):
    """Recommendation agent (decider mode): its own tool set, no facts like fees or dates. Interests accumulate over the whole chat.
    The suggestions go out as structured cards (the UI renders them with action buttons); the text is only the introduction."""
    t.agent = "recommend"
    cat = get_catalogue()
    latest = q.history[-1]["content"]
    mine = " ".join(m["content"] for m in q.history if m["role"] == "user")[-1500:]
    level = q.u.level if q.u.level in ("undergraduate", "postgraduate", "doctoral") else (detect_level(latest) or detect_level(mine))
    campus = _campus_named(latest, cat) or _campus_named(mine, cat)
    said_before = " ".join(m["content"] for m in q.history if m["role"] == "assistant")
    shown = [p.name for p in cat.programmes if p.name in said_before]            # "show me more" must not repeat what was already suggested
    if current_catalog() is not None:          # blueprint ranking: programme content (BM25), eligibility filter, saved profile
        out = t.run("rank_programs", interests=mine, level={"undergraduate": "UG", "postgraduate": "PG", "doctoral": "PhD"}.get(level), campus=campus,
                    exclude_names=shown or None)
        recs = [{"programme_id": r["programme_id"], "name": r["name"], "level": r["level"].lower(), "department": r["department"], "duration_years": r["duration_years"],
                 "campuses": r["campuses"], "matched_interests": r["matched_words"], "career_paths": r["career_paths"], "eligibility": r["eligibility"]}
                for r in out.data.get("recommendations", [])]
    else:
        out = t.run("recommend_programmes", interests=mine, level=level, campus=campus, exclude_names=shown or None)
        recs = out.data.get("recommendations")
    if not recs and shown and cat.interest_tokens(mine):
        t.followups = [f"I'm interested in {d}" for d in cat.departments(4)]
        return "Those are all the programmes I found for what you've told me so far. Tell me about another interest or field and I'll look again."
    if not recs:                                # last resort: the latest message as a literal programme-name search
        found = (t.run("search_programmes", query=latest, level=level, campus=campus).data.get("programmes") or []) if cat._units(latest) else []
        if 0 < len(found) <= 12:
            t.followups = [f"Tell me about {found[0]['name']}", "Which programmes would suit me?"]
            return ("**Closest programmes by name**\n\n" + _table(["Programme", "Level", "Campus"], [[p["name"], p["level"].title(), _na(p["campus"])] for p in found[:8]]))
        return _profile_questions(t, q, cat, level, campus, has_interest=False)   # nothing matched, so a field is still needed
    t.cards = recs
    seen_words = list(dict.fromkeys(w for r in recs for w in r["matched_interests"]))[:4]
    echo = ", ".join(x for x in (level, campus) if x)
    t.followups = [f"Tell me about {recs[0]['name']}", f"What is the eligibility for {recs[0]['name']}?", "Show me more options"]
    return ("**Programmes that may suit you**\n\n"
            f"Based on what you've told me (matching: {', '.join(seen_words)}{'; ' + echo if echo else ''}), these CHRIST programmes look like the best fit. "
            "Open a card to see its eligibility or curriculum, or tell me more about what you like and I'll refine the list.\n\n"
            "_These are suggestions made by matching your interests to programme content in the CHRIST catalog. They are not admission advice; "
            "the Admissions Office decides eligibility._")


def h_unavailable(what: str):
    def fn(t, q):
        return _missing(what)
    return fn


def h_guarantee(t, q):
    return ("I can't guarantee or promise admission, a scholarship, a seat or a job, and I don't make admission decisions: the CHRIST Admissions Office does. "
            "I can share the published criteria and give you a *preliminary* indication.")


def h_other(t, q):
    return "I can't share anyone's application or personal information, and I don't have access to applicant records. I can help with programmes, eligibility, fees and the admission process."


def h_off(t, q):
    return f"I can only help with admissions at **{UNI}**: programmes, eligibility, fees, curriculum, selection, applying and campuses."


HANDLERS = {
    "greeting": h_greeting, "recommend_programmes": h_recommend, "programmes_overview": h_programmes, "programme_details": h_details, "curriculum": h_curriculum,
    "compare_programmes": h_compare, "eligibility_criteria": lambda t, q: h_eligibility(t, q, False), "eligibility_check": lambda t, q: h_eligibility(t, q, True),
    "dates": h_dates, "commencement": h_unavailable("Class start dates and the academic calendar"), "application_help": h_apply, "payment_help": h_payment,
    "application_fee": lambda t, q: h_fees(t, q, True), "programme_fees": lambda t, q: h_fees(t, q, False), "entrance_exam": h_selection,
    "offer_joining": h_offer, "documents": h_unavailable("A required-documents checklist"),
    "scholarships": h_scholarship, "hostel": h_hostel, "campus": h_campus,
    "placements": h_placements, "international": h_unavailable("Guidance for international and NRI applicants (beyond the fee categories in a programme's fee table)"),
    "contact_support": h_contact, "about_university": h_unavailable("General information about the university (history, rankings, accreditation)"),
    "application_status": h_application_status,
    "guarantee_request": h_guarantee, "other_person_data": h_other, "off_topic": h_off,
}
PROG_INTENTS = {"programme_details", "curriculum", "eligibility_criteria", "eligibility_check", "dates", "application_fee", "programme_fees", "entrance_exam",
                "scholarships", "placements", "application_help", "offer_joining"}
STICKY_RECOMMEND = {"off_topic", "programmes_overview", "unclear", "programme_details", "eligibility_check"}
LOW_CONFIDENCE = ("I'm not sure I understood that. Could you rephrase, or pick a topic?\n\n"
                  "- Programmes and curriculum\n- Eligibility\n- Application dates\n- Fees\n- Applying and selection")


# ----------------------------------------------------------------------------- entry point
async def run_decision_agent(decider: DecisionsClient, history: list[dict], *, composer=None, last_agent: str | None = None, profile=None) -> AgentResult:
    t = Turn(ToolContext(profile=profile))
    answers = await decider.decide(build_state(history), build_questions())
    u = understand(answers)
    # While the applicant is answering the recommender's questions ("I like computers", "undergraduate") the recommender keeps the
    # conversation, unless they now ask for a specific fact, which goes to the information agent.
    if last_agent == "recommend" and u.intent in STICKY_RECOMMEND and not INFO_RX.search(history[-1]["content"]):
        u.intent, u.confidence = "recommend_programmes", 1.0

    if u.confidence < CONF_MIN and u.intent not in ("greeting", "guarantee_request", "other_person_data"):
        return AgentResult(LOW_CONFIDENCE, [], t.used, DEFAULT_SUGGEST, intent="unclear", agent=last_agent or "info")

    try:
        res = resolve_programme(history) if (u.intent in PROG_INTENTS or u.intent == "compare_programmes") else None
        q = Req(history[-1]["content"], history, u, res)
        reply = HANDLERS[u.intent](t, q)
    except SourceUnavailable:
        return AgentResult("The CHRIST programme database cannot be reached right now, so I can't look that up. Please try again shortly, or contact the Admissions Office.",
                           [], t.used, [], intent="data_unavailable")
    follow = t.followups or ([x.format(n=res.best.name) for x in NEXT[:3]] if res and res.best and not res.ambiguous else GENERIC.get(u.intent, DEFAULT_SUGGEST))
    final = _finish(reply)
    if composer is not None:
        from .composer import should_compose
        if should_compose(u.intent, final, bool(res and res.ambiguous)):
            nice = await composer.compose(history[-1]["content"], history, final, u.intent)
            if nice:
                final = nice
    return AgentResult(final, list(t.sources.values()), t.used, follow, intent=u.intent, agent=t.agent, cards=t.cards)

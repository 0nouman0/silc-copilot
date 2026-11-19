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

from ..applicants.adapter import Application
from ..catalogue import Programme, Resolution, get_catalogue
from ..llm.decisions import DecisionsClient
from ..models import SourceRef
from ..sources import SourceUnavailable
from ..sources.text import tidy
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
    c = get_catalogue()
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
    return (f"Hello! I'm the unofficial admissions assistant for **{UNI}**. I can help with programmes, eligibility, fees, curriculum, selection, "
            "the application process and campuses. What would you like to know?")


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
            ["Duration", _na(_dur(d.get("duration_years")))], ["Term type", _na(d.get("term_type"))],
            ["Approved intake", _na(d.get("approved_intake"))]]


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
    rows = _facts(d) + [["Recorded application window", f"{_fmt_date(d.get('application_open'))} → {_fmt_date(d.get('application_close'))}"]]
    t.followups = [x.format(n=p.name) for x in NEXT]
    note = "\n\n_Application dates are as recorded in the database and may be out of date._"
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
    res = t.run("assess_preliminary_eligibility", programme_id=p.id, percentage=pcts[-1] if pcts else None).data["result"]
    return (f"**{res['label']}** — {p.name}\n\n{res['detail']}\n\n" + body + f"\n\n_{res['disclaimer']}_")


STATUS_LABEL = {"open": "Open", "upcoming": "Opens soon", "closed": "Closed", "unknown": "—"}
DATES_CAVEAT = "_These are the dates recorded in the CHRIST database and they may be out of date. Please confirm the current dates with the Admissions Office._"


def h_dates(t, q):
    res = q.res
    if res and res.best and not res.ambiguous:
        p = res.best
        t.run("get_admission_dates", programme_id=p.id)
        t.followups = [x.format(n=p.name) for x in NEXT if "Application dates" not in x]
        rows = [[_label(x), _fmt_date(x.application_open), _fmt_date(x.application_close), STATUS_LABEL[x.status]] for x in res.candidates]
        return f"**Application dates — {p.name}**\n\n" + _table(["Campus", "Opens", "Closes", "Status"], rows) + "\n\n" + DATES_CAVEAT
    if res and res.candidates:  # several different programmes match: ask which
        return need_programme(t, q, "dates", "application dates")
    out = t.run("get_admission_dates")
    w = out.data["windows"]
    if not w:
        return f"No application dates are recorded in the database. {CONTACT_HINT}"
    current = sorted((x for x in w if x["status"] in ("open", "upcoming")), key=lambda x: (x["close"] or "9", x["level"]))
    if current:
        rows = [[x["level"].title(), _na(x["campus"]), _fmt_date(x["open"]), _fmt_date(x["close"]), STATUS_LABEL[x["status"]], str(x["programmes"])] for x in current]
        return ("**Application windows recorded in the database** · open or upcoming\n\n" + _table(["Level", "Campus", "Opens", "Closes", "Status", "Programmes"], rows[:30]) +
                "\n\nName a programme (for example *MBA*) for its exact dates.\n\n" + DATES_CAVEAT)
    latest = sorted(w, key=lambda x: x["close"] or "", reverse=True)[:6]
    rows = [[x["level"].title(), _na(x["campus"]), _fmt_date(x["open"]), _fmt_date(x["close"]), str(x["programmes"])] for x in latest]
    return ("**No open application window is recorded in the database.** Every window stored there has already closed; the most recent ones are:\n\n" +
            _table(["Level", "Campus", "Opened", "Closed", "Programmes"], rows) + f"\n\nThe database may simply not have been updated for the current cycle. {CONTACT_HINT}")


def h_fees(t, q, fee_only=False):
    if (m := need_programme(t, q, "application_fee" if fee_only else "programme_fees", "fees")):
        return m
    p = q.res.best
    out = t.run("get_fee_structure", programme_id=p.id)
    t.followups = [x.format(n=p.name) for x in NEXT if "fees" not in x]
    if (e := _err(out)):
        return f"{e} {CONTACT_HINT}"
    rows = out.data["sections"]
    if fee_only:
        text = "\n".join(r["text"] for r in rows)
        lines = [ln for ln in text.split("\n") if re.search(r"registration fee|application fee|caution deposit|non-refundable", ln, re.I) and not ln.startswith("|")]
        if lines:
            return f"**Application / registration fee — {p.name}**\n\n" + "\n".join(f"- {ln.strip()}" for ln in lines) + other_campuses(q)
    return f"**Fees — {p.name}**{' · ' + p.campus if p.campus else ''} (as published in the CHRIST database)\n\n{_sections(rows, 2)}" + other_campuses(q)


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


def h_payment(t, q):
    return ("Payment details (failed or pending payments, receipts, refunds) aren't in the CHRIST programme database I read. "
            "For transaction verification, refunds or debited-but-unconfirmed payments, please contact the CHRIST Admissions Office / payment support. "
            "_I can't verify payments, and I will never ask for your PIN, CVV, OTP or password._")


def h_contact(t, q):
    t.followups = ["How do I apply?", "Show me the programmes available"]
    return (f"{_missing('Admissions phone numbers and email addresses')}\n\n_I can't create support tickets or book callbacks myself._")


def h_offer(t, q):
    if q.signed_in and q.active:
        return h_my_status(t, q)
    body = _process(t, q, "admission_dates", "Results and offers")
    return body + ("\n\n" + ("Select an application to see its status." if q.signed_in else "Sign in to see the status of your own application."))


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
    out = t.run("list_campuses")
    if _err(out):
        return _err(out)
    names = [c["name"] for c in out.data["campuses"]]
    return ("**CHRIST campuses** (from the CHRIST database)\n\n" + "\n".join(f"- {n}" for n in names) +
            "\n\nCampus facilities, library and transport details aren't stored in the database I read. " + CONTACT_HINT)


def h_unavailable(what: str):
    def fn(t, q):
        return _missing(what)
    return fn


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
    return f"I can only help with admissions at **{UNI}**: programmes, eligibility, fees, curriculum, selection, applying and campuses."


HANDLERS = {
    "greeting": h_greeting, "programmes_overview": h_programmes, "programme_details": h_details, "curriculum": h_curriculum,
    "compare_programmes": h_compare, "eligibility_criteria": lambda t, q: h_eligibility(t, q, False), "eligibility_check": lambda t, q: h_eligibility(t, q, True),
    "dates": h_dates, "commencement": h_unavailable("Class start dates and the academic calendar"), "application_help": h_apply, "payment_help": h_payment,
    "application_fee": lambda t, q: h_fees(t, q, True), "programme_fees": lambda t, q: h_fees(t, q, False), "entrance_exam": h_selection,
    "offer_joining": h_offer, "documents": h_unavailable("A required-documents checklist"),
    "scholarships": h_scholarship, "hostel": h_unavailable("Hostel and accommodation information"), "campus": h_campus,
    "placements": h_placements, "international": h_unavailable("Guidance for international and NRI applicants (beyond the fee categories in a programme's fee table)"),
    "contact_support": h_contact, "about_university": h_unavailable("General information about the university (history, rankings, accreditation)"),
    "my_applications": h_my_apps, "my_application_status": h_my_status,
    "guarantee_request": h_guarantee, "other_person_data": h_other, "off_topic": h_off,
}
PROG_INTENTS = {"programme_details", "curriculum", "eligibility_criteria", "eligibility_check", "dates", "application_fee", "programme_fees", "entrance_exam",
                "scholarships", "placements", "application_help", "offer_joining"}
LOW_CONFIDENCE = ("I'm not sure I understood that. Could you rephrase, or pick a topic?\n\n"
                  "- Programmes and curriculum\n- Eligibility\n- Application dates\n- Fees\n- Applying and selection")


# ----------------------------------------------------------------------------- entry point
async def run_decision_agent(decider: DecisionsClient, history: list[dict], *, user_id: str | None,
                             active_application: Application | None, composer=None) -> AgentResult:
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
    except SourceUnavailable:
        return AgentResult("The CHRIST programme database cannot be reached right now, so I can't look that up. Please try again shortly, or contact the Admissions Office.",
                           [], t.used, [], False, intent="data_unavailable")
    follow = t.followups or ([x.format(n=res.best.name) for x in NEXT[:3]] if res and res.best and not res.ambiguous else GENERIC.get(u.intent, DEFAULT_SUGGEST))
    final = _finish(reply)
    if composer is not None:
        from .composer import should_compose
        if should_compose(u.intent, final, bool(res and res.ambiguous)):
            nice = await composer.compose(history[-1]["content"], history, final, u.intent)
            if nice:
                final = nice
    return AgentResult(final, list(t.sources.values()), t.used, follow, t.demo, intent=u.intent)

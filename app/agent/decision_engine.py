"""Decision-driven agent for typed 'decider' models (no free-text generation).

Flow per message:
  1. ONE typed call: the decider classifies intent, programme, level, applicant
     category and a few applicant details. Answers are constrained to options we define.
  2. Code runs the relevant registry tool(s) (same validation / ownership checks
     as the tool-calling agent) and renders the answer from templates.
Nothing the model outputs can become user-visible text, and nothing is invented:
anything missing in the data is reported as unavailable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..applicants.adapter import Application
from ..data.models import SourceRef
from ..eligibility import ApplicantProfile, EligibilityCriteria, assess
from ..llm.decisions import DecisionsClient
from ..tools import ToolContext, ToolOutput, execute
from .orchestrator import AgentResult

CONF_MIN = 0.45  # below this we ask the user to rephrase instead of guessing

INTENTS = {
    "greeting": "Greeting or small talk such as hello, hi, thanks",
    "programmes_overview": "Asks which programmes/courses/degrees the university offers, optionally by level",
    "programme_details": "Asks about one programme: duration, modes, specializations, medium, accreditation, internships, research, careers",
    "curriculum": "Asks about syllabus, subjects, semester-wise curriculum, core or elective subjects, labs",
    "compare_programmes": "Asks to compare or differentiate two programmes",
    "eligibility_criteria": "Asks what the eligibility, qualification, minimum marks, age limit, work experience, entrance-score or required/compulsory subjects (e.g. is Mathematics compulsory) are for a programme",
    "eligibility_check": "Gives their own marks/qualification or asks whether THEY are eligible / can apply (final year, backlog, stream, other country)",
    "dates": "Asks about application open/close dates, deadlines, last date, result dates, admission calendar",
    "commencement": "Asks when classes/programme/semester start, orientation, reporting date, academic calendar",
    "application_help": "Asks how to apply, the application portal, registering, saving, submitting, what an application status term means in general (e.g. what does Under Review / Shortlisted mean), login/password/OTP/email problems, duplicate accounts",
    "payment_help": "Payment problems: failed payment, money debited, receipt, refund of application fee, payment pending",
    "application_fee": "Asks the amount of the application fee or fee waiver for applying",
    "programme_fees": "Asks tuition/programme fee, total fee, semester or annual fee, other charges, caution deposit, instalments, loans",
    "entrance_exam": "Asks about entrance exam, pattern, syllabus, admit card, centre, interview, merit list, selection process",
    "offer_joining": "Asks about admission offer letter, accepting an offer, confirmation fee, deferral, original documents, student ID",
    "documents": "Asks which documents/certificates are required, upload size/format, verification",
    "scholarships": "Asks about scholarships, concessions, financial assistance",
    "hostel": "Asks about hostel or accommodation, rooms, mess, hostel fees and rules",
    "campus": "Asks about campuses, location, facilities, library, labs, sports, transport, student life, campus visit",
    "placements": "Asks about placements, recruiters, salary packages, internships, career support",
    "international": "Asks about international applicants, foreign students, visa, passport, IELTS/TOEFL, equivalence",
    "contact_support": "Asks how to contact admissions, phone, email, counsellor, callback, support ticket, brochure",
    "my_applications": "Asks how many applications they have submitted or to list their own applications",
    "my_application_status": "Asks about the status or outcome of THEIR OWN specific application: have I been selected, is mine shortlisted/rejected, what should I do next (NOT a general question about what a status term means)",
    "guarantee_request": "Asks the assistant to guarantee or promise admission, a scholarship, a seat or a job, or to make the admission decision",
    "other_person_data": "Asks about another person's application, status, marks or personal data",
    "off_topic": "Unrelated to university admissions",
}

SUGGEST = {
    "programmes_overview": ["Tell me about a programme", "Help me check my eligibility"],
    "programme_details": ["What is the eligibility for this programme?", "What are the fees?"],
    "curriculum": ["What is the eligibility for this programme?", "What are the fees?"],
    "compare_programmes": ["What are the fees?", "Help me check my eligibility"],
    "eligibility_criteria": ["Help me check my eligibility", "Which documents do I need?"],
    "eligibility_check": ["Which documents do I need?", "What are the application deadlines?"],
    "dates": ["What is the application fee?", "How do I apply?"],
    "commencement": ["What are the application deadlines?", "What are the hostel facilities?"],
    "application_help": ["Which documents do I need?", "What are the application deadlines?"],
    "payment_help": ["How do I contact the Admissions Office?", "What is the application fee?"],
    "application_fee": ["Show me the programme fee structure", "Are scholarships available?"],
    "programme_fees": ["Are scholarships available?", "What are the hostel facilities?"],
    "entrance_exam": ["What are the application deadlines?", "Help me check my eligibility"],
    "documents": ["What are the application deadlines?", "How do I apply?"],
    "scholarships": ["Show me the programme fee structure", "How do I apply?"],
    "hostel": ["What are the campus facilities?", "Show me the programme fee structure"],
    "campus": ["What are the hostel facilities?", "Show me the programmes available"],
    "placements": ["Show me the programmes available", "How do I contact the Admissions Office?"],
    "international": ["Which documents do I need?", "What are the application deadlines?"],
    "my_applications": ["What is the status of my application?", "What documents are required?"],
    "my_application_status": ["What are the fees for this programme?", "What documents are required?"],
}
DEFAULT_SUGGEST = ["Show me the programmes available", "How do I contact the Admissions Office?"]

# ----------------------------------------------------------------------------- understanding
@dataclass
class Understanding:
    intent: str = "off_topic"
    confidence: float = 0.0
    programme: str | None = None
    programme_b: str | None = None
    level: str | None = None
    category: str | None = None
    qualification: str | None = None
    completion: str | None = None
    backlogs: str | None = None
    entrance: str | None = None
    foreign: str | None = None


def _choice(a: dict, key: str) -> tuple[str | None, float]:
    x = a.get(key) or {}
    return x.get("choice"), float(x.get("confidence") or 0.0)


def build_questions(programmes: list[dict]) -> dict[str, Any]:
    plist = {p["id"]: f'{p["name"]} ({p["level"]})' for p in programmes}
    prog = {**plist, "none": "No specific programme is mentioned or implied"}
    prog_b = {**plist, "none": "No second programme is mentioned"}
    q: dict[str, Any] = {
        "intent": {"type": "choice", "criteria": INTENTS,
                   "instructions": "What is the user's latest message mainly asking? Use the conversation for context."},
        "level": {"type": "choice", "instructions": "Which programme level does the user ask about?",
                  "criteria": {"undergraduate": "Undergraduate / bachelor / UG", "postgraduate": "Postgraduate / master / PG",
                               "doctoral": "Doctoral / PhD", "any": "Not specified"}},
        "category": {"type": "choice", "instructions": "Is the applicant international/foreign or domestic?",
                     "criteria": {"domestic": "Domestic / Indian applicant", "international": "International, foreign or from another country",
                                  "unspecified": "Not stated"}},
        "qualification": {"type": "choice", "instructions": "What is the user's own highest completed or ongoing qualification, if they state it?",
                          "criteria": {"class12": "Class 12 / high school / intermediate", "bachelor": "Bachelor's degree / graduation",
                                       "master": "Master's degree", "not_stated": "Not stated"}},
        "completion": {"type": "choice", "instructions": "Does the user say their qualification is finished or still in its final year?",
                       "criteria": {"completed": "Completed / passed out / have the degree", "final_year": "Currently in final year / results pending",
                                    "not_stated": "Not stated"}},
        "backlogs": {"type": "choice", "instructions": "Does the user mention backlogs or supplementary exams?",
                     "criteria": {"has_backlogs": "Says they have backlogs/arrears/supplementary exams", "no_backlogs": "Says they have no backlogs",
                                  "not_mentioned": "Not mentioned"}},
        "entrance": {"type": "choice", "instructions": "Does the user say whether they have taken the entrance exam?",
                     "criteria": {"taken": "Has taken it / has a score", "not_taken": "Has not taken it", "not_mentioned": "Not mentioned"}},
        "foreign": {"type": "choice", "instructions": "Was the user's qualification obtained outside India?",
                    "criteria": {"foreign": "Qualification from another country", "indian": "Indian qualification", "not_mentioned": "Not mentioned"}},
    }
    if programmes:
        q["programme"] = {"type": "choice", "criteria": prog,
                          "instructions": "Which single programme does the user's latest message refer to? Use earlier conversation and the active application programme if they say 'this programme' or 'my application'."}
        q["programme_b"] = {"type": "choice", "criteria": prog_b,
                            "instructions": "If the user compares two programmes, which is the SECOND one mentioned? Otherwise none."}
    return q


def build_state(history: list[dict], *, signed_in: bool, active: Application | None) -> list[str]:
    ctx = [f"Signed in: {'yes' if signed_in else 'no'}"]
    if active:
        ctx.append(f"Active application: {active.application_id}, programme_id={active.programme_id} ({active.programme_name})")
    lines = [f'{m["role"].upper()}: {m["content"][:300]}' for m in history[:-1][-6:]]
    return ["Context: " + "; ".join(ctx), "Conversation so far:\n" + ("\n".join(lines) or "(none)"),
            "Latest user message: " + history[-1]["content"][:1000]]


def understand(answers: dict[str, dict]) -> Understanding:
    u = Understanding()
    u.intent, u.confidence = _choice(answers, "intent")
    u.intent = u.intent if u.intent in INTENTS else "off_topic"
    for key in ("programme", "programme_b", "level", "category", "qualification", "completion", "backlogs", "entrance", "foreign"):
        v, _ = _choice(answers, key)
        setattr(u, key, v)
    return u


# ----------------------------------------------------------------------------- turn context
@dataclass
class Turn:
    ctx: ToolContext
    used: list[str] = field(default_factory=list)
    sources: dict[str, SourceRef] = field(default_factory=dict)
    demo: bool = False

    def run(self, tool: str, **args) -> ToolOutput:
        out = execute(tool, json.dumps({k: v for k, v in args.items() if v is not None}), self.ctx)
        if tool not in self.used:
            self.used.append(tool)
        self.demo = self.demo or bool(out.data.get("demo"))
        for s in out.sources:
            self.sources[f"{s.doc_id}|{s.title}"] = s
        return out


CONTACT_HINT = "Please contact the Admissions Office for confirmation."


def _err(out: ToolOutput) -> str | None:
    d = out.data
    if "error" in d:
        return d.get("message") or "This information cannot be retrieved right now."
    if d.get("found") is False:
        return d["message"]
    return None


def _money(n, cur="") -> str:
    return "Not published" if n is None else f"{cur} {n:,.0f}".strip()


def _months(m) -> str:
    return "Not available" if m is None else (f"{m // 12} years" if m % 12 == 0 else f"{m} months")


def _na(v) -> str:
    return "Not available" if v in (None, "", []) else str(v)


def _fmt_date(s: str | None) -> str:
    if not s:
        return "Not confirmed"
    try:
        d = date.fromisoformat(s)
        return f"{d.day} {d.strftime('%B %Y')}"
    except ValueError:
        return s


def _table(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda x: str(x).replace("|", "\\|")
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers),
                      *["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]])


# ----------------------------------------------------------------------------- handlers
def ask_programme(t: Turn, what: str) -> str:
    out = t.run("search_programmes")
    names = "\n".join(f"- {p['name']}" for p in out.data.get("programmes", []))
    return f"Which programme would you like {what} for? Available programmes:\n\n{names}" if names else \
        f"Which programme would you like {what} for?"


def h_greeting(t, u, h, **_):
    return ("Hello! I'm the SILC Student Applicant Copilot. I can help with programmes, eligibility, dates, fees, applications, "
            "scholarships, hostel, campus, placements and international admission. What would you like to know?")


def h_programmes_overview(t, u, h, **_):
    out = t.run("search_programmes", level=u.level if u.level in ("undergraduate", "postgraduate", "doctoral") else None)
    if (e := _err(out)):
        return e
    ps = out.data["programmes"]
    if not ps:
        return "I couldn't find any programmes matching that. Try another level, or ask me to show all programmes."
    return "Here are the programmes available:\n\n" + _table(
        ["Programme", "Level", "Duration"], [[p["name"], p["level"].title(), _months(p["duration_months"])] for p in ps])


def _details(t, pid):
    return t.run("get_programme_details", programme_id=pid)


def h_programme_details(t, u, h, pid=None, **_):
    if not pid:
        return ask_programme(t, "details")
    out = _details(t, pid)
    if (e := _err(out)):
        return e
    p = out.data["programme"]
    rows = [["Level", p["level"].title()], ["Duration", _months(p["duration_months"])],
            ["Modes", ", ".join(p["modes"]) or "Not available"], ["Medium of instruction", _na(p["medium_of_instruction"])],
            ["Accreditation / approval", _na(p["accreditation"])],
            ["Specializations", ", ".join(p["specializations"]) or "None listed"], ["Internship", _na(p["internship"])],
            ["Research component", "Not available" if p["research_component"] is None else ("Yes" if p["research_component"] else "No")],
            ["Career paths", ", ".join(p["career_paths"]) if p["career_paths"] else "Not available"]]
    return f"**{p['name']}** — {p['summary']}\n\n" + _table(["Item", "Detail"], rows)


def h_curriculum(t, u, h, pid=None, **_):
    if not pid:
        return ask_programme(t, "the curriculum")
    out = _details(t, pid)
    if (e := _err(out)):
        return e
    p = out.data["programme"]
    if not p["curriculum"]:
        return f"A curriculum for **{p['name']}** is not available in the current knowledge base. {CONTACT_HINT}"
    parts = [f"**{p['name']} — curriculum (as published)**"]
    for s in p["curriculum"]:
        parts.append(f"**Semester {s['semester']}**\n" + "\n".join(f"- {i['name']} ({i['type']})" for i in s["items"]))
    return "\n\n".join(parts)


def h_compare(t, u, h, pid=None, pid_b=None, **_):
    if not pid or not pid_b or pid == pid_b:
        names = "\n".join(f"- {x['name']}" for x in t.run("search_programmes").data.get("programmes", []))
        return "Which two programmes would you like to compare?" + (f"\n\n{names}" if names else "")
    a, b = _details(t, pid), _details(t, pid_b)
    if (e := _err(a) or _err(b)):
        return e
    pa, pb = a.data["programme"], b.data["programme"]
    row = lambda k, f: [k, f(pa), f(pb)]
    return f"**{pa['name']} vs {pb['name']}**\n\n" + _table(["", pa["name"], pb["name"]], [
        row("Level", lambda p: p["level"].title()), row("Duration", lambda p: _months(p["duration_months"])),
        row("Modes", lambda p: ", ".join(p["modes"]) or "Not available"),
        row("Specializations", lambda p: ", ".join(p["specializations"]) or "None listed"),
        row("Internship", lambda p: _na(p["internship"])),
        row("Research component", lambda p: "Not available" if p["research_component"] is None else ("Yes" if p["research_component"] else "No"))])


def _criteria_lines(c: dict) -> list[str]:
    ex = c["entrance_exam"]
    lines = [f"- **Minimum qualification:** {_na(c['min_qualification_text'])}",
             f"- **Minimum percentage:** {'Not published' if c['min_percentage'] is None else str(c['min_percentage']) + '%'}",
             f"- **Required subjects:** {', '.join(c['required_subjects']) or 'None listed'}",
             f"- **Entrance examination:** " + ("Not published" if ex is None else
                                                 ("Not required" if not ex["required"] else
                                                  f"Required{': ' + ex['name'] if ex.get('name') else ''}"
                                                  f"{' (minimum score ' + str(ex['min_score']) + ')' if ex.get('min_score') is not None else ''}"))]
    return lines


def h_eligibility_criteria(t, u, h, pid=None, **_):
    if not pid:
        return ask_programme(t, "eligibility criteria")
    out = t.run("get_eligibility_criteria", programme_id=pid)
    if (e := _err(out)):
        return e + f" {CONTACT_HINT}"
    c = out.data["criteria"]
    return f"**Published eligibility criteria** (academic year {c['academic_year']}):\n\n" + "\n".join(_criteria_lines(c)) + \
        "\n\nI can compare your own details against these — just share your qualification, stream, marks and year of completion."


def _pct(texts: list[str], asked_pct: bool) -> float | None:
    found = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b|per cent\b|percentage\b)", " ".join(texts), re.I)
    val = float(found[-1]) if found else None
    if val is None and asked_pct and (m := re.fullmatch(r"\s*(\d{1,3}(?:\.\d+)?)\s*", texts[-1])):
        val = float(m.group(1))
    return val if val is not None and 0 <= val <= 100 else None


_SUBJECTS = {"mathematics": "Mathematics", "maths": "Mathematics", "math": "Mathematics", "physics": "Physics", "chemistry": "Chemistry",
             "biology": "Biology", "statistics": "Statistics", "english": "English", "computer science": "Computer Science",
             "economics": "Economics", "accountancy": "Accountancy"}


def extract_profile(user_texts: list[str], u: Understanding, asked_pct: bool) -> ApplicantProfile:
    blob = " ".join(user_texts)
    low = blob.lower()
    subs = sorted({v for k, v in _SUBJECTS.items() if re.search(rf"\b{re.escape(k)}\b", low)})
    stream = next((s.title() for s in ("science", "commerce", "arts", "humanities") if re.search(rf"\b{s}\b", low)), None)
    score = re.search(r"(?:entrance|aptitude|exam|test|\bmat\b)\D{0,25}?(\d{1,3}(?:\.\d+)?)(?!\s*(?:%|percent))", blob, re.I)
    return ApplicantProfile(
        highest_qualification=u.qualification if u.qualification in ("class12", "bachelor", "master") else None,
        stream=stream, subjects=subs or None, percentage=_pct(user_texts, asked_pct),
        completion_status=u.completion if u.completion in ("completed", "final_year") else None,
        has_backlogs={"has_backlogs": True, "no_backlogs": False}.get(u.backlogs or ""),
        entrance_taken={"taken": True, "not_taken": False}.get(u.entrance or "") if not score else True,
        entrance_score=float(score.group(1)) if score else None,
        qualification_country={"foreign": "Foreign", "indian": "India"}.get(u.foreign or ""))


def h_eligibility_check(t, u, h, pid=None, user_texts=(), asked_pct=False, **_):
    if not pid:
        return ask_programme(t, "the eligibility check")
    out = t.run("get_eligibility_criteria", programme_id=pid)
    crit = EligibilityCriteria(**out.data["criteria"]) if "criteria" in out.data else None
    profile = extract_profile(list(user_texts), u, asked_pct)
    res = assess(crit, profile)
    missing = [c for c in res.checks if c.status == "missing_input"]
    name = t.run("get_programme_details", programme_id=pid).data.get("programme", {}).get("name", pid)
    if res.outcome == "unable_to_assess" and missing:
        need = "\n".join(f"- **{c.criterion}** — {c.detail.split('.')[0]}." for c in missing)
        return (f"To check **{name}** against its published criteria I still need:\n\n{need}\n\n"
                "Share what you know (for example: *Bachelor's in Commerce, 62%, completed*). "
                "I'll give a preliminary indication only; the Admissions Office makes the official decision.")
    sym = {"met": "Matched", "not_met": "Not matched", "missing_input": "More information needed",
           "criteria_unavailable": "Criteria not published", "needs_verification": "Needs official verification"}
    t.run("assess_preliminary_eligibility", programme_id=pid,
          **{k: v for k, v in profile.model_dump().items() if v is not None})  # records the audited tool use + source
    return (f"**{name}: {res.outcome_label}**\n\n" + _table(["Criterion", "Result", "Detail"], [[c.criterion, sym[c.status], c.detail] for c in res.checks])
            + f"\n\n_{res.disclaimer}_")


def h_dates(t, u, h, pid=None, category=None, only=None, **_):
    out = t.run("get_admission_dates", programme_id=pid, applicant_type=category if category in ("domestic", "international") else None)
    if (e := _err(out)):
        return e
    rows = [d for d in out.data["dates"] if not only or re.search(only, d["label"], re.I)]
    if not rows:
        return f"No published dates were found for that in the current knowledge base. {CONTACT_HINT}"
    rows.sort(key=lambda d: d["date"] or "9999")
    return "**Important dates**\n\n" + _table(["Event", "Date", "Applies to", "Year"], [
        [d["label"], _fmt_date(d["date"]) if d["status"] == "published" else "Not confirmed yet",
         (d["programme_id"] or "All programmes") + ("" if d["applicant_type"] == "all" else f" ({d['applicant_type']})"), d["academic_year"]]
        for d in rows]) + "\n\nDates marked *Not confirmed yet* have not been published."


def h_commencement(t, u, h, **kw):
    r = h_dates(t, u, h, only=r"classes|commence|orientation|reporting|semester", **kw)
    return r if "Event" in r else "Programme commencement, orientation and academic-calendar dates are not available in the current knowledge base. " + CONTACT_HINT


def _kb(t, query, category=None):
    out = t.run("search_knowledge_base", query=query, category=category)
    if _err(out):
        return None
    return "\n\n".join(f"**{p['section']}**\n{p['text']}" for p in out.data["passages"][:2])


def h_application_help(t, u, h, query="", **_):
    body = _kb(t, query, None)
    note = ("\n\n_I can explain the process, but I can't submit, edit or withdraw an application for you._")
    return (body + note) if body else ("I don't have specific guidance on that in the current knowledge base, "
                                         "and I can't change or submit applications myself. Please contact the Admissions Office.")


def h_payment_help(t, u, h, query="", **_):
    body = _kb(t, query, "payments")
    return (body if body else "I don't have payment-specific guidance for that in the current knowledge base.") + \
        "\n\nFor transaction verification, refunds or debited-but-unconfirmed payments, please use the official admissions/payment support channel. " \
        "_I can't verify payments, and I will never ask for your PIN, CVV, OTP or password._"


def h_application_fee(t, u, h, pid=None, category=None, **_):
    if not pid:
        return ask_programme(t, "the application fee")
    cat = "international" if category == "international" else "domestic"
    out = t.run("get_fee_structure", programme_id=pid, applicant_category=cat)
    if (e := _err(out)):
        return e + f" {CONTACT_HINT}"
    f = out.data["fees"]
    return (f"The **application fee** for this programme ({cat} applicants, {f['academic_year']}) is **{_money(f['application_fee'], f['currency'])}**.\n\n"
            "Refundability, waivers and payment methods are not stated in the current knowledge base. " + CONTACT_HINT)


def h_programme_fees(t, u, h, pid=None, category=None, **_):
    if not pid:
        return ask_programme(t, "fees")
    cat = "international" if category == "international" else "domestic"
    out = t.run("get_fee_structure", programme_id=pid, applicant_category=cat)
    if (e := _err(out)):
        return e + f" {CONTACT_HINT}"
    f = out.data["fees"]
    rows = [["Application fee", _money(f["application_fee"], f["currency"]), "One-time", "—"]] + [
        [c["name"], _money(c["amount"], f["currency"]), c["frequency"],
         "Not stated" if c["refundable"] is None else ("Yes" if c["refundable"] else "No")] for c in f["components"]]
    note = "" if category in ("domestic", "international") else "\n\n_Showing domestic fees. Tell me if you are an international applicant._"
    return f"**Fee structure** — {cat.title()} applicants, academic year {f['academic_year']}\n\n" + \
        _table(["Item", "Amount", "Frequency", "Refundable"], rows) + note


def h_entrance(t, u, h, pid=None, **_):
    if not pid:
        return ask_programme(t, "entrance-exam details")
    out = t.run("get_eligibility_criteria", programme_id=pid)
    if (e := _err(out)):
        return e + f" {CONTACT_HINT}"
    ex = out.data["criteria"]["entrance_exam"]
    if ex is None:
        body = "Whether an entrance examination is required is not published for this programme."
    elif not ex["required"]:
        body = "No entrance examination is listed as required for this programme."
    else:
        body = f"An entrance examination is required: **{ex.get('name') or 'name not published'}**." + \
            (f" Minimum qualifying score: **{ex['min_score']}**." if ex.get("min_score") is not None else " The minimum score is not published.")
    d = h_dates(t, u, h, pid=pid, only=r"entrance|result")
    return body + "\n\nPattern, syllabus, admit card, centres, interview and merit-list details are not available in the current knowledge base. " + \
        CONTACT_HINT + ("\n\n" + d if "Event" in d else "")


def h_documents(t, u, h, level=None, category=None, **_):
    at = "international" if category == "international" else (level if level in ("undergraduate", "postgraduate", "doctoral") else None)
    out = t.run("get_required_documents", applicant_type=at)
    docs = out.data.get("documents", [])
    if not docs:
        return f"No document list is available for that in the current knowledge base. {CONTACT_HINT}"
    lines = "\n".join(f"- **{d['name']}** ({d['stage']}){'' if d['mandatory'] is not None else ' — requirement not confirmed'}"
                      + (f" — {d['notes']}" if d["notes"] else "") for d in docs)
    return f"**Documents checklist{' — ' + at if at else ''}**\n\n{lines}\n\nFile size and formats are shown on the portal's upload screen; they are not in my knowledge base."


def h_scholarships(t, u, h, level=None, **_):
    out = t.run("get_scholarships", level=level if level in ("undergraduate", "postgraduate", "doctoral") else None)
    if (e := _err(out)):
        return e
    parts = []
    for s in out.data["scholarships"]:
        parts.append(f"**{s['name']}**\n- Criteria: {s['criteria']}\n- Amount: {_na(s['amount_description'])}\n"
                     f"- Separate application: {'Not stated' if s['separate_application'] is None else ('Yes' if s['separate_application'] else 'No')}\n"
                     f"- Deadline: {_fmt_date(s['deadline']) if s['deadline'] else 'Not published'}")
    return "\n\n".join(parts) + "\n\nI can't tell you whether you qualify — eligibility depends on the published criteria and the Admissions Office."


def h_hostel(t, u, h, **_):
    out = t.run("get_hostel_information")
    if (e := _err(out)):
        return e + f" {CONTACT_HINT}"
    parts = []
    for x in out.data["hostels"]:
        rows = [[f"{r['type'].title()} sharing", _money(r["annual_fee"], r["currency"]) + " / year"] for r in x["room_types"]]
        parts.append(f"**Hostel — campus `{x['campus_id']}`**\n\n" + _table(["Room type", "Fee"], rows) +
                     f"\n\n- Facilities: {', '.join(x['facilities']) or 'Not available'}\n"
                     f"- Mess charges included: {'Not stated' if x['mess_charges_included'] is None else ('Yes' if x['mess_charges_included'] else 'No')}\n"
                     f"- Registration deadline: {_na(x['registration_deadline'])}\n"
                     f"- Accommodation guaranteed: {'Not stated' if x['availability_guaranteed'] is None else ('Yes' if x['availability_guaranteed'] else 'No')}")
    return "\n\n".join(parts)


def h_campus(t, u, h, **_):
    out = t.run("get_campus_information")
    cs = out.data.get("campuses", [])
    if not cs:
        return f"Campus information is not available. {CONTACT_HINT}"
    return "\n\n".join(f"**{c['name']}**\n- Location: {_na(c['location'])}\n- Facilities: {', '.join(c['facilities']) or 'Not available'}\n"
                       f"- Hostel: {'Not confirmed' if c['hostel_available'] is None else ('Available' if c['hostel_available'] else 'Not available')}" for c in cs)


def h_placements(t, u, h, pid=None, **_):
    out = t.run("get_placement_information", programme_id=pid)
    if (e := _err(out)):
        return "Placement statistics are not available in the current knowledge base, so I can't quote figures or recruiters. " + CONTACT_HINT
    return "\n\n".join(f"**{r['programme_id']} — {r['academic_year']} ({r['cohort']})**\n{r['summary']}" for r in out.data["reports"]) + \
        "\n\n_Historical figures are not guarantees of future outcomes._"


def h_international(t, u, h, pid=None, **_):
    parts = [h_documents(t, u, h, category="international"), h_dates(t, u, h, pid=pid, category="international")]
    if pid:
        parts.append(h_programme_fees(t, u, h, pid=pid, category="international"))
    return "\n\n".join(parts) + ("\n\nVisa assistance, English-test requirements, online interviews and airport pickup are not covered in the "
                                 "current knowledge base. Please contact the Admissions Office.")


def h_contact(t, u, h, **_):
    out = t.run("get_contact_information")
    c = out.data["contact"]
    rows = [[k, _na(c[f]) if c[f] else "Not provided yet"] for k, f in (("Phone", "phone"), ("Email", "email"), ("Hours", "hours"), ("Applicant portal", "portal_url"))]
    return f"**{c['office']}**\n\n" + _table(["Channel", "Details"], rows) + \
        "\n\nI can't create support tickets or book callbacks myself; use the official channels above once available."


SIGN_IN = "Please **sign in** (top right) so I can look up your own applications. I can't share personal application details otherwise."


def _apps(t):
    return t.run("get_my_applications")


def h_my_applications(t, u, h, signed_in=False, **_):
    if not signed_in:
        return SIGN_IN
    out = _apps(t)
    if (e := _err(out)):
        return e
    apps = out.data["applications"]
    if not apps:
        return "I don't see any applications associated with your account."
    return (f"I found **{len(apps)} application{'s' if len(apps) != 1 else ''}** associated with your account:\n\n" +
            _table(["Reference", "Programme", "Status", "Last updated"], [[a["application_id"], a["programme_name"], a["status"], _na(a["last_updated"])] for a in apps]) +
            "\n\nUse **Switch application** below the message box to pick one to discuss.")


def h_my_status(t, u, h, signed_in=False, active=None, **_):
    if not signed_in:
        return SIGN_IN
    app_id = active.application_id if active else None
    if not app_id:
        out = _apps(t)
        if (e := _err(out)):
            return e
        apps = out.data["applications"]
        if len(apps) != 1:
            return ("Which application would you like to discuss? " +
                    ("Use **Switch application** below the message box." if apps else "I don't see any applications on your account."))
        app_id = apps[0]["application_id"]
    out = t.run("get_application_status", application_id=app_id)
    if (e := _err(out)):
        return e
    a = out.data["application"]
    offer = a["offer_status"]
    return (f"**{a['programme_name']}** ({a['application_id']})\n\n- **Status:** {a['status']}\n"
            f"- **Offer status:** {offer if offer else 'No offer information has been returned for this application'}\n"
            f"- **Last updated:** {_na(a['last_updated'])}\n\n" +
            ("" if offer else f"_“{a['status']}” does not mean you have been selected or offered a seat._"))


def h_offer(t, u, h, signed_in=False, active=None, **kw):
    if signed_in and active:
        return h_my_status(t, u, h, signed_in=True, active=active)
    return ("Details about offer letters, acceptance deadlines, confirmation fees, deferral and joining formalities are not available in the current "
            "knowledge base. " + ("Select an application to see its offer status. " if signed_in else "Sign in to see the offer status of your own application. ") + CONTACT_HINT)


def h_guarantee(t, u, h, **_):
    return ("I can't guarantee or promise admission, a scholarship, a seat or a job, and I don't make admission decisions — the Admissions Office does. "
            "I can share the published criteria and give you a *preliminary* eligibility indication.")


def h_other_person(t, u, h, **_):
    return "I can't share another person's application or personal information. I can only discuss your own applications after you sign in."


def h_off_topic(t, u, h, **_):
    return "I can only help with university admissions: programmes, eligibility, dates, fees, applications, scholarships, hostel, campus and placements."


NEEDS_PROGRAMME_FALLBACK = {"programme_details", "curriculum", "eligibility_criteria", "eligibility_check", "application_fee",
                            "programme_fees", "entrance_exam"}
HANDLERS = {
    "greeting": h_greeting, "programmes_overview": h_programmes_overview, "programme_details": h_programme_details, "curriculum": h_curriculum,
    "compare_programmes": h_compare, "eligibility_criteria": h_eligibility_criteria, "eligibility_check": h_eligibility_check, "dates": h_dates,
    "commencement": h_commencement, "application_help": h_application_help, "payment_help": h_payment_help, "application_fee": h_application_fee,
    "programme_fees": h_programme_fees, "entrance_exam": h_entrance, "offer_joining": h_offer, "documents": h_documents,
    "scholarships": h_scholarships, "hostel": h_hostel, "campus": h_campus, "placements": h_placements, "international": h_international,
    "contact_support": h_contact, "my_applications": h_my_applications, "my_application_status": h_my_status,
    "guarantee_request": h_guarantee, "other_person_data": h_other_person, "off_topic": h_off_topic,
}
LOW_CONFIDENCE = ("I'm not sure I understood that. Could you rephrase, or pick one of these topics?\n\n"
                  "- Programmes and curriculum\n- Eligibility\n- Dates and deadlines\n- Fees and scholarships\n- Applying and documents")


# ----------------------------------------------------------------------------- entry point
async def run_decision_agent(decider: DecisionsClient, history: list[dict], *, user_id: str | None,
                             active_application: Application | None) -> AgentResult:
    ctx = ToolContext(user_id=user_id, active_application_id=active_application.application_id if active_application else None)
    t = Turn(ctx)
    plist = t.run("search_programmes").data.get("programmes", [])  # [] if the data source is unavailable
    answers = await decider.decide(build_state(history, signed_in=bool(user_id), active=active_application), build_questions(plist))
    u = understand(answers)

    if u.confidence < CONF_MIN and u.intent not in ("greeting", "guarantee_request", "other_person_data"):
        return AgentResult(LOW_CONFIDENCE, [], t.used, DEFAULT_SUGGEST, t.demo, intent="unclear")

    valid = {p["id"] for p in plist}
    pid = u.programme if u.programme in valid else None
    pid_b = u.programme_b if u.programme_b in valid else None
    if not pid and active_application and u.intent in NEEDS_PROGRAMME_FALLBACK | {"dates", "documents"}:
        pid = active_application.programme_id  # "this programme" with an active application

    user_texts = [m["content"] for m in history if m["role"] == "user"]
    prev_bot = next((m["content"] for m in reversed(history[:-1]) if m["role"] == "assistant"), "")
    reply = HANDLERS[u.intent](
        t, u, history, pid=pid, pid_b=pid_b, level=u.level, category=u.category, query=history[-1]["content"],
        signed_in=bool(user_id), active=active_application, user_texts=user_texts, asked_pct="percentage" in prev_bot.lower())
    return AgentResult(reply, list(t.sources.values()), t.used, SUGGEST.get(u.intent, DEFAULT_SUGGEST), t.demo, intent=u.intent)

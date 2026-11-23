"""Skills: short instruction bundles loaded only when needed, each with its own tool set, so prompts stay small and each skill is testable.

One orchestrator handles every turn. A light rule layer (`select_skill`) picks the skill for obvious cases and the previous skill
stays sticky while the applicant answers its questions. The pure-data module has no imports from the tool registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COMMON_LIVE = ("get_fees", "get_application_dates", "get_seats")


@dataclass(frozen=True)
class Skill:
    name: str
    agent: str                       # "info" | "recommend": the API-level agent this skill belongs to
    tools: frozenset[str]
    prompt: str


_RESOLVE = ("resolve_program", "search_programmes", "get_programme_details")

SKILLS: dict[str, Skill] = {s.name: s for s in [
    Skill("discover", "info", frozenset({*_RESOLVE, "search_programs", "get_campus_details"}),
          "SKILL: Discover. Help the applicant find programmes. Use search_programs with the level, campus, school or department they gave; if the request is "
          "too broad, ask the campus or level first. Show a short filtered list with links. Do not recommend or rank."),
    Skill("program_qa", "info", frozenset({*_RESOLVE, "get_programme_section", "get_specializations", "get_curriculum", "get_programme_highlights", "get_documents",
                                           "search_content", "compare_programs", "search_programs", "search_lateral_entry"}),
          "SKILL: Programme Q&A. First resolve the programme with resolve_program (ask which campus or level if several match), then answer ONLY from that "
          "programme's sections, specialisations, curriculum or documents, with the page link as the citation. A section with an expires_on date in the past "
          "or an academic_year older than the current cycle must be reported with that date, and the applicant told to confirm it."),
    Skill("apply_guide", "info", frozenset({"resolve_program", "get_programme_section", "get_admission_process", "get_campus_contacts", "get_application_dates", "search_content", "search_lateral_entry",
                                          "search_admission_process", "get_eligibility_criteria"}),
          "SKILL: Apply guide. Give a step-by-step answer from the programme's application / admission / selection / entrance-test sections or from "
          "get_admission_process, and point to the application portal named in the text. Dates come only from get_application_dates (live data): if it is "
          "unavailable, say so and give the campus admissions contact."),
    Skill("fees_deadlines", "info", frozenset({"resolve_program", "get_fees", "get_application_dates", "get_seats", "get_campus_contacts"}),
          "SKILL: Fees and deadlines. Fees, application dates, entrance-test dates and seats are LIVE data. Call get_fees / get_application_dates / get_seats "
          "and report the value with its source and as_of time. If available is false, say that this figure is not available right now and give the campus "
          "admissions contact. Never quote a fee, date or seat count from memory, from website text or from a brochure."),
    Skill("eligibility", "info", frozenset({"resolve_program", "get_eligibility_criteria", "get_programme_section", "check_eligibility", "search_content"}),
          "SKILL: Eligibility. Use check_eligibility for any verdict: it returns eligible, not_eligible, need_info (ask the ONE missing question it names) or "
          "cannot_decide (show the published criteria and the admissions contact, no verdict). You only explain its result. Always end a verdict with: "
          "'This is based on published criteria; the final admission decision is made by the admissions office.'"),
    Skill("recommend", "recommend", frozenset({"resolve_program", "rank_programs", "recommend_programmes", "check_eligibility", "compare_programs", "search_programmes",
                                               "get_programme_details", "get_programme_highlights", "get_campus_details"}),
          "SKILL: Recommend. Get to know the applicant (interests, level, campus, marks), then call rank_programs. It already filters by eligibility and never returns "
          "an ineligible programme. Present 3 to 5 with why each matches and its eligibility status (say 'not verified' when it is). Never predict admission chances "
          "or cutoffs. Fees only if asked, and only from get_fees."),
    Skill("campus_facilities", "info", frozenset({"get_topic_info", "get_campus_details", "get_campus_facilities", "get_campus_contacts", "get_hostel_vacancy", "search_content"}),
          "SKILL: Campus and facilities. Use get_topic_info for hostel, dining, scholarship, NRI, international, transport, medical, library and similar topics, per "
          "campus. Hostel vacancy is live data from get_hostel_vacancy; if unavailable, say so and point to the hostel office."),
    Skill("escalate", "info", frozenset({"get_campus_contacts", "create_handoff", "get_application_status"}),
          "SKILL: Escalate. This is a personal case (application status, payment, refund, documents, callback, complaint) that a person must handle. Be brief and kind: "
          "do not ask for application numbers, phone numbers or payment details. Call create_handoff with a short summary and give the right campus contact. "
          "Never claim you can see or change an application or a payment."),
]}

_RX = {
    "escalate": re.compile(r"\b(refund|cancel(?:lation)? (my )?(admission|seat|application)|complaint|complain|payment (failed|issue|problem|deducted|not)|deducted|charged twice|"
                           r"my application|application (number|no\.?|id|status)|my (status|result|offer)|callback|call me|call back|grievance|dispute|transaction)\b", re.I),
    "fees_deadlines": re.compile(r"\b(fees?|tuition|cost|how much|last date|deadline|dates?|seats?|intake|vacanc\w*|when does|when do)\b", re.I),
    "eligibility": re.compile(r"\b(eligib\w*|can i (apply|get|join|do|take)|am i eligible|qualif\w*|criteria|minimum (marks|percentage)|my (marks|percentage|cgpa|score))\b|\b\d{2}(\.\d+)?\s?%", re.I),
    "apply_guide": re.compile(r"\b(how (do|to|can) i apply|apply|application process|admission process|selection|entrance|audition|interview|documents? (required|needed)|register)\b", re.I),
    "campus_facilities": re.compile(r"\b(hostel|dining|mess|canteen|transport|bus|library|medical|scholarship|nri|international (student|admission|office)|anti[- ]?ragging|facilit\w*|sports|campus life)\b", re.I),
    "recommend": re.compile(r"\b(recommend|suggest|which (course|programme|program|degree)|suit(s|able)? me|best for me|help me (choose|pick|decide)|i('m| am) (interested|confused|not sure)|"
                            r"i (like|love|enjoy)|my (interest|passion)s?|not sure what|interested in|career (advice|guidance))\b", re.I),
    "program_qa": re.compile(r"\b(curriculum|syllabus|subjects?|specialis\w*|specializ\w*|structure|outcomes?|objectives?|placements?|brochure|compare|what will i (study|learn)|duration|about)\b", re.I),
    "discover": re.compile(r"\b(programmes?|programs?|courses?|degrees?|which .* do you (have|offer)|what .* (do you|are) (have|offer|available)|list|available)\b", re.I),
}
_ORDER = ("escalate", "campus_facilities", "fees_deadlines", "eligibility", "apply_guide", "recommend", "program_qa", "discover")


def select_skill(history: list[dict], last_skill: str | None = None) -> str:
    """Skill for the latest user message. Escalation and live-data words win; the recommender keeps the conversation while the applicant answers it."""
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    for name in _ORDER:
        if name == "recommend" and last_skill == "recommend" and not any(_RX[n].search(last) for n in ("escalate", "fees_deadlines", "eligibility", "apply_guide", "campus_facilities")):
            return "recommend"
        if _RX[name].search(last):
            return name
    return last_skill if last_skill in SKILLS else "program_qa"


def skill_for_agent(agent: str | None) -> str:
    return "recommend" if agent == "recommend" else "program_qa"

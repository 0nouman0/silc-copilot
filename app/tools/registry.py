"""Tool registry over the CHRIST programme database.

Small, focused tools with typed (pydantic) inputs. The model supplies parameters only; every query is predefined and
parameterised inside the `CatalogueSource`. Anything the database does not hold comes back as found=false, data the assistant
has not been granted access to comes back as such, and a database outage comes back as a structured error.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from .. import live
from ..agent.skills import SKILLS
from ..catalog.clean import looks_like_role
from ..catalog.refresh import current_catalog
from ..catalogue import Programme, get_catalogue
from ..eligibility_hint import hint
from ..llm.base import ToolDef
from ..models import SourceRef
from ..sources import DataNotGranted, SourceUnavailable, get_source

MAX_TEXT = 3500
DB_TITLE = "CHRIST programme database"


@dataclass
class ToolContext:
    """Per-request context for tools. The CHRIST data is public programme content, so the only per-request state is the applicant's
    own profile (marks, board, interests...) that the frontend resends every turn; the server stores nothing."""
    profile: Any = None


@dataclass
class ToolOutput:
    data: dict[str, Any]
    sources: list[SourceRef] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[Any, ToolContext], ToolOutput]


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args: type[BaseModel]):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, args, fn)
        return fn
    return deco


def _simplify(schema: Any) -> Any:
    if isinstance(schema, dict):
        if "anyOf" in schema:
            non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
            if len(non_null) == 1:
                return _simplify({**non_null[0], **{k: v for k, v in schema.items() if k != "anyOf"}})
        return {k: _simplify(v) for k, v in schema.items() if k not in ("title", "default")}
    if isinstance(schema, list):
        return [_simplify(v) for v in schema]
    return schema


# Two agents, two tool sets. The INFO agent only fetches and reports facts (it never recommends or ranks); the RECOMMEND agent
# suggests programmes from what the applicant tells it and can look up just enough to explain a suggestion (no fees, dates,
# eligibility text or other facts it could misquote: those belong to the info agent). `agent=None` means no restriction.
AGENTS = ("info", "recommend")
# Kept executable for the typed-decision engine and older clients, but never offered to a chat model (superseded by get_fees / get_application_dates).
HIDDEN_FROM_MODEL = frozenset({"get_fee_structure", "get_admission_dates"})
RECOMMEND_TOOLS = frozenset({"recommend_programmes", "rank_programs", "check_eligibility", "compare_programs", "resolve_program", "search_programmes",
                             "get_programme_details", "get_programme_highlights", "get_campus_details"})
INFO_EXCLUDED = frozenset({"recommend_programmes", "rank_programs"})


def _allowed(name: str, agent: str | None, skill: str | None = None) -> bool:
    if skill in SKILLS:
        return name in SKILLS[skill].tools
    if agent is None:
        return True
    if agent == "recommend":
        return name in RECOMMEND_TOOLS
    return name not in INFO_EXCLUDED


def available_tools(ctx: ToolContext, agent: str | None = None, skill: str | None = None) -> list[Tool]:
    return [t for t in REGISTRY.values() if _allowed(t.name, agent, skill) and t.name not in HIDDEN_FROM_MODEL]


def tool_defs(ctx: ToolContext, agent: str | None = None, skill: str | None = None) -> list[ToolDef]:
    return [ToolDef(t.name, t.description, _simplify(t.args.model_json_schema())) for t in available_tools(ctx, agent, skill)]


def execute(name: str, raw_args: str, ctx: ToolContext, agent: str | None = None, skill: str | None = None) -> ToolOutput:
    """Validate + run one tool. Never raises: failures become structured errors the caller must relay honestly."""
    t = REGISTRY.get(name)
    if t is None or not _allowed(name, agent, skill):
        return ToolOutput({"error": "tool_unavailable", "message": "This capability is not available."})
    try:
        parsed = t.args.model_validate(json.loads(raw_args or "{}"))
    except (ValidationError, json.JSONDecodeError) as e:
        return ToolOutput({"error": "invalid_arguments", "message": str(e)[:300]})
    try:
        return t.fn(parsed, ctx)
    except SourceUnavailable:
        return ToolOutput({"error": "catalogue_unavailable",
                           "message": "The CHRIST programme database cannot be reached right now. Say so; do not guess."})
    except DataNotGranted:
        return ToolOutput({"found": False, "message": "That information has not been made available to this assistant, so it cannot be answered here."})


# ------------------------------------------------------------------------------ helpers
def _db() -> Any:
    src = get_source()
    if src is None:
        raise SourceUnavailable("no catalogue source configured")
    return src


def _cut(text: str | None, limit: int = MAX_TEXT) -> str | None:
    if not text or len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n…(truncated)"


def _nf(what: str) -> ToolOutput:
    return ToolOutput({"found": False, "message": f"{what} is not available in the CHRIST programme database."})


def as_of() -> str | None:
    """When the catalog snapshot behind these answers was built (shown with every answer)."""
    c = current_catalog()
    return c.build.built_at.isoformat(timespec="minutes") if c else None


def _page_url(pid: int) -> str | None:
    c = current_catalog()
    p = c.programs.get(pid) if c else None
    return (p.url or None) if p else None


def _ref(p: Programme | None, label: str = "") -> SourceRef:
    if p is None:
        return SourceRef(doc_id="catalogue", title=DB_TITLE)
    campus = f" ({p.campus})" if p.campus else ""
    return SourceRef(doc_id=f"course:{p.id}" + (f":{label}" if label else ""), title=f"{DB_TITLE} — {p.name}{campus}" + (f" · {label}" if label else ""),
                     url=_page_url(p.id))


# Some programmes have admission requirements filed under "careers" in the database; those are not career paths.
_NOT_A_CAREER = re.compile(r"\b(candidates?|eligib\w*|minimum|marks|applicants?|preference|admission|qualif\w*|percentage|entrance|who|should|must|willing|passion|requirements?|basic understanding)\b", re.I)


def _brief(text: str, limit: int = 80) -> str:
    """First clause of a long stored sentence, for a compact list."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def _prog(pid: int) -> Programme | None:
    return get_catalogue().by_id.get(pid)


LIVE_NOTE = ("Fees, application dates, entrance-test dates and seats are live data, not part of the programme record: use get_fees, get_application_dates "
             "and get_seats. Never state one from memory.")


# ------------------------------------------------------------------------------ programme tools
class SearchProgrammesArgs(BaseModel):
    query: str | None = None
    level: Literal["undergraduate", "postgraduate", "doctoral"] | None = None
    campus: str | None = None


@tool("search_programmes",
      "Search CHRIST programmes by name words, level or campus. Returns programme_id (use it with the other tools), name, level, campus and department.",
      SearchProgrammesArgs)
def search_programmes(a: SearchProgrammesArgs, ctx):
    rows = get_catalogue().search(a.query, a.level, a.campus, limit=25)
    return ToolOutput({"count": len(rows), "programmes": [
        {"programme_id": p.id, "name": p.name, "level": p.level, "campus": p.campus, "department": p.department, "duration_years": p.duration_years}
        for p in rows]}, [_ref(None)])


class ProgrammeIdArgs(BaseModel):
    programme_id: int


@tool("get_programme_details",
      "Programme record by programme_id: level, campus, department, duration, term type, summary, overview, selection process (no fees, dates or seats: those are live data).",
      ProgrammeIdArgs)
def get_programme_details(a: ProgrammeIdArgs, ctx):
    c = _db().get_course(a.programme_id)
    if not c:
        return _nf(f"Programme {a.programme_id}")
    for k in ("overview", "selection_process"):
        c[k] = _cut(c.get(k), 2000)
    for k in ("application_open", "application_close", "approved_intake", "brochure"):   # dead website dates / junk intake: ERP only
        c.pop(k, None)
    out = {"programme": c, "live_data_note": LIVE_NOTE, "as_of": as_of()}
    cat = current_catalog()
    p = cat.programs.get(a.programme_id) if cat else None
    if p is not None:                                       # the clean structured record: degree, specialization, eligibility, curriculum, careers, lateral route, fee status
        from ..catalog.lateral import lateral_for
        from ..catalog.record import programme_record
        rec = programme_record(cat, p)
        lat = lateral_for(cat, a.programme_id)
        fee = live.get_fees(a.programme_id)
        out["record"] = {**{k: v for k, v in rec.items() if k not in ("fees",)}, "fees": {"available": fee.available, "summary": (fee.value or {}).get("summary") if fee.available else None, "note": fee.note},
                         "lateral_entry": ({"available": True, "eligibility": lat.eligibility, "unspecified": list(lat.unspecified)} if lat else {"available": False, "note": "No lateral entry is listed for this programme in the database."})}
    return ToolOutput(out, [_ref(_prog(a.programme_id))])


SECTION_KINDS = Literal["eligibility", "fees", "selection", "outcomes", "structure", "entrance_test", "placements", "scholarship", "application_process",
                        "admission_process", "admission_queries", "nri", "international", "cancellation_policy", "faq", "syllabus", "objectives", "misc"]
LEGACY_SQL_KINDS = frozenset({"eligibility", "selection", "outcomes", "structure", "entrance_test", "placements", "scholarship"})


class SectionArgs(BaseModel):
    programme_id: int
    section: SECTION_KINDS
    audience: Literal["indian", "nri", "international"] | None = None


def live_unavailable(fact: live.LiveFact, programme_id: int | None = None) -> ToolOutput:
    """Fees, dates and seats are ERP-only: until the ERP is connected the answer is an honest 'unavailable' plus the admissions contact."""
    p = _prog(programme_id) if programme_id is not None else None
    note = ("Quote the fee table exactly as given, with its academic year, and repeat the note. Never add or change a figure." if fact.available else
            "Say this figure is not available right now and give the admissions contact. Never state it from memory.")
    return ToolOutput({"live_fact": fact.as_dict(), "programme": p.name if p else None, "admissions_contact": live.admissions_contact(), "instruction": note}, [_ref(p)] if p else [])


@tool("get_programme_section",
      "Published text of one section for a programme: eligibility, selection, outcomes, structure, entrance_test, placements, scholarship, application_process, admission_process, "
      "nri, international, cancellation_policy, faq, syllabus... (optionally for one audience). Fees are NOT a website section: asking for 'fees' returns live-data status. "
      "Quote it exactly; report each section's academic_year and expires_on; never fill gaps.",
      SectionArgs)
def get_programme_section(a: SectionArgs, ctx):
    if a.section == "fees":
        return live_unavailable(live.get_fees(a.programme_id), a.programme_id)
    src = _db()
    if getattr(src, "name", "") == "mysql" and a.section not in LEGACY_SQL_KINDS:
        return _nf(f"The {a.section} section for programme {a.programme_id}")
    rows = src.get_sections(a.programme_id, a.section)
    if a.audience:
        rows = [r for r in rows if r.get("audience", "all") in ("all", a.audience)]
    if not rows:
        return _nf(f"The {a.section} section for programme {a.programme_id}")
    return ToolOutput({"sections": [{**r, "text": _cut(r["text"], 6000)} for r in rows[:3]], "as_of": as_of()}, [_ref(_prog(a.programme_id), a.section)])


class EligibilityArgs(BaseModel):
    programme_id: int
    entry_type: Literal["regular", "lateral"] | None = None     # lateral: the eligibility published for the lateral-entry route of this programme


def _route_id(programme_id: int, entry_type: str | None) -> tuple[int, str | None]:
    """The listing that carries the requested route's text: the lateral listing for 'lateral', the regular one for 'regular'. (id, note when not found)"""
    cat = current_catalog()
    p = cat.programs.get(programme_id) if cat else None
    if p is None or not entry_type or p.entry == entry_type:
        return programme_id, None
    twin = next((x for x in cat.programs.values() if x.dedupe_key == p.dedupe_key and x.entry == entry_type), None)
    if twin is not None:
        return twin.id, None
    return programme_id, f"No separate {entry_type}-entry listing exists for this programme."


@tool("get_eligibility_criteria", "Eligibility criteria published for a programme_id (verbatim text); entry_type 'lateral' returns the lateral-entry route's eligibility.", EligibilityArgs)
def get_eligibility_criteria(a: EligibilityArgs, ctx):
    pid, note = _route_id(a.programme_id, a.entry_type)
    rows = _db().get_sections(pid, "eligibility")
    if a.entry_type == "lateral" and note:               # no lateral listing: only a Lateral Entry paragraph inside the regular text counts
        rows = [{**r, "text": "\n".join(ln for ln in r["text"].split("\n") if "lateral" in ln.lower())} for r in rows]
        rows = [r for r in rows if r["text"].strip()]
    if not rows:
        return _nf(f"Published {'lateral-entry ' if a.entry_type == 'lateral' else ''}eligibility criteria for programme {a.programme_id}")
    return ToolOutput({"sections": [{**r, "text": _cut(r["text"])} for r in rows[:3]], "entry_type": a.entry_type, "listing_id": pid, "as_of": as_of()}, [_ref(_prog(pid), "Eligibility")])


class AssessArgs(BaseModel):
    programme_id: int
    percentage: float | None = None


@tool("assess_preliminary_eligibility",
      "Compare an applicant's percentage with percentages mentioned in the programme's published eligibility text. PRELIMINARY only; never confirms eligibility.",
      AssessArgs)
def assess_preliminary_eligibility(a: AssessArgs, ctx):
    rows = _db().get_sections(a.programme_id, "eligibility")
    text = "\n".join(r["text"] for r in rows[:3]) or None
    return ToolOutput({"result": hint(text, a.percentage)}, [_ref(_prog(a.programme_id), "Eligibility")] if text else [])


@tool("get_fee_structure", "Superseded by get_fees: fees are live ERP data. Returns the live-data status for the programme.", ProgrammeIdArgs)
def get_fee_structure(a: ProgrammeIdArgs, ctx):
    return live_unavailable(live.get_fees(a.programme_id), a.programme_id)


class DatesArgs(BaseModel):
    programme_id: int | None = None


@tool("get_admission_dates", "Superseded by get_application_dates: application dates are live ERP data. Returns the live-data status.", DatesArgs)
def get_admission_dates(a: DatesArgs, ctx):
    return live_unavailable(live.get_application_dates(a.programme_id or 0), a.programme_id)


@tool("get_programme_highlights", "Why choose the programme, what you will learn, career paths and modules for a programme_id.", ProgrammeIdArgs)
def get_programme_highlights(a: ProgrammeIdArgs, ctx):
    rows = _db().get_highlights(a.programme_id)
    if not rows:
        return _nf(f"Highlights for programme {a.programme_id}")
    return ToolOutput({"highlights": [{**r, "text": _cut(r["text"], 600)} for r in rows[:20]]}, [_ref(_prog(a.programme_id), "Highlights")])


class CurriculumArgs(BaseModel):
    programme_id: int
    year: str | None = None


@tool("get_curriculum", "Term-by-term subjects and credits for a programme (optionally one year, e.g. 'Year 1'), plus its phase roadmap.", CurriculumArgs)
def get_curriculum(a: CurriculumArgs, ctx):
    src = _db()
    cur = src.get_curriculum(a.programme_id, a.year)
    if not cur:
        return _nf(f"Curriculum for programme {a.programme_id}")
    return ToolOutput({"terms": cur, "roadmap": src.get_roadmap(a.programme_id)[:8]}, [_ref(_prog(a.programme_id), "Curriculum")])


class GroupArgs(BaseModel):
    group: str | None = None


@tool("get_admission_process",
      "General admission process (how to apply, eligibility, selection, dates, notes) for a programme group such as 'ug mgmt', 'pg science', 'phd arts'. Omit group to get every group.",
      GroupArgs)
def get_admission_process(a: GroupArgs, ctx):
    rows = _db().get_admission_process(a.group)
    if not rows:
        return _nf("Admission process for that group")
    return ToolOutput({"processes": [{k: _cut(v, 1800) for k, v in r.items()} for r in rows]},
                      [SourceRef(doc_id="adm_links", title=f"{DB_TITLE} — admission process")])


class RecommendArgs(BaseModel):
    interests: str
    level: Literal["undergraduate", "postgraduate", "doctoral"] | None = None
    campus: str | None = None
    exclude_names: list[str] | None = None   # programmes already suggested, so "show me more" gives new ones


@tool("recommend_programmes",
      "Suggest CHRIST programmes that match what the applicant said they are interested in (free text, e.g. 'coding and AI', 'law', 'money and business'). "
      "Optionally restrict by level or campus. Returns ranked programmes with the matching words, department, campuses and career paths.",
      RecommendArgs)
def recommend_programmes(a: RecommendArgs, ctx):
    cat = get_catalogue()
    skip = {n.strip().lower() for n in (a.exclude_names or [])}
    ranked = [r for r in cat.recommend(a.interests, a.level, a.campus, limit=5 + len(skip)) if r["programme"].name.strip().lower() not in skip][:5]
    if not ranked:
        return ToolOutput({"found": False, "message": "Nothing in the database matches those interests. Ask the applicant what they enjoy or which field they like, "
                                                      "or offer the main departments.", "departments": cat.departments(8)}, [_ref(None)])
    out = []
    for i, r in enumerate(ranked):
        p = r["programme"]
        careers = [_brief(h["title"]) for h in _db().get_highlights(p.id) if h["kind"] == "careers" and h["title"] and not _NOT_A_CAREER.search(h["title"]) and looks_like_role(h["title"])][:3] if i < 3 else []
        out.append({"programme_id": p.id, "name": p.name, "level": p.level, "department": p.department, "duration_years": p.duration_years,
                    "campuses": r["campuses"], "matched_interests": r["matched"], "career_paths": careers})
    return ToolOutput({"recommendations": out, "note": "Matched on programme and department names only. These are suggestions, not admission advice."},
                      [_ref(None)] + [_ref(r["programme"]) for r in ranked[:3]])


class NoArgs(BaseModel):
    pass


class CampusArgs(BaseModel):
    campus: str | None = None


def _campus_pick(name: str | None, rows: list[dict]) -> list[dict]:
    if not name:
        return rows
    words = {w for w in name.lower().replace("campus", " ").split() if w not in ("bangalore", "bengaluru", "the", "christ")} or set(name.lower().split())
    return [r for r in rows if words & set(r["name"].lower().split())]


@tool("get_campus_details",
      "Campus contact details from the database: address, phone numbers, official email, website and a short description. Give a campus name (e.g. 'Kengeri', 'Pune Lavasa') or omit it for every campus. "
      "Use for 'where is', 'how do I contact', phone and email questions.",
      CampusArgs)
def get_campus_details(a: CampusArgs, ctx):
    rows = _campus_pick(a.campus, _db().list_campus_details())
    if not rows:
        return _nf(f"A campus matching '{a.campus}'")
    return ToolOutput({"campuses": rows}, [_ref(None)])


class FacilityArgs(BaseModel):
    campus: str | None = None
    facility: str | None = None


@tool("get_campus_facilities",
      "Campus facilities from the database (hostel/accommodation, library, transport, medical, cafeteria, labs...). Optionally filter by campus name and/or a facility word such as 'hostel'. "
      "Quote the text exactly; a campus without an entry simply has none recorded.",
      FacilityArgs)
def get_campus_facilities(a: FacilityArgs, ctx):
    src = _db()
    cid = None
    if a.campus:
        picked = _campus_pick(a.campus, src.list_campus_details())
        if not picked:
            return _nf(f"A campus matching '{a.campus}'")
        cid = picked[0]["campus_id"] if len(picked) == 1 else None
    rows = src.list_facilities(cid)
    if a.facility:
        words = [w for w in a.facility.lower().split() if len(w) > 2]
        syn = {"hostel": ("hostel", "accommodation", "residence", "residences"), "accommodation": ("hostel", "accommodation", "residence", "residences"),
               "transport": ("transport", "transportation", "bus"), "food": ("cafeteria", "canteen", "dining")}
        wanted = {x for w in words for x in syn.get(w, (w,))}
        rows = [r for r in rows if any(x in r["facility"].lower() for x in wanted)]
    if not rows:
        return _nf("That facility is not recorded for this campus; the facility information")
    return ToolOutput({"facilities": [{**r, "text": _cut(r["text"], 900)} for r in rows[:12]]}, [_ref(None)])

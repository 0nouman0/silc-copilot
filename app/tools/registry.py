"""Tool registry over the CHRIST programme database.

Small, focused tools with typed (pydantic) inputs. The model supplies parameters only; every query is predefined and
parameterised inside the `CatalogueSource`. Applicant tools require sign-in and re-check ownership on every call.
Anything the database does not hold comes back as found=false, and a database outage comes back as a structured error.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from ..applicants.adapter import ApplicantApiUnavailable, get_applicant_api
from ..catalogue import Programme, get_catalogue, window_status
from ..eligibility_hint import hint
from ..llm.base import ToolDef
from ..models import SourceRef
from ..sources import SourceUnavailable, get_source

MAX_TEXT = 3500
DB_TITLE = "CHRIST programme database"


@dataclass
class ToolContext:
    user_id: str | None = None
    active_application_id: str | None = None


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
    requires_auth: bool = False


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args: type[BaseModel], requires_auth: bool = False):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, args, fn, requires_auth)
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


def available_tools(ctx: ToolContext) -> list[Tool]:
    return [t for t in REGISTRY.values() if not t.requires_auth or ctx.user_id]


def tool_defs(ctx: ToolContext) -> list[ToolDef]:
    return [ToolDef(t.name, t.description, _simplify(t.args.model_json_schema())) for t in available_tools(ctx)]


def execute(name: str, raw_args: str, ctx: ToolContext) -> ToolOutput:
    """Validate + run one tool. Never raises: failures become structured errors the caller must relay honestly."""
    t = REGISTRY.get(name)
    if t is None or (t.requires_auth and not ctx.user_id):
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
    except ApplicantApiUnavailable:
        return ToolOutput({"error": "live_status_unavailable", "message": "Live application information cannot be checked right now."})


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


def _ref(p: Programme | None, label: str = "") -> SourceRef:
    if p is None:
        return SourceRef(doc_id="catalogue", title=DB_TITLE)
    campus = f" ({p.campus})" if p.campus else ""
    return SourceRef(doc_id=f"course:{p.id}" + (f":{label}" if label else ""), title=f"{DB_TITLE} — {p.name}{campus}" + (f" · {label}" if label else ""))


def _prog(pid: int) -> Programme | None:
    return get_catalogue().by_id.get(pid)


DATES_NOTE = ("Application dates are the ones stored on the programme record in the database; they may be out of date, so tell the applicant "
              "to confirm the current dates on the official website or with the Admissions Office.")


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
      "Programme record by programme_id: level, campus, department, duration, term type, approved intake, summary, overview, selection process, recorded application dates.",
      ProgrammeIdArgs)
def get_programme_details(a: ProgrammeIdArgs, ctx):
    c = _db().get_course(a.programme_id)
    if not c:
        return _nf(f"Programme {a.programme_id}")
    for k in ("overview", "selection_process"):
        c[k] = _cut(c.get(k), 2000)
    return ToolOutput({"programme": c, "dates_note": DATES_NOTE}, [_ref(_prog(a.programme_id))])


class SectionArgs(BaseModel):
    programme_id: int
    section: Literal["eligibility", "fees", "selection", "outcomes", "structure", "entrance_test", "placements", "scholarship"]


@tool("get_programme_section",
      "Published text of one section for a programme: eligibility, fees, selection, outcomes, structure, entrance_test, placements or scholarship. Quote it exactly; never fill gaps.",
      SectionArgs)
def get_programme_section(a: SectionArgs, ctx):
    rows = _db().get_sections(a.programme_id, a.section)
    if not rows:
        return _nf(f"The {a.section} section for programme {a.programme_id}")
    return ToolOutput({"sections": [{**r, "text": _cut(r["text"], 6000)} for r in rows[:3]]}, [_ref(_prog(a.programme_id), a.section)])


@tool("get_eligibility_criteria", "Eligibility criteria published for a programme_id (verbatim text).", ProgrammeIdArgs)
def get_eligibility_criteria(a: ProgrammeIdArgs, ctx):
    rows = _db().get_sections(a.programme_id, "eligibility")
    if not rows:
        return _nf(f"Published eligibility criteria for programme {a.programme_id}")
    return ToolOutput({"sections": [{**r, "text": _cut(r["text"])} for r in rows[:3]]}, [_ref(_prog(a.programme_id), "Eligibility")])


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


@tool("get_fee_structure", "Fee structure published for a programme_id (registration fee, per-year fees by category, payment timing).", ProgrammeIdArgs)
def get_fee_structure(a: ProgrammeIdArgs, ctx):
    rows = _db().get_sections(a.programme_id, "fees")
    if not rows:
        return _nf(f"A published fee structure for programme {a.programme_id}")
    return ToolOutput({"sections": [{**r, "text": _cut(r["text"], 6000)} for r in rows[:2]]}, [_ref(_prog(a.programme_id), "Fees")])


class DatesArgs(BaseModel):
    programme_id: int | None = None


@tool("get_admission_dates", "Application open/close dates recorded in the database: for one programme_id, or the application windows across the catalogue.", DatesArgs)
def get_admission_dates(a: DatesArgs, ctx):
    cat = get_catalogue()
    if a.programme_id is not None:
        p = cat.by_id.get(a.programme_id)
        if not p:
            return _nf(f"Programme {a.programme_id}")
        return ToolOutput({"programme": p.name, "campus": p.campus, "application_open": p.application_open, "application_close": p.application_close,
                           "status": p.status, "record_last_modified": p.record_last_modified, "note": DATES_NOTE}, [_ref(p)])
    return ToolOutput({"today": date.today().isoformat(), "note": DATES_NOTE,
                       "windows": [{"level": lv, "campus": cp, "open": o, "close": cl, "programmes": n, "status": window_status(o, cl)}
                                   for (lv, cp, o, cl, n) in cat.windows()]},
                      [_ref(None)])


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


class NoArgs(BaseModel):
    pass


@tool("list_campuses", "List the active CHRIST campuses.", NoArgs)
def list_campuses(a: NoArgs, ctx):
    return ToolOutput({"campuses": _db().list_campuses()}, [_ref(None)])


# ------------------------------------------------------------------------------ applicant tools (auth required, MOCK API)
@tool("get_my_applications", "List the signed-in applicant's own applications (count, programme, status).", NoArgs, requires_auth=True)
def get_my_applications(a: NoArgs, ctx):
    api = get_applicant_api()
    apps = api.list_applications(ctx.user_id)  # type: ignore[arg-type]
    return ToolOutput({"demo": api.is_demo, "application_count": len(apps), "applications": [x.model_dump(mode="json") for x in apps]})


class AppArgs(BaseModel):
    application_id: str | None = None


@tool("get_application_status",
      "Get the exact status of one of the signed-in applicant's applications (defaults to the active one). Report status exactly; never infer selection.",
      AppArgs, requires_auth=True)
def get_application_status(a: AppArgs, ctx):
    api = get_applicant_api()
    app_id = a.application_id or ctx.active_application_id
    if not app_id:
        return ToolOutput({"found": False, "message": "No application selected. Ask which application to discuss."})
    app = api.get_application(ctx.user_id, app_id)  # type: ignore[arg-type]  # ownership enforced inside the adapter
    if app is None:
        return ToolOutput({"found": False, "message": "No application with that reference is associated with your account."})
    return ToolOutput({"demo": api.is_demo, "application": app.model_dump(mode="json"),
                       "note": "status/offer_status are authoritative. offer_status null means no offer information was returned."})

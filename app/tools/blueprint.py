"""Blueprint tools over the in-memory catalog, the deterministic eligibility engine and the (deferred) live-data layer.

Same rules as `registry.py`: typed pydantic arguments only, no SQL or URLs from the model, never raises, and every answer carries
its provenance (page link, build time). Fees, dates, seats and vacancy are live ERP facts: they return an honest 'unavailable'.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import live
from ..catalog.indexes import norm_name
from ..catalog.models import Audience, Catalog, Program, SectionKind
from ..catalog.refresh import current_catalog, get_catalog
from ..catalogue import get_catalogue
from ..eligibility import Profile, check_eligibility, compare_programs as _compare, explain_only, rank_programs as _rank
from ..models import SourceRef
from ..sources import SourceUnavailable
from .registry import DB_TITLE, ProgrammeIdArgs, ToolOutput, _cut, _nf, as_of, live_unavailable, tool

LEVELS = Literal["UG", "PG", "PhD", "Diploma", "Certificate"]
TOPICS = Literal["hostel", "dining", "scholarship", "nri", "international", "cancellation_refund", "transport", "anti_ragging", "grievance",
                 "placement", "sports_clubs", "medical", "library", "contact", "facility"]


def _cat() -> Catalog:
    return get_catalog()


def _ref(p: Program | None, label: str = "") -> SourceRef:
    if p is None:
        return SourceRef(doc_id="catalogue", title=DB_TITLE)
    return SourceRef(doc_id=f"course:{p.id}" + (f":{label}" if label else ""), title=f"{DB_TITLE} — {p.name}" + (f" · {label}" if label else ""), url=p.url or None)


def _campus_name(cat: Catalog, cid: int | None) -> str | None:
    c = cat.campuses.get(cid) if cid is not None else None
    return c.name if c else None


def _brief(cat: Catalog, p: Program) -> dict[str, Any]:
    d = cat.departments.get(p.department_id) if p.department_id else None
    return {"programme_id": p.id, "name": p.name, "level": p.level, "campus": _campus_name(cat, p.campus_id), "department": d.name if d else None,
            "campuses": sorted({n for n in (_campus_name(cat, pl.campus_id) for pl in p.placements) if n}), "duration_years": p.duration_years, "url": p.url}


def _find_campus(cat: Catalog, name: str | None) -> int | None:
    if not name:
        return None
    words = [w for w in name.lower().replace("campus", " ").split() if w not in ("bangalore", "bengaluru", "the", "christ")] or name.lower().split()
    hits = [c.id for c in cat.campuses.values() if any(w in c.name.lower() for w in words)]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------------------ discovery
class ResolveArgs(BaseModel):
    text: str = Field(max_length=200)


@tool("resolve_program",
      "Map what the applicant typed ('BBA', 'mba finance', 'B.Com Hons') to programme_id(s) with their campus. If more than one distinct programme or campus matches, "
      "ambiguous is true: ask the applicant which one. Use this before any programme-specific tool.", ResolveArgs)
def resolve_program(a: ResolveArgs, ctx):
    cat = current_catalog()
    ids = list(cat.indexes.by_alias.get(norm_name(a.text), ())) if cat else []
    if ids:
        progs = [cat.programs[i] for i in ids]
        names = {" ".join(p.name.lower().split()) for p in progs}
        return ToolOutput({"matches": [_brief(cat, p) for p in progs[:8]], "ambiguous": len(progs) > 1,
                           "clarify": "Several programmes or campuses match: ask which campus, level or programme." if len(progs) > 1 else None,
                           "distinct_programmes": len(names)}, [_ref(None)])
    res = get_catalogue().resolve(a.text)
    if not res.candidates:
        return ToolOutput({"matches": [], "ambiguous": False, "message": "No programme matches that name in the CHRIST programme database."}, [_ref(None)])
    return ToolOutput({"matches": [{"programme_id": p.id, "name": p.name, "level": p.level, "campus": p.campus, "department": p.department} for p in res.candidates],
                       "ambiguous": res.ambiguous, "clarify": "Several different programmes match: ask which one." if res.ambiguous else None}, [_ref(None)])


class SearchArgs(BaseModel):
    keywords: str | None = Field(default=None, max_length=120)
    level: LEVELS | None = None
    campus: str | None = Field(default=None, max_length=80)
    school: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, max_length=80)
    degree_type: str | None = Field(default=None, max_length=20)         # HARD: B.Tech, BSc, BCA, MSc, PhD ... (validated against the degrees in the catalog)
    discipline: str | None = Field(default=None, max_length=80)          # HARD when it names a known subject (Computer Science, engineering ...)
    specialization: str | None = Field(default=None, max_length=80)      # HARD (AI/ML, Data Science, Cyber Security ...)
    entry_type: Literal["regular", "lateral"] | None = None              # HARD: the admission route


@tool("search_programs",
      "List programmes by level (UG/PG/PhD/Diploma/Certificate), campus, school, department and/or keywords. Returns programme_id, campus(es), department and page link.",
      SearchArgs)
def search_programs(a: SearchArgs, ctx):
    cat = _cat()
    if a.degree_type or a.discipline or a.specialization or a.entry_type:        # structured, validated search (what the conversation orchestrator uses)
        return _structured_search(cat, a)
    ix = cat.indexes
    campus_id = _find_campus(cat, a.campus)
    if a.campus and campus_id is None:
        return _nf(f"A campus matching '{a.campus}'")
    school_id = next((s.id for s in cat.schools.values() if a.school and a.school.lower() in s.name.lower()), None)
    dept_id = next((d.id for d in cat.departments.values() if a.department and a.department.lower() in d.name.lower()), None)
    filtered = bool(a.level or campus_id is not None or school_id or dept_id)
    ids = ix.by_filter(a.level, campus_id, school_id, dept_id) if filtered else sorted(cat.programs)
    if a.keywords:
        named = {p.id for p in get_catalogue().search(a.keywords, None, None, limit=200)}
        by_text = {c.program_id for c in ix.search(a.keywords, 60) if c.program_id is not None} if not named else set()
        keep = named or by_text
        ids = [i for i in ids if i in keep]
    progs = [cat.programs[i] for i in ids][:25]
    if not progs:
        return _nf("A programme matching those filters")
    return ToolOutput({"count": len(ids), "shown": len(progs), "programmes": [_brief(cat, p) for p in progs], "as_of": as_of()}, [_ref(None)])


def _structured_search(cat: Catalog, a: SearchArgs) -> ToolOutput:
    from ..catalog.search import DEGREE_LEVEL, DEGREE_PATTERNS, Query, find_programmes, parse_query
    import re as _re
    degree = None
    if a.degree_type:
        degree = next((d for rx, d in DEGREE_PATTERNS if _re.search(rx, a.degree_type, _re.I)), None) or (a.degree_type if a.degree_type in DEGREE_LEVEL else None)
        if degree is None:
            return ToolOutput({"found": False, "message": f"'{a.degree_type}' is not a degree in the CHRIST programme database.", "valid_degrees": sorted(DEGREE_LEVEL)})
    parsed = parse_query(" ".join(x for x in (a.discipline, a.specialization) if x), cat)
    q = Query(degree=degree, level={"UG": "Undergraduate", "PG": "Postgraduate", "PhD": "Doctoral"}.get(a.level or ""), concepts=parsed.concepts, words=parsed.words,
              soft=tuple(parse_query(a.keywords, cat).words) if a.keywords else (), campus=a.campus and (_campus_label(cat, a.campus)), entry=a.entry_type)
    if degree:
        q.level = DEGREE_LEVEL[degree]
    groups = find_programmes(cat, q)
    if not groups:
        return ToolOutput({"found": False, "message": "No programme in the CHRIST programme database matches all of those filters.", "filters": _filters(q)}, [_ref(None)])
    return ToolOutput({"found": True, "count": len(groups), "filters": _filters(q), "as_of": as_of(), "programmes": [
        {"programme_id": g.rep.id, "listing_ids": list(g.ids), "name": g.rep.name, "degree_type": g.rep.degree_type, "entry": g.rep.entry, "lateral_entry_also": g.lateral_also,
         "campuses": [cat.campuses[i].name for i in g.campus_ids if i in cat.campuses], "duration_years": g.rep.duration_years, "url": g.rep.url} for g in groups[:60]]},
        [_ref(g.rep) for g in groups[:3]])


def _filters(q) -> dict[str, Any]:
    return {"degree_type": q.degree, "academic_level": q.level, "concepts": [c.name for c in q.concepts], "subject_words": list(q.words), "campus": q.campus, "entry_type": q.entry}


def _campus_label(cat: Catalog, text: str) -> str | None:
    from ..catalog.search import campus_of
    return campus_of(cat, text) or ("Bangalore" if "bangal" in text.lower() or "bengal" in text.lower() else None)


# ------------------------------------------------------------------------------------------------ lateral entry and the admission process
class LateralArgs(BaseModel):
    programme_id: int | None = None                                      # check THIS programme (the one under discussion), else every programme matching the filters
    degree_type: str | None = Field(default=None, max_length=20)
    discipline: str | None = Field(default=None, max_length=80)
    specialization: str | None = Field(default=None, max_length=80)
    campus: str | None = Field(default=None, max_length=80)


@tool("search_lateral_entry",
      "Which programmes explicitly support lateral entry (direct second-year admission), with the verbatim eligibility text, entry year if stated, procedure if stated and what the database does NOT say. "
      "Give programme_id to check one programme, or filters (degree_type, discipline, specialization, campus) to check every matching programme. Never assume a programme offers it.", LateralArgs)
def search_lateral_entry(a: LateralArgs, ctx):
    from ..catalog.lateral import lateral_index
    from ..catalog.search import DEGREE_LEVEL, DEGREE_PATTERNS, Query, find_programmes, group_for, parse_query
    import re as _re
    cat = _cat()
    idx = lateral_index(cat)
    if a.programme_id is not None:
        p = cat.programs.get(a.programme_id)
        if p is None:
            return _nf(f"Programme {a.programme_id}")
        keys, label = [p.dedupe_key], p.name
        matching = [group_for(cat, a.programme_id)]
        filters: dict[str, Any] = {"programme_id": a.programme_id}
    else:
        degree = None
        if a.degree_type:
            degree = next((d for rx, d in DEGREE_PATTERNS if _re.search(rx, a.degree_type, _re.I)), None) or (a.degree_type if a.degree_type in DEGREE_LEVEL else None)
        parsed = parse_query(" ".join(x for x in (a.discipline, a.specialization) if x), cat)
        q = Query(degree=degree, level=DEGREE_LEVEL.get(degree), concepts=parsed.concepts, words=parsed.words, campus=a.campus and _campus_label(cat, a.campus))
        any_filter = bool(degree or q.concepts or q.words or q.campus)
        matching = find_programmes(cat, q, limit=500) if any_filter else []
        keys = [g.rep.dedupe_key for g in matching] if any_filter else list(idx)
        label = None
        filters = _filters(q)
    offering = [idx[k] for k in dict.fromkeys(keys) if k in idx]
    offering.sort(key=lambda e: e.name)
    not_listed = [g.rep.name for g in matching if g and g.rep.dedupe_key not in idx][:12]
    rows = [{"programme_id": e.programme_id, "regular_listing_id": e.regular_listing_id, "lateral_listing_id": e.listing_id, "name": e.name, "degree_type": e.degree_type,
             "campuses": [cat.campuses[i].name for i in e.campus_ids if i in cat.campuses], "lateral_entry_available": True, "evidence": list(e.evidence), "eligibility": e.eligibility,
             "eligibility_source": e.eligibility_source, "entry_year_or_semester": e.entry_year, "admission_process": e.admission_process, "not_stated_in_database": list(e.unspecified), "url": e.url}
            for e in offering]
    return ToolOutput({"found": bool(rows), "checked_programme": label, "filters": filters, "programmes_offering_lateral_entry": rows, "matching_programmes_without_a_lateral_entry_listing": not_listed,
                       "as_of": as_of(), "admissions_contact": live.admissions_contact(),
                       "note": "Availability comes from a '(Lateral Entry)' listing or a 'Lateral Entry' paragraph in the published eligibility. Entry year, procedure and documents are given only when stored."},
                      [SourceRef(doc_id=f"course:{e.programme_id}:lateral", title=f"{DB_TITLE} — {e.name} · lateral entry", url=e.url) for e in offering[:3]] or [_ref(None)])


class AdmissionArgs(BaseModel):
    query: str = Field(default="how to apply admission process", max_length=200)
    programme_id: int | None = None
    entry_type: Literal["regular", "lateral"] | None = None


@tool("search_admission_process",
      "Published admission, application, selection and entrance-test text (optionally for one programme or for the lateral-entry route), ranked, with its source page. Returns nothing when the database holds none: never invent a procedure.", AdmissionArgs)
def search_admission_process(a: AdmissionArgs, ctx):
    cat = _cat()
    kinds = {"application_process", "admission_process", "selection_process", "admission_queries", "entrance_test", "faq"}
    query = a.query + " application admission process apply register" + (" lateral entry diploma" if a.entry_type == "lateral" else "")
    hits = cat.indexes.search(query, 12, program_id=a.programme_id, kinds=kinds)
    if a.programme_id is None:                                  # no programme named: only the university-wide blocks, never one programme's own process
        hits = [h for h in hits if h.program_ids is not None][:6]
    if a.entry_type == "lateral":
        hits = [h for h in hits if "lateral" in h.text.lower()] or []
    if not hits:
        return ToolOutput({"found": False, "message": "No admission-process text matching that is published in the CHRIST programme database.", "admissions_contact": live.admissions_contact()}, [_ref(None)])
    return ToolOutput({"found": True, "passages": [{"title": c.title, "kind": c.kind, "text": _cut(c.text, 1400), "url": c.url, "expired_section": c.expired,
                                                    "source_row": c.provenance.row_id if c.provenance else None} for c in hits[:4]], "as_of": as_of(), "admissions_contact": live.admissions_contact()},
                      [SourceRef(doc_id=f"chunk:{c.id}", title=f"{DB_TITLE} — {c.title}", url=c.url) for c in hits[:3]])


# ------------------------------------------------------------------------------ programme content
@tool("get_specializations", "Specialisations of a programme with their roadmap phases (terms, description, career roles, published package as stated).", ProgrammeIdArgs)
def get_specializations(a: ProgrammeIdArgs, ctx):
    p = _cat().program(a.programme_id)
    if not p or not p.specializations:
        return _nf(f"Specialisations for programme {a.programme_id}")
    return ToolOutput({"programme": p.name, "specialisations": [
        {"id": s.id, "name": s.name, "description": _cut(s.description, 900), "note": s.note,
         "roadmap": [{"phase": r.title, "terms": r.term_range, "description": _cut(r.description, 400), "career_roles": list(r.career_roles), "published_package": r.published_package}
                     for r in s.roadmap]} for s in p.specializations[:12]], "as_of": as_of()}, [_ref(p, "Specialisations")])


@tool("get_documents", "Brochure, programme-structure and syllabus files of a programme (link and any extracted text). Brochure text ranks below the website sections and is never used for fees or dates.", ProgrammeIdArgs)
def get_documents(a: ProgrammeIdArgs, ctx):
    p = _cat().program(a.programme_id)
    if not p or not p.documents:
        return _nf(f"Documents for programme {a.programme_id}")
    return ToolOutput({"programme": p.name, "documents": [
        {"kind": d.kind, "file": d.filename, "url": d.url or None, "extracted": [{"page": s.provenance.page if s.provenance else None, "text": _cut(s.text, 900)} for s in d.extracted[:4]],
         "text_extracted": bool(d.extracted)} for d in p.documents[:10]], "as_of": as_of()}, [_ref(p, "Documents")])


class ContentArgs(BaseModel):
    query: str = Field(max_length=200)
    programme_id: int | None = None
    topic: TOPICS | None = None
    campus: str | None = Field(default=None, max_length=80)


@tool("search_content",
      "Keyword search over the programme sections, campus topic pages and extracted brochure text. Returns ranked passages with their source page; expired or older-cycle passages are flagged.",
      ContentArgs)
def search_content(a: ContentArgs, ctx):
    cat = _cat()
    cid = _find_campus(cat, a.campus)
    hits = cat.indexes.search(a.query, 5, program_id=a.programme_id, topic=a.topic, campus_id=cid)
    if not hits:
        return _nf("Content matching that question")
    return ToolOutput({"passages": [{"title": c.title, "kind": c.kind, "text": _cut(c.text, 1100), "url": c.url, "expired_section": c.expired,
                                     "source_row": c.provenance.row_id if c.provenance else None} for c in hits], "as_of": as_of()},
                      [SourceRef(doc_id=f"chunk:{c.id}", title=f"{DB_TITLE} — {c.title}", url=c.url) for c in hits[:3]])


class TopicArgs(BaseModel):
    topic: TOPICS
    campus: str | None = Field(default=None, max_length=80)


@tool("get_topic_info", "Reviewed campus information on a topic (hostel, dining, scholarship, NRI, international, transport, medical, library, ...), per campus. Quote exactly.", TopicArgs)
def get_topic_info(a: TopicArgs, ctx):
    cat = _cat()
    cid = _find_campus(cat, a.campus)
    if a.campus and cid is None:
        return _nf(f"A campus matching '{a.campus}'")
    items = [it for (t, c), its in cat.indexes.by_topic.items() if t == a.topic and (cid is None or c in (None, cid)) for it in its]
    if not items:
        return _nf(f"Reviewed {a.topic} information" + (f" for {a.campus}" if a.campus else ""))
    return ToolOutput({"topic": a.topic, "items": [{"campus": _campus_name(cat, it.campus_id) or "All campuses", "title": it.title, "text": _cut(it.text, 1200), "link": it.link} for it in items[:10]],
                       "as_of": as_of()}, [_ref(None)])


class CampusContactArgs(BaseModel):
    campus: str | None = Field(default=None, max_length=80)


@tool("get_campus_contacts", "Admissions email and phone numbers of a campus (or every campus). Use for contact, escalation and 'who do I ask' answers.", CampusContactArgs)
def get_campus_contacts(a: CampusContactArgs, ctx):
    cat = _cat()
    cid = _find_campus(cat, a.campus)
    if a.campus and cid is None:
        return _nf(f"A campus matching '{a.campus}'")
    return ToolOutput({**live.admissions_contact(cid), "as_of": as_of()}, [_ref(None)])


# ------------------------------------------------------------------------------ eligibility, ranking, comparison
class EligibilityArgs(BaseModel):
    programme_id: int
    audience: Literal["indian", "nri", "international"] | None = None
    qualification: str | None = Field(default=None, max_length=60)
    percent: float | None = Field(default=None, ge=0, le=100)
    cgpa: float | None = Field(default=None, ge=0, le=10)
    cgpa_scale: Literal[4, 10] | None = None
    board: str | None = Field(default=None, max_length=60)
    subjects: list[str] | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, max_length=40)


def merged_profile(ctx, **over: Any) -> Profile:
    """The applicant's saved profile with any fields the model passes for this call layered on top."""
    base = ctx.profile.model_dump() if getattr(ctx, "profile", None) is not None else {}
    base.update({k: v for k, v in over.items() if v is not None})
    return Profile.model_validate(base)


@tool("check_eligibility",
      "Computed eligibility verdict for a programme from admissions-reviewed rules: eligible, not_eligible (which condition failed and by how much), need_info (ask the ONE question given) or "
      "cannot_decide (no reviewed rule / foreign qualification: show the published criteria and the admissions contact). Pass only what the applicant said; saved profile fields are used too.",
      EligibilityArgs)
def check_eligibility_tool(a: EligibilityArgs, ctx):
    cat = _cat()
    p = cat.program(a.programme_id)
    if not p:
        return _nf(f"Programme {a.programme_id}")
    prof = merged_profile(ctx, qualification=a.qualification, percent=a.percent, cgpa=a.cgpa, cgpa_scale=a.cgpa_scale, board=a.board, subjects=a.subjects, category=a.category)
    v = check_eligibility(prof, p, a.audience)
    out = {"programme": p.name, "verdict": v.as_dict(), "as_of": as_of()}
    if v.outcome in ("cannot_decide", "need_info", "not_eligible") or p.quality.unreviewed_eligibility:
        out["published_criteria"] = explain_only(cat, p)["published_criteria"]
    return ToolOutput(out, [_ref(p, "Eligibility")])


class RankArgs(BaseModel):
    interests: str | None = Field(default=None, max_length=300)
    level: LEVELS | None = None
    campus: str | None = Field(default=None, max_length=80)
    exclude_names: list[str] | None = Field(default=None, max_length=10)


@tool("rank_programs",
      "Recommend 3-5 programmes: applicant interests (free text) ranked by programme content, after dropping every programme the applicant is ineligible for. Uses the saved profile "
      "(marks, nationality, preferred campus). Never predicts admission chances.", RankArgs)
def rank_programs(a: RankArgs, ctx):
    cat = _cat()
    prof = merged_profile(ctx)
    res = _rank(cat, prof, a.interests, level=a.level, campus=a.campus, limit=5, exclude_names=a.exclude_names)
    return ToolOutput({**res, "as_of": as_of()}, [_ref(None)] + [SourceRef(doc_id=f"course:{r['programme_id']}", title=f"{DB_TITLE} — {r['name']}", url=r["url"] or None) for r in res.get("recommendations", [])[:3]])


class CompareArgs(BaseModel):
    programme_ids: list[int] = Field(min_length=2, max_length=4)


@tool("compare_programs", "Side-by-side facts for 2-4 programmes (level, campus, duration, specialisations, published eligibility and selection). Fees only via get_fees.", CompareArgs)
def compare_programs(a: CompareArgs, ctx):
    cat = _cat()
    res = _compare(cat, a.programme_ids)
    if not res["found"]:
        return _nf("Those programmes")
    return ToolOutput({**res, "fees_note": "Fees are live data: ask get_fees for each programme.", "as_of": as_of()}, [_ref(cat.program(i)) for i in a.programme_ids if cat.program(i)])


# ------------------------------------------------------------------------------ hand-off
class HandoffArgs(BaseModel):
    summary: str = Field(max_length=500)
    campus: str | None = Field(default=None, max_length=80)
    category: Literal["application_status", "payment", "refund", "documents", "callback", "complaint", "other"] = "other"


@tool("create_handoff",
      "Prepare a hand-off to the admissions staff for a personal case (status, payment, refund, documents, callback, complaint). Returns the right campus contact and the summary to give them. "
      "Nothing is sent automatically yet: tell the applicant to contact the office. Never put names, phone numbers or payment details in the summary.", HandoffArgs)
def create_handoff(a: HandoffArgs, ctx):
    cat = _cat()
    cid = _find_campus(cat, a.campus)
    return ToolOutput({"handoff": {"status": "prepared_not_sent", "category": a.category, "summary": a.summary, "campus": _campus_name(cat, cid)},
                       "contact": live.admissions_contact(cid),
                       "instruction": "Tell the applicant this needs a person, give the contact above, and do not claim a ticket was created: no ticketing endpoint is connected yet."}, [_ref(None)])


# ------------------------------------------------------------------------------ live data (deferred: ERP not connected)
class FeesArgs(BaseModel):
    programme_id: int
    audience: Literal["indian", "nri", "international"] | None = None
    year: str | None = Field(default=None, max_length=9)


@tool("get_fees", "Published fees for a programme exactly as stored (registration fee, per-year fee table by applicant category, academic year, validity note), or that they are not available. Never quote a fee from anywhere else.", FeesArgs)
def get_fees(a: FeesArgs, ctx):
    return live_unavailable(live.get_fees(a.programme_id, a.audience, a.year), a.programme_id)


@tool("get_application_dates", "Application open/close and entrance-test dates for a programme. LIVE ERP data: reports value, source and as_of, or that it is unavailable.", ProgrammeIdArgs)
def get_application_dates(a: ProgrammeIdArgs, ctx):
    return live_unavailable(live.get_application_dates(a.programme_id), a.programme_id)


@tool("get_seats", "Seats / intake for a programme. LIVE ERP data: reports value, source and as_of, or that it is unavailable.", ProgrammeIdArgs)
def get_seats(a: ProgrammeIdArgs, ctx):
    return live_unavailable(live.get_seats(a.programme_id), a.programme_id)


@tool("get_hostel_vacancy", "Hostel vacancy for a campus. LIVE ERP data: reports value, source and as_of, or that it is unavailable.", CampusContactArgs)
def get_hostel_vacancy(a: CampusContactArgs, ctx):
    cat = _cat()
    cid = _find_campus(cat, a.campus)
    f = live.get_hostel_vacancy(cid)
    return ToolOutput({"live_fact": f.as_dict(), "admissions_contact": live.admissions_contact(cid)}, [_ref(None)])


class NoArgs(BaseModel):
    pass


@tool("get_application_status",
      "Status of an applicant's own application. Only possible for a logged-in applicant on the university portal; this assistant cannot see applications. Never ask for an application number.", NoArgs)
def get_application_status(a: NoArgs, ctx):
    return ToolOutput({"live_fact": live.get_application_status().as_dict(), "admissions_contact": live.admissions_contact()}, [_ref(None)])

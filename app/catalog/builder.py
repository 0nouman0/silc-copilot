"""Replica rows + reviewed config (+ extracted PDF text) -> an immutable in-memory `Catalog`.

Nothing here knows about fees, application dates, seats or personal data: those queries do not exist, and `check_catalog` refuses a
build that somehow contains them. Tables the account may not read degrade to a warning (except the required ones).
"""
from __future__ import annotations

import base64
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote

from ..sources.base import DataNotGranted
from .classify import (classify_r, detect_audience, detect_year, document_kind, drop_older_cycles, facility_topic, shared_excluded, year_value)
from .normalize import classify_programme, norm_key
from .clean import clean_html, content_hash, ints, one_line, parse_tables, split_list
from .config import CatalogConfig, load_config
from .indexes import Chunk, build_indexes, chunk_text, norm_name
from .models import (Audience, BuildInfo, Campus, Catalog, DataQuality, Department, DocumentRef, Placement, Program, Provenance, RoadmapPhase, School,
                     Section, SectionKind, Semester, Specialization, Subject, TopicItem)
from .queries import REQUIRED
from .reader import RowReader

DETAIL_KIND = {1: SectionKind.WHY_CHOOSE, 2: SectionKind.WHAT_YOU_LEARN, 3: SectionKind.CAREERS, 4: SectionKind.MODULES, 5: SectionKind.CAREER_ROLES}
TERM = {"T": "trimester", "S": "semester", "Y": "year"}
EXPECTED = (SectionKind.ELIGIBILITY, SectionKind.SELECTION_PROCESS, SectionKind.OUTCOMES)
_FILE = re.compile(r"([^\s/\\\"'<>|]+\.(?:pdf|docx?|pptx?|xlsx?))", re.I)
_REAL_PDF = re.compile(r"^[^_\s][^\\/]*\.pdf$", re.I)
_HREF_PDF = re.compile(r"href\s*=\s*[\"']([^\"']+\.pdf)[\"']", re.I)


def level_of(category_name: str | None, category_id: int | None = None) -> str:
    n = (category_name or "").lower()
    if "undergrad" in n:
        return "UG"
    if "postgrad" in n:
        return "PG"
    if "doctor" in n or "phd" in n or "ph.d" in n:
        return "PhD"
    if "diploma" in n:
        return "Diploma"
    if "certificate" in n:
        return "Certificate"
    return "Other"


def program_url(website: str | None, course_id: int) -> str:
    """Citation link: {campus_website}/courses/{base64(course_id)} (pattern to be verified with the web team)."""
    token = base64.b64encode(str(course_id).encode()).decode().rstrip("=")
    return f"{(website or '').rstrip('/')}/courses/{token}" if website else ""


def _date(v: Any) -> date | None:
    s = str(v) if v else ""
    if not s or s.startswith("0000"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def build_catalog(reader: RowReader, cfg: CatalogConfig | None = None, *, now: datetime | None = None,
                  extracted: Mapping[str, tuple[Section, ...]] | None = None, today: date | None = None) -> Catalog:
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    extracted = extracted or {}
    warnings: list[str] = []

    def fetch(name: str, params: tuple = ()) -> list[dict[str, Any]]:
        try:
            return reader.rows(name, params)
        except DataNotGranted:
            from .queries import QUERIES
            table = QUERIES[name][0]
            if table in REQUIRED:
                raise
            warnings.append(f"no SELECT grant on {table}: {name} not loaded")
            return []

    # ---- reference data -------------------------------------------------------------------------------------------------
    cat_names = {int(r["course_cat_id"]): one_line(r["course_cat_name"]) for r in fetch("categories")}
    settings = {int(r["campus_id"]): r for r in fetch("settings") if r.get("campus_id") is not None}
    campuses: dict[int, Campus] = {}
    for r in fetch("campuses"):
        cid = int(r["campus_id"])
        st = settings.get(cid, {})
        phones = tuple(dict.fromkeys(x for x in (one_line(r.get("campus_phone1")), one_line(r.get("campus_phone2")), one_line(st.get("settings_phone_number")),
                                                 one_line(st.get("settings_phone_number2")), one_line(st.get("settings_mobile"))) if x))
        emails = tuple(dict.fromkeys(x for x in (one_line(st.get("settings_email")), one_line(r.get("campus_email1")), one_line(r.get("campus_email2"))) if x))
        campuses[cid] = Campus(id=cid, name=one_line(r["campus_name"]), short_code=one_line(r.get("short_code")), website=one_line(r.get("campus_website")),
                               address=one_line(clean_html(r.get("campus_address"))), location=one_line(clean_html(r.get("campus_location"))),
                               description=clean_html(r.get("campus_description")), admissions_email=emails[0] if emails else "", phones=phones, emails=emails)
    schools = {int(r["division_id"]): School(id=int(r["division_id"]), campus_id=int(r["campus_id"] or 0), name=one_line(r["division_name"]),
                                             description=clean_html(r.get("division_description"))) for r in fetch("schools")}
    dept_pages: dict[tuple[int, int], str] = {}
    for r in fetch("department_pages"):
        dept_pages[(int(r["department_id"]), int(r["submenu_id"]))] = clean_html(r.get("dept_spec_description"))
    dept_rows = {int(r["department_id"]): r for r in fetch("departments")}

    # ---- programmes ----------------------------------------------------------------------------------------------------
    program_rows = fetch("programs")
    loaded = {int(r["course_id"]): r for r in program_rows}
    placements_raw: dict[int, list[Placement]] = defaultdict(list)
    for r in fetch("placements"):
        cid = int(r["course_id"])
        if cid in loaded:
            pl = Placement(int(r["campus_id"] or 0), int(r["department_id"]) if r.get("department_id") else None)
            if pl not in placements_raw[cid]:
                placements_raw[cid].append(pl)

    # sections by program
    secs: dict[int, list[Section]] = defaultdict(list)
    docs: dict[int, list[tuple[str, DocumentRef, str | None]]] = defaultdict(list)
    spec_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    expired_count: dict[int, int] = defaultdict(int)

    def site(pid: int) -> str:
        r = loaded[pid]
        cm = campuses.get(int(r["campus_id"] or 0)) or next((campuses[p.campus_id] for p in placements_raw.get(pid, []) if p.campus_id in campuses), None)
        return cm.website if cm else ""

    for r in fetch("sections"):
        pid = int(r["course_id"])
        rid = int(r["rel_link_id"])
        if pid not in loaded or rid in cfg.hide_ids:
            continue
        title = one_line(clean_html(r.get("rel_link_name")))
        cat, typ = (r.get("rel_link_category") or "").upper(), (r.get("rel_link_type") or "").upper()
        raw = r.get("rel_link_description") or ""
        if cat == "A":
            if typ == "T":
                spec_rows[pid].append(r)
            continue
        if typ == "F":                                               # attached file -> document reference (R only)
            if cat == "R" and title and not re.search(r"\bfees?\b|important dates?", title, re.I):
                m = _FILE.search(clean_html(raw))
                if m:
                    docs[pid].append((title, DocumentRef(kind=document_kind(title), filename=m.group(1), url=_file_url(site(pid), m.group(1))), detect_year(title)))
            continue
        if typ != "T":
            continue
        if cat == "E":
            kind = SectionKind.ELIGIBILITY
        elif cat == "O":
            kind = SectionKind.HIGHLIGHTS
        elif cat == "R":
            kind = classify_r(title, cfg)
            if kind is None:
                continue
        else:
            continue
        text = clean_html(raw)
        if not text:
            continue
        for href in _HREF_PDF.findall(raw):                           # PDF links inside the text
            name = href.rsplit("/", 1)[-1]
            if _REAL_PDF.match(name):
                docs[pid].append((title, DocumentRef(kind="other", filename=name, url=_file_url(site(pid), name)), None))
        exp = _date(r.get("rel_link_expire_date"))
        year = detect_year(title)
        aud = detect_audience(title)
        if kind == SectionKind.NRI:
            aud = Audience.NRI
        elif kind == SectionKind.INTERNATIONAL:
            aud = Audience.INTERNATIONAL
        secs[pid].append(Section(kind=kind, title=title, text=text, tables=parse_tables(text), academic_year=year, audience=aud, expires_on=exp,
                                 provenance=Provenance("replica", "course_related_links", rid, url=None, content_hash=content_hash(title, text))))

    for r in fetch("details"):
        pid = int(r["intCourseID"])
        if pid not in loaded:
            continue
        kind = DETAIL_KIND.get(int(r["chrType"]))
        title, desc = one_line(clean_html(r.get("chrTitle"))), clean_html(r.get("chrDescription"))
        text = title if not desc or desc == title else (f"{title}\n{desc}" if title else desc)
        if kind and text:
            secs[pid].append(Section(kind=kind, title=title[:120], text=text, provenance=Provenance("replica", "course_info", int(r["intAutoNo"]), content_hash=content_hash(text))))

    # shared sections (course_common_links)
    shared: list[Section] = []
    shared_for: dict[int, frozenset[int]] = {}
    for r in fetch("shared_sections"):
        name = one_line(clean_html(r.get("chrName")))
        targets = [i for i in ints(r.get("course_ids")) if i in loaded]
        if not targets or shared_excluded(name, cfg) or (r.get("chrType") or "T").upper() != "T":
            continue
        kind = classify_r(name, cfg) or (SectionKind.NRI if re.search(r"\bnri\b", name, re.I) else SectionKind.INTERNATIONAL if re.search(r"international", name, re.I) else None)
        if kind is None:
            continue
        text = clean_html(r.get("chrDescription"))
        if not text:
            continue
        aud = Audience.NRI if kind == SectionKind.NRI else Audience.INTERNATIONAL if kind == SectionKind.INTERNATIONAL else detect_audience(name)
        s = Section(kind=kind, title=name, text=text, tables=parse_tables(text), academic_year=detect_year(name), audience=aud, shared=True,
                    provenance=Provenance("replica", "course_common_links", int(r["intAutoNo"]), content_hash=content_hash(name, text)))
        shared.append(s)
        shared_for[len(shared) - 1] = frozenset(targets)
        for pid in targets:
            secs[pid].append(s)

    # roadmap and curriculum
    phases: dict[tuple[int, int], list[RoadmapPhase]] = defaultdict(list)
    for r in fetch("roadmap"):
        pid = int(r["course_id"])
        if pid in loaded:
            phases[(pid, int(r["specID"] or 0))].append(RoadmapPhase(title=one_line(clean_html(r.get("chrTitle"))), term_range=one_line(r.get("term_range")),
                                                                    description=clean_html(r.get("chrDescription")) or clean_html(r.get("chrShortDesc")),
                                                                    career_roles=split_list(r.get("chrOutcomes")), published_package=one_line(r.get("chrPackage")) or None))
    details_by_master: dict[int, list[Subject]] = defaultdict(list)
    for r in fetch("curriculum_details"):
        name = one_line(clean_html(r.get("chrSubject")))
        if name:
            details_by_master[int(r["curriculum_id"])].append(Subject(name=name, category=one_line(r.get("chrCategory")), credits=_num(r.get("credits"))))
    curriculum: dict[int, list[Semester]] = defaultdict(list)
    for r in fetch("curriculum_master"):
        pid = int(r["course_id"])
        if pid in loaded:
            m = re.search(r"\d+", str(r.get("chrSem") or ""))
            curriculum[pid].append(Semester(year=one_line(r.get("chrYear")), term_type=one_line(r.get("chrType")), number=int(m.group()) if m else 0,
                                            title=one_line(clean_html(r.get("chrTitle"))), subjects=tuple(details_by_master.get(int(r["intAutoNo"]), ()))))

    # ---- assemble programmes ---------------------------------------------------------------------------------------------
    programs: dict[int, Program] = {}
    alias_map: dict[str, set[int]] = defaultdict(set)
    by_name_prefix: list[tuple[str, int]] = []
    for pid, r in loaded.items():
        pls = placements_raw.get(pid) or []
        raw_campus = int(r["campus_id"] or 0)
        campus_inferred = raw_campus == 0
        campus_id = raw_campus or (pls[0].campus_id if pls else 0)
        dept_ids = ints(r.get("department_id"))
        dept_id = dept_ids[0] if dept_ids else (pls[0].department_id if pls else None)
        if not pls and campus_id:
            pls = [Placement(campus_id, dept_id)]
        dept_row = dept_rows.get(dept_id) if dept_id else None
        school_id = None
        if dept_row:
            sids = ints(dept_row.get("division_id"))
            school_id = next((s for s in sids if s in schools), None)
        website = (campuses.get(campus_id).website if campus_id in campuses else "")
        name = one_line(clean_html(r["course_name"]))
        code = one_line(r.get("chrCourseCode")) or None
        slug = one_line(r.get("urlparam")) or None
        brochure = cfg.brochures.get(pid) or (one_line(r.get("chrPgmBrochure")) if _REAL_PDF.match(one_line(r.get("chrPgmBrochure"))) else None)
        sec_list = drop_older_cycles(secs.get(pid, []))
        overview = clean_html(r.get("course_description"))
        if overview:
            sec_list.append(Section(kind=SectionKind.OVERVIEW, title="Overview", text=overview, provenance=Provenance("replica", "courses", pid, content_hash=content_hash(overview))))
        grouped: dict[SectionKind, list[Section]] = defaultdict(list)
        for s in sec_list:
            grouped[s.kind].append(s)
        # documents: latest cycle only for syllabus / structure; brochure from the course row or brochures.yaml
        dlist = _latest_docs(docs.get(pid, []))
        if brochure:
            dlist.append(DocumentRef(kind="brochure", filename=brochure, url=_file_url(website, brochure)))
        seen_f: set[str] = set()
        dlist = [d for d in dlist if not (d.filename in seen_f or seen_f.add(d.filename))]
        dlist = [DocumentRef(d.kind, d.filename, d.url, extracted.get(d.filename, ())) for d in dlist]
        # specializations with their roadmap
        specs: list[Specialization] = []
        used_spec: set[int] = set()
        for sr in spec_rows.get(pid, []):
            sid = int(sr["rel_link_id"])
            used_spec.add(sid)
            specs.append(Specialization(id=sid, name=one_line(clean_html(sr.get("rel_link_name"))), description=clean_html(sr.get("rel_link_description")),
                                        note=clean_html(sr.get("short_description")) or None, roadmap=tuple(phases.get((pid, sid), ()))))
        for (ppid, sid), ph in phases.items():
            if ppid == pid and sid not in used_spec:
                specs.append(Specialization(id=sid, name="Programme roadmap", description="", roadmap=tuple(ph)))
        rules = tuple(x for x in cfg.eligibility_rules if x.program_id == pid)
        expired = sum(1 for x in sec_list if x.expires_on and x.expires_on < today and not (x.provenance and x.provenance.row_id in cfg.current_ids and x.provenance.table == "course_related_links"))
        quality = DataQuality(missing=tuple(k for k in EXPECTED if k not in grouped), no_erp_id=not (_num(r.get("course_web_prog_id"))),
                              campus_inferred=campus_inferred, expired_sections=expired, unreviewed_eligibility=not rules)
        aliases = tuple(dict.fromkeys(a for a in (code, slug) if a))
        cls_ = classify_programme(name, level_of(cat_names.get(int(r["course_cat_id"]))))
        prog = Program(
            id=pid, name=name, code=code, aliases=aliases, level=level_of(cat_names.get(int(r["course_cat_id"]))), category_id=int(r["course_cat_id"]),
            campus_id=campus_id, department_id=dept_id, school_id=school_id,
            erp_program_id=str(int(r["course_web_prog_id"])) if _num(r.get("course_web_prog_id")) else None,
            duration_years=_num(r.get("intDuration")), term_type=TERM.get((r.get("chrTermType") or "").upper()), semesters=int(r["intSemester"]) if _num(r.get("intSemester")) else None,
            highlights=split_list(r.get("chrOthers")), placement_highlights=split_list(r.get("chrPlacement")),
            summary=clean_html(r.get("course_shortdesc")), overview=overview, url=program_url(website, pid),
            sections={k: tuple(v) for k, v in grouped.items()}, specializations=tuple(specs),
            curriculum=tuple(sorted(curriculum.get(pid, []), key=lambda s: (s.year, s.number))), documents=tuple(dlist), eligibility_rules=rules, quality=quality,
            placements=tuple(pls), specialization_count=int(r["intSpecialisations"]) if _num(r.get("intSpecialisations")) else None,
            brochure_file=brochure, offline_flag=int(r["offline"]) if r.get("offline") is not None else None, source_modified=_date(r.get("course_modified_date")),
            degree_type=cls_.degree_type, academic_level=cls_.academic_level, field=cls_.field, specialization=cls_.specialization, subjects=cls_.subjects,
            entry=cls_.entry, dedupe_key=norm_key(name))
        programs[pid] = prog
        by_name_prefix.append((name.lower(), pid))
        for a in aliases:
            alias_map[norm_name(a)].add(pid)
    for a in cfg.aliases:
        key = norm_name(str(a.get("alias", "")))
        ids = set(int(i) for i in a.get("program_ids") or ())
        if a.get("name"):
            pref = str(a["name"]).lower()
            ids |= {pid for nm, pid in by_name_prefix if nm.startswith(pref)}
        if key and ids:
            alias_map[key] |= ids
            for pid in ids:
                p = programs[pid]
                programs[pid] = _with_alias(p, str(a["alias"]))

    # ---- departments, topics ---------------------------------------------------------------------------------------------
    referenced = {p.department_id for p in programs.values() if p.department_id} | {pl.department_id for p in programs.values() for pl in p.placements if pl.department_id}
    departments: dict[int, Department] = {}
    for did, r in dept_rows.items():
        if did not in referenced:
            continue
        sids = ints(r.get("division_id"))
        departments[did] = Department(id=did, campus_id=int(r["campus_id"] or 0), school_id=next((s for s in sids if s in schools), None), name=one_line(clean_html(r["department_name"])),
                                      code=one_line(r.get("department_code")) or None, intro=clean_html(r.get("introduction")) or clean_html(r.get("shortDesc")),
                                      overview_page=dept_pages.get((did, 1)) or None, placement_page=dept_pages.get((did, 9)) or None,
                                      email=one_line(r.get("department_email")) or None, phone=one_line(r.get("department_phone1")) or None)
    topics: dict[str, list[TopicItem]] = defaultdict(list)
    for r in fetch("facilities"):
        name = one_line(clean_html(r.get("cam_fecilities_name")))
        body, short = clean_html(r.get("cam_fecilities_description")), clean_html(r.get("cam_fecilities_short_description"))
        is_link = (r.get("cam_fecilities_menutype") or "").upper() == "U"
        link = body if is_link and body.startswith("http") else None
        text = short if is_link else (body or short)
        if not name or not ((len(text) > 30) or link):
            continue
        topics[facility_topic(name, cfg) or "facility"].append(TopicItem(topic=facility_topic(name, cfg) or "facility", campus_id=int(r["campus_id"]), title=name, text=text, tables=parse_tables(text), link=link,
                                                                         provenance=Provenance("replica", "campus_fecilities", int(r["cam_fecilities_id"]), content_hash=content_hash(name, text))))
    wanted = cfg.topic_content_ids
    if wanted:
        pages = {int(r["content_id"]): r for r in fetch("topic_pages", tuple(wanted))}
        for topic, entries in cfg.topics.items():
            for e in entries:
                r = pages.get(int(e["content_id"]))
                if not r:
                    warnings.append(f"topic page {e['content_id']} ({topic}) not found or inactive")
                    continue
                text = clean_html(r.get("content_description")) or clean_html(r.get("content_shortdesc"))
                cid = int(e["campus_id"]) if e.get("campus_id") is not None else (int(r["campus_id"]) if r.get("campus_id") else None)
                if text:
                    topics[topic].append(TopicItem(topic=topic, campus_id=cid, title=one_line(clean_html(r.get("content_title"))), text=text, tables=parse_tables(text),
                                                   provenance=Provenance("replica", "content", int(r["content_id"]), content_hash=content_hash(text))))

    # ---- indexes ---------------------------------------------------------------------------------------------------------
    chunks: list[Chunk] = []

    def add(text: str, title: str, kind: str, **kw):
        for part in chunk_text(text):
            chunks.append(Chunk(id=len(chunks), text=part, title=title, kind=kind, **kw))

    for pid, p in programs.items():
        for kind, ss in p.sections.items():
            for s in ss:
                if s.shared:
                    continue
                add(s.text, f"{p.name} - {s.title}", kind.value, program_id=pid, url=p.url, provenance=s.provenance,
                    expired=bool(s.expires_on and s.expires_on < today))
        for sp in p.specializations:
            add(f"{sp.name}\n{sp.description}", f"{p.name} - {sp.name}", "specialization", program_id=pid, url=p.url)
        for d in p.documents:
            for s in d.extracted:
                add(s.text, f"{p.name} - {d.filename}", "document", program_id=pid, url=d.url, provenance=s.provenance)
    for i, s in enumerate(shared):
        add(s.text, s.title, s.kind.value, program_ids=shared_for[i], provenance=s.provenance)
    for topic, items in topics.items():
        for it in items:
            add(it.text, it.title, "topic", topic=topic, campus_id=it.campus_id, provenance=it.provenance)
    ix = build_indexes(programs, {k: tuple(v) for k, v in topics.items()}, alias_map, lambda p: p.level, chunks)

    counts = {"programs": len(programs), "sections": sum(len(v) for p in programs.values() for v in p.sections.values()), "shared_sections": len(shared),
              "campuses": len(campuses), "departments": len(departments), "topic_items": sum(len(v) for v in topics.values()), "chunks": len(chunks),
              "documents": sum(len(p.documents) for p in programs.values()), "eligibility_rules": len(cfg.eligibility_rules)}
    erp_missing = sorted(p.id for p in programs.values() if p.quality.no_erp_id)
    if erp_missing:
        warnings.append(f"{len(erp_missing)} programmes have no ERP program id (course_web_prog_id): {erp_missing[:10]}{'...' if len(erp_missing) > 10 else ''}")
    cat = Catalog(programs=programs, campuses=campuses, departments=departments, schools=schools, topics={k: tuple(v) for k, v in topics.items()},
                  shared_sections=tuple(shared), indexes=ix, categories=cat_names,
                  build=BuildInfo(built_at=now, source_checksums=_safe_checksums(reader), counts=counts, warnings=tuple(warnings)))
    problems = check_catalog(cat, reader)
    if problems:
        raise CatalogCheckFailed(problems)
    return cat


class CatalogCheckFailed(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _safe_checksums(reader: RowReader) -> dict[str, int]:
    try:
        return reader.checksums()
    except Exception:  # noqa: BLE001  a missing CHECKSUM privilege must not block a build
        return {}


def _file_url(website: str | None, filename: str) -> str:
    return f"{(website or '').rstrip('/')}/uploads/userfiles/{quote(filename)}" if website else ""


def _latest_docs(items: list[tuple[str, DocumentRef, str | None]]) -> list[DocumentRef]:
    """Keep the latest cycle per document kind (syllabus / structure); undated ones stay."""
    best: dict[str, int] = {}
    for _t, d, y in items:
        v = year_value(y)
        if v:
            best[d.kind] = max(best.get(d.kind, 0), v)
    return [d for _t, d, y in items if not year_value(y) or year_value(y) >= best.get(d.kind, 0)]


def _with_alias(p: Program, alias: str) -> Program:
    from dataclasses import replace
    return p if alias in p.aliases else replace(p, aliases=p.aliases + (alias,))


_FEE_TITLE = re.compile(r"\bfees?\b|important dates?|application (open|close) date", re.I)


def check_catalog(cat: Catalog, reader: RowReader | None = None) -> list[str]:
    """Build-time proof that the catalog holds no fee, date or personal-data content. Returns the problems (empty = clean)."""
    problems: list[str] = []
    from .queries import ALLOWED_TABLES
    if reader is not None:
        extra = reader.tables_read() - ALLOWED_TABLES
        if extra:
            problems.append(f"tables outside the catalog allow-list were read: {sorted(extra)}")
    for p in cat.programs.values():
        for kind, ss in p.sections.items():
            for s in ss:
                if _FEE_TITLE.search(s.title or ""):
                    problems.append(f"programme {p.id}: section '{s.title}' looks like a fee or dates section")
        if re.search(r"\bfees?\b", " ".join(k.value for k in p.sections)):
            problems.append(f"programme {p.id}: a fee section kind is present")
    for s in cat.shared_sections:
        if _FEE_TITLE.search(s.title or ""):
            problems.append(f"shared section '{s.title}' looks like a fee or dates section")
    return problems[:20]

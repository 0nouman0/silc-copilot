"""`CatalogueSource` backed by the in-memory catalog: every programme fact is served from memory, with no per-request SQL.

Same dict shapes as `MySqlSource`, so the legacy tools, pages and decision engine keep working. Differences by design (blueprint):
no fee sections, no application dates, no intake figures, and the general admission process comes from the shared "Application /
Admission / Selection" sections of the current cycle instead of the 2016-2020 `adm_links` text.
"""
from __future__ import annotations

import re
from typing import Any

from ..catalog.models import Catalog, Program, Section, SectionKind
from ..catalog.refresh import get_catalog, version
from .base import SourceUnavailable

LEGACY_KIND = {"eligibility": SectionKind.ELIGIBILITY, "selection": SectionKind.SELECTION_PROCESS, "outcomes": SectionKind.OUTCOMES,
               "structure": SectionKind.PROGRAMME_STRUCTURE, "entrance_test": SectionKind.ENTRANCE_TEST, "placements": SectionKind.PLACEMENT,
               "scholarship": SectionKind.SCHOLARSHIP}
HIGHLIGHT_KIND = {SectionKind.WHY_CHOOSE: "why_choose", SectionKind.WHAT_YOU_LEARN: "what_you_will_learn", SectionKind.CAREERS: "careers",
                  SectionKind.MODULES: "modules", SectionKind.CAREER_ROLES: "career_roles"}
_NOT_PAGES = {SectionKind.OVERVIEW, *HIGHLIGHT_KIND}


def _split(text: str) -> tuple[str, str]:
    first, _, rest = (text or "").partition("\n")
    return first.strip(), rest.strip()


class CatalogSource:
    name = "catalog"

    @property
    def version(self) -> int:
        return version()

    def _cat(self) -> Catalog:
        return get_catalog()

    def ping(self) -> bool:
        try:
            return bool(self._cat().programs)
        except SourceUnavailable:
            return False

    # ------------------------------------------------------------------ shaping
    def _rows(self, p: Program, cat: Catalog) -> list[dict[str, Any]]:
        level = (cat.categories.get(p.category_id) or p.level).title()
        out = []
        for pl in (p.placements or ()) or ():
            cm = cat.campuses.get(pl.campus_id)
            d = cat.departments.get(pl.department_id) if pl.department_id else cat.departments.get(p.department_id) if p.department_id else None
            out.append(self._shape(p, level, cm.name if cm else None, d.name if d else None))
        return out or [self._shape(p, level, cat.campuses[p.campus_id].name if p.campus_id in cat.campuses else None,
                                   cat.departments[p.department_id].name if p.department_id in cat.departments else None)]

    @staticmethod
    def _shape(p: Program, level: str, campus: str | None, department: str | None) -> dict[str, Any]:
        return {"course_id": p.id, "name": p.name, "code": p.code, "level": level, "campus": campus, "department": department, "duration_years": p.duration_years,
                "term_type": p.term_type, "credits": None, "approved_intake": None, "application_open": None, "application_close": None,
                "record_last_modified": p.source_modified.isoformat() if p.source_modified else None}

    # ------------------------------------------------------------------ CatalogueSource
    def list_catalogue(self) -> list[dict[str, Any]]:
        cat = self._cat()
        rows = [r for p in cat.programs.values() for r in self._rows(p, cat)]
        return sorted(rows, key=lambda r: (r["name"], r["course_id"]))

    def search_courses(self, query: str, level: str | None, campus: str | None, limit: int) -> list[dict[str, Any]]:
        toks = re.findall(r"[a-z0-9&]+", (query or "").lower())[:6]
        rows = []
        for r in self.list_catalogue():
            name = r["name"].lower()
            if toks and not all(re.search(rf"\b{re.escape(t)}\b", name) for t in toks):
                continue
            if level and not (r["level"] or "").lower().startswith(level.lower()[:5]):
                continue
            if campus and campus.lower() not in (r["campus"] or "").lower():
                continue
            rows.append(r)
        return sorted(rows, key=lambda r: (len(r["name"]), r["name"]))[:max(1, min(limit, 50))]

    def get_course(self, course_id: int) -> dict[str, Any] | None:
        cat = self._cat()
        p = cat.programs.get(course_id)
        if not p:
            return None
        out = self._rows(p, cat)[0]
        sel = (p.sections.get(SectionKind.SELECTION_PROCESS) or (None,))[0]
        out.update(summary=p.summary or None, overview=p.overview or None, selection_process=sel.text if sel else None, brochure=p.brochure_file, url=p.url)
        return out

    def get_sections(self, course_id: int, kind: str | None) -> list[dict[str, Any]]:
        p = self._cat().programs.get(course_id)
        if not p or kind == "fees":                       # fees are ERP-only: never served from the catalog
            return []
        if kind is None:
            kinds = [k for k in p.sections if k not in _NOT_PAGES]
        else:
            k = LEGACY_KIND.get(kind) or (SectionKind(kind) if kind in {x.value for x in SectionKind} else None)
            kinds = [k] if k else []
        out, seen = [], set()
        for k in kinds:
            for s in sorted(p.sections.get(k, ()), key=lambda s: (-(int(s.academic_year[:4]) if s.academic_year else 0))):
                key = (s.title.lower(), s.text[:60])
                if key not in seen:
                    seen.add(key)
                    out.append({"section": s.title, "text": s.text, "added": None, "academic_year": s.academic_year, "audience": s.audience.value,
                                "expires_on": s.expires_on.isoformat() if s.expires_on else None})
        return out

    def get_highlights(self, course_id: int) -> list[dict[str, Any]]:
        p = self._cat().programs.get(course_id)
        if not p:
            return []
        out = []
        for k, label in HIGHLIGHT_KIND.items():
            for s in p.sections.get(k, ()):
                title, rest = _split(s.text)
                out.append({"kind": label, "title": title.replace("\n", " "), "text": rest})
        return out

    def get_curriculum(self, course_id: int, year: str | None) -> list[dict[str, Any]]:
        p = self._cat().programs.get(course_id)
        if not p:
            return []
        sems = [s for s in p.curriculum if not year or year.lower() in s.year.lower()]
        return [{"year": s.year, "term": str(s.number), "focus": s.title, "subjects": [{"category": x.category, "subject": x.name, "credits": x.credits} for x in s.subjects]}
                for s in sems[:12]]

    def get_roadmap(self, course_id: int) -> list[dict[str, Any]]:
        p = self._cat().programs.get(course_id)
        if not p:
            return []
        return [{"specialisation_ref": sp.id, "phase": ph.title, "terms": ph.term_range, "summary": ph.description} for sp in p.specializations for ph in sp.roadmap]

    def get_admission_process(self, deanery: str | None) -> list[dict[str, Any]]:
        cat = self._cat()
        pool: list[Section] = list(cat.shared_sections)
        if not any(s.kind in (SectionKind.APPLICATION_PROCESS, SectionKind.ADMISSION_PROCESS) for s in pool):   # shared blocks not loaded: use programme-level ones
            pool += [s for p in cat.programs.values() for k in (SectionKind.APPLICATION_PROCESS, SectionKind.ADMISSION_PROCESS, SectionKind.SELECTION_PROCESS, SectionKind.ADMISSION_QUERIES)
                     for s in p.sections.get(k, ()) if not s.shared]
        out, seen = [], set()
        for s in pool:
            if s.kind not in (SectionKind.APPLICATION_PROCESS, SectionKind.ADMISSION_PROCESS, SectionKind.SELECTION_PROCESS, SectionKind.ADMISSION_QUERIES):
                continue
            if deanery and deanery.lower() not in s.title.lower():
                continue
            key = (s.title.lower(), s.text[:80])
            if key in seen:
                continue
            seen.add(key)
            out.append({"group": s.title, "how_to_apply": s.text if s.kind in (SectionKind.APPLICATION_PROCESS, SectionKind.ADMISSION_PROCESS) else "", "eligibility": "",
                        "selection": s.text if s.kind == SectionKind.SELECTION_PROCESS else "", "admission_dates": "",
                        "contact_note": s.text if s.kind == SectionKind.ADMISSION_QUERIES else ""})
        return out[:12]

    def list_campuses(self) -> list[dict[str, Any]]:
        return [{"campus_id": c.id, "name": c.name} for c in sorted(self._cat().campuses.values(), key=lambda c: c.id)]

    def list_campus_details(self) -> list[dict[str, Any]]:
        return [{"campus_id": c.id, "name": c.name, "address": c.address or None, "phones": list(c.phones), "emails": list(c.emails or ((c.admissions_email,) if c.admissions_email else ())),
                 "website": c.website or None, "description": c.description or None} for c in sorted(self._cat().campuses.values(), key=lambda c: c.id)]

    def list_facilities(self, campus_id: int | None) -> list[dict[str, Any]]:
        cat = self._cat()
        out = []
        for items in cat.topics.values():
            for it in items:
                if it.provenance and it.provenance.table != "campus_fecilities":
                    continue
                if campus_id is not None and it.campus_id != campus_id:
                    continue
                cm = cat.campuses.get(it.campus_id) if it.campus_id is not None else None
                out.append({"campus_id": it.campus_id, "campus": cm.name if cm else "", "facility": it.title, "text": it.text, "link": it.link})
        return sorted(out, key=lambda r: (r["campus_id"] or 0, r["facility"]))

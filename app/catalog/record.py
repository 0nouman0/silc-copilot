"""The clean, structured programme record: one consistent shape for every programme, built only from stored data.

Missing information stays missing (None / empty list); nothing is invented. Fees are always `{"available": False}` here because fees are
live ERP data, never website text.
"""
from __future__ import annotations

from typing import Any

from .clean import looks_like_role
from .models import Catalog, Program, SectionKind

ADMISSION_KINDS = (SectionKind.APPLICATION_PROCESS, SectionKind.ADMISSION_PROCESS, SectionKind.SELECTION_PROCESS, SectionKind.ENTRANCE_TEST, SectionKind.ADMISSION_QUERIES)


def programme_record(cat: Catalog, p: Program) -> dict[str, Any]:
    dept = cat.departments.get(p.department_id) if p.department_id else None
    campuses = sorted({cat.campuses[pl.campus_id].name for pl in p.placements if pl.campus_id in cat.campuses}
                      | ({cat.campuses[p.campus_id].name} if p.campus_id in cat.campuses else set()))
    roles = [r for s in p.sections.get(SectionKind.CAREERS, ()) + p.sections.get(SectionKind.CAREER_ROLES, ()) for r in [s.text.split("\n")[0][:90]] if looks_like_role(r)]
    return {
        "programme_id": p.id, "programme_name": p.name, "degree_type": p.degree_type, "academic_level": p.academic_level, "field": p.field or None,
        "specialization": p.specialization, "entry": p.entry, "department": dept.name if dept else None, "campus": campuses,
        "duration_years": p.duration_years, "term_type": p.term_type,
        "eligibility": [{"title": s.title, "text": s.text, "academic_year": s.academic_year, "audience": s.audience.value,
                         "expires_on": s.expires_on.isoformat() if s.expires_on else None} for s in p.sections.get(SectionKind.ELIGIBILITY, ())],
        "fees": {"available": False, "note": "Fees are live admissions data and are not part of the programme record."},
        "programme_description": p.summary or p.overview or None,
        "curriculum": [{"year": s.year, "term": s.number, "title": s.title, "subjects": [{"name": x.name, "category": x.category, "credits": x.credits} for x in s.subjects]}
                       for s in p.curriculum],
        "career_opportunities": list(dict.fromkeys(roles)),
        "admission_details": [{"kind": s.kind.value, "title": s.title, "text": s.text, "academic_year": s.academic_year} for k in ADMISSION_KINDS for s in p.sections.get(k, ())],
        "url": p.url or None, "duplicate_listing_ids": [x.id for x in cat.programs.values() if x.dedupe_key == p.dedupe_key and x.entry == p.entry and x.id != p.id],
    }

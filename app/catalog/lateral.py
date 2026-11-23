"""Lateral entry as a normalised admission route, built ONLY from what the database states.

Audit of the 476 listings (379 programmes): lateral entry appears in two verifiable ways.
  * a separate listing named "... - (Lateral Entry)" (its own course id, its own eligibility section), and
  * a "Lateral Entry: ..." paragraph inside a programme's eligibility text.
Nothing else is assumed: a B.Tech programme is NOT lateral-entry capable because it is a B.Tech, a diploma holder is NOT eligible unless the
stored eligibility text says so, and fields the database does not hold (entry year, documents, procedure) stay None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Catalog, Program, SectionKind

_LATERAL_LINE = re.compile(r"lateral|diploma|direct(ly)?\s+(admission\s+)?(to|into|in)\s+(the\s+)?(2nd|second|3rd|third)", re.I)
_ENTRY_YEAR = re.compile(r"\b((?:2nd|second|3rd|third)[- ]year|(?:3rd|third) semester|semester\s*(?:3|III)\b)", re.I)
_PROCESS_KINDS = (SectionKind.APPLICATION_PROCESS, SectionKind.ADMISSION_PROCESS, SectionKind.SELECTION_PROCESS, SectionKind.ADMISSION_QUERIES, SectionKind.ENTRANCE_TEST)


@dataclass(frozen=True)
class LateralEntry:
    programme_id: int                 # the listing to open (the lateral listing when there is one, else the regular programme)
    listing_id: int | None            # the "(Lateral Entry)" listing id, if it exists
    regular_listing_id: int | None
    name: str
    degree_type: str
    campus_ids: tuple[int, ...]
    available: bool = True
    evidence: tuple[str, ...] = ()    # "listing" and/or "eligibility_text"
    eligibility: str | None = None    # verbatim text about who can apply by lateral entry
    eligibility_source: str | None = None
    entry_year: str | None = None     # only if the text states it
    admission_process: str | None = None
    unspecified: tuple[str, ...] = field(default=())
    url: str | None = None


def _paragraphs(text: str) -> str | None:
    """The lines of an eligibility text that talk about lateral entry / diplomas, verbatim."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    hit = [ln for ln in lines if _LATERAL_LINE.search(ln)]
    return "\n".join(hit) if hit else None


def _first(p: Program, kind: SectionKind):
    ss = p.sections.get(kind, ())
    return ss[0] if ss else None


def _build(cat: Catalog) -> dict[str, LateralEntry]:
    by_key: dict[str, list[Program]] = {}
    for p in cat.programs.values():
        by_key.setdefault(p.dedupe_key, []).append(p)
    out: dict[str, LateralEntry] = {}
    for key, rows in by_key.items():
        lat = next((p for p in rows if p.entry == "lateral"), None)
        reg = next((p for p in sorted(rows, key=lambda p: p.id) if p.entry == "regular"), None)
        evidence, elig, src = [], None, None
        if lat is not None:
            evidence.append("listing")
            e = _first(lat, SectionKind.ELIGIBILITY)
            if e is not None:
                elig, src = _paragraphs(e.text) or e.text, f"{lat.name} · {e.title}" + (f" ({e.academic_year})" if e.academic_year else "")
        if reg is not None:
            for s in reg.sections.get(SectionKind.ELIGIBILITY, ()):
                para = _paragraphs(s.text)
                if para and "lateral" in para.lower():
                    evidence.append("eligibility_text")
                    if elig is None:
                        elig, src = para, f"{reg.name} · {s.title}" + (f" ({s.academic_year})" if s.academic_year else "")
                    break
        if not evidence:
            continue
        base = lat or reg
        texts = " ".join(s.text for p in (lat, reg) if p is not None for k in (SectionKind.ELIGIBILITY, SectionKind.OVERVIEW) for s in p.sections.get(k, ()))
        m = _ENTRY_YEAR.search(elig or "") if elig else None
        year = m.group(1) if m else None
        process = None
        for p in (lat, reg):
            for k in _PROCESS_KINDS:
                for s in (p.sections.get(k, ()) if p else ()):
                    if re.search(r"lateral", s.text, re.I):
                        process = s.text[:1500]
                        break
        missing = tuple(n for n, v in (("entry year or semester", year), ("admission procedure", process), ("required documents", None), ("eligibility details", elig)) if not v)
        campuses = tuple(sorted({pl.campus_id for p in rows for pl in p.placements} | {p.campus_id for p in rows} - {0}))
        out[key] = LateralEntry(programme_id=base.id, listing_id=lat.id if lat else None, regular_listing_id=reg.id if reg else None, name=base.name, degree_type=base.degree_type,
                                campus_ids=campuses, evidence=tuple(dict.fromkeys(evidence)), eligibility=elig, eligibility_source=src, entry_year=year, admission_process=process,
                                unspecified=missing, url=base.url or None)
        del texts
    return out


_cache: dict[int, dict[str, LateralEntry]] = {}


def lateral_index(cat: Catalog) -> dict[str, LateralEntry]:
    """dedupe_key -> LateralEntry for every programme with verifiable lateral entry (the catalog is immutable, so this is computed once per build)."""
    k = id(cat)
    if k not in _cache:
        _cache.clear()
        _cache[k] = _build(cat)
    return _cache[k]


def lateral_for(cat: Catalog, programme_id: int | None) -> LateralEntry | None:
    p = cat.programs.get(programme_id) if programme_id else None
    return lateral_index(cat).get(p.dedupe_key) if p else None


def audit(cat: Catalog) -> dict:
    """Numbers for the data audit: listings, lateral listings, text-only mentions, and which fields the data lacks."""
    idx = lateral_index(cat)
    return {"listings": len(cat.programs), "lateral_listings": sum(1 for p in cat.programs.values() if p.entry == "lateral"), "programmes_with_lateral_entry": len(idx),
            "by_evidence": {"listing": sum(1 for e in idx.values() if "listing" in e.evidence), "eligibility_text_only": sum(1 for e in idx.values() if e.evidence == ("eligibility_text",))},
            "with_entry_year": sum(1 for e in idx.values() if e.entry_year), "with_admission_process": sum(1 for e in idx.values() if e.admission_process),
            "degree_types": sorted({e.degree_type for e in idx.values()})}

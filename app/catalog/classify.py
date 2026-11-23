"""Section typing: kind from the CMS category code plus the reviewed title allow-list, academic year and audience detection."""
from __future__ import annotations

import re

from .config import CatalogConfig
from .models import Audience, Section, SectionKind

_YEAR = re.compile(r"(?<!\d)(20\d\d)(?:\s*[-–/]\s*(20)?(\d\d))?(?!\d)")


def detect_year(*texts: str | None) -> str | None:
    """Academic year named in a title, e.g. 'Eligibility-2027' -> '2027', 'Syllabus 2026-27' -> '2026-27'."""
    for t in texts:
        m = _YEAR.search(t or "")
        if m:
            return f"{m.group(1)}-{m.group(3)}" if m.group(3) else m.group(1)
    return None


def year_value(y: str | None) -> int | None:
    """Comparable value of a detected year ('2026-27' counts as 2027)."""
    if not y:
        return None
    if "-" in y:
        a, b = y.split("-")
        return int(a[:2] + b[-2:])
    return int(y)


def detect_audience(title: str | None) -> Audience:
    t = (title or "").lower()
    if re.search(r"\bnri\b|non[- ]resident", t):
        return Audience.NRI
    if re.search(r"international|foreign", t):
        return Audience.INTERNATIONAL
    return Audience.ALL


def classify_r(title: str, cfg: CatalogConfig) -> SectionKind | None:
    """Kind for a category-R title, or None when it is excluded or not on the allow-list."""
    t = (title or "").strip()
    if not t or any(p.search(t) for p in cfg.exclude):
        return None
    for pat, kind in cfg.rules:
        if pat.search(t):
            return kind
    return None


def shared_excluded(title: str, cfg: CatalogConfig) -> bool:
    return any(p.search(title or "") for p in cfg.shared_exclude)


def drop_older_cycles(sections: list[Section]) -> list[Section]:
    """Remove a section that names an older cycle when a newer one of the same kind (and audience) exists."""
    best: dict[tuple, int] = {}
    for s in sections:
        v = year_value(s.academic_year)
        if v is not None:
            k = (s.kind, s.audience)
            best[k] = max(best.get(k, 0), v)
    return [s for s in sections
            if year_value(s.academic_year) is None or year_value(s.academic_year) >= best.get((s.kind, s.audience), 0)]


def document_kind(title: str) -> str:
    t = (title or "").lower()
    if "placement" in t:
        return "placement_brochure"
    if re.search(r"brou?chure", t):
        return "brochure"
    if "syllabus" in t:
        return "syllabus"
    if re.search(r"structure|summary", t):
        return "programme_structure"
    return "other"


def facility_topic(name: str, cfg: CatalogConfig) -> str | None:
    n = (name or "").lower()
    for topic, words in cfg.facility_topics.items():
        if any(w in n for w in words):
            return topic
    return None

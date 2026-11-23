"""Build-time draft rules from the published eligibility text. Run once, then admissions reviews every draft.

    python -m app.eligibility.extract            # writes config/eligibility_drafts.yaml

A draft is NEVER used by the checker: it has no reviewed_by / reviewed_on. Admissions approves, edits or rejects each draft and
copies the approved ones into config/eligibility_rules.yaml. The extractor is a conservative pattern reader; `llm_refine` can be
passed a completion function to let a model propose the same schema (still a draft, still reviewed).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from ..catalog.config import CONFIG_DIR
from ..catalog.models import Audience, Catalog, Program, SectionKind

_PCT = re.compile(r"(\d{2}(?:\.\d+)?)\s*(?:%|per\s*cent|percent|percentage)", re.I)
_CGPA = re.compile(r"(?:cgpa|gpa)[^0-9]{0,25}(\d(?:\.\d+)?)", re.I)
_SUBJECTS = ["Mathematics", "Physics", "Chemistry", "Biology", "English", "Computer Science", "Economics", "Accountancy", "Business Studies", "Statistics"]
_TESTS = ["CUET", "CAT", "MAT", "GMAT", "CLAT", "NET", "GATE", "CHRIST entrance test", "entrance test"]


def _qualification(text: str, program: Program) -> str:
    t = text.lower()
    if re.search(r"master'?s? degree|post[- ]?graduate degree|\bpg degree|m\.?(tech|com|sc|a)\b|\bmba\b|\bmca\b", t):
        return "PG degree"
    if re.search(r"bachelor|graduat|\bb\.?(tech|com|sc|a|e)\b|\bbba\b|\bbca\b|\bug degree", t):
        return "UG degree"
    if re.search(r"10\s*\+\s*2|12th|class (12|xii)|\+2|puc|pre[- ]university|higher secondary|senior secondary", t):
        return "10+2"
    return {"UG": "10+2", "PG": "UG degree", "PhD": "PG degree"}.get(program.level, "")


def draft_for(program: Program, title: str, text: str, audience: Audience, year: str | None) -> dict[str, Any]:
    pcts = [float(x) for x in _PCT.findall(text)]
    cg = _CGPA.findall(text)
    subjects = [s for s in _SUBJECTS if re.search(rf"\b{re.escape(s)}\b", text, re.I)]
    tests = [t for t in _TESTS if re.search(rf"\b{re.escape(t)}\b", text, re.I) and t != "entrance test"]
    return {"program_id": program.id, "audience": audience.value, "academic_year": year or "", "min_qualification": _qualification(text, program),
            "min_percent": min(pcts) if pcts else None, "min_cgpa": float(cg[0]) if cg else None, "required_subjects": subjects, "accepted_boards": None,
            "entrance_tests": tests, "relaxations": {}, "notes": "DRAFT: check against the source text below", "reviewed_by": None, "reviewed_on": None,
            "_programme": program.name, "_source_title": title, "_source_text": text[:1500], "_all_percentages_found": pcts}


def draft_rules(cat: Catalog, llm_refine: Callable[[dict, str], dict] | None = None) -> list[dict[str, Any]]:
    """One draft per programme and audience that has eligibility text (plus the shared NRI/international blocks)."""
    out: list[dict[str, Any]] = []
    for p in cat.programs.values():
        by_aud: dict[Audience, tuple] = {}
        for s in p.sections.get(SectionKind.ELIGIBILITY, ()):
            by_aud.setdefault(s.audience, (s.title, s.text, s.academic_year))
        for s in p.sections.get(SectionKind.NRI, ()) + p.sections.get(SectionKind.INTERNATIONAL, ()):
            by_aud.setdefault(s.audience, (s.title, s.text, s.academic_year))
        for aud, (title, text, year) in by_aud.items():
            d = draft_for(p, title, text, aud, year)
            out.append(llm_refine(d, text) if llm_refine else d)
    return out


def main() -> int:
    from ..catalog.refresh import get_catalog
    cat = get_catalog()
    drafts = draft_rules(cat)
    path = Path(CONFIG_DIR) / "eligibility_drafts.yaml"
    path.write_text("# DRAFT eligibility rules for admissions review. Not used by the checker.\n# Copy approved entries to eligibility_rules.yaml and set reviewed_by / reviewed_on.\n"
                    + yaml.safe_dump({"drafts": drafts}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(json.dumps({"drafts": len(drafts), "written_to": str(path)}))
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(main())

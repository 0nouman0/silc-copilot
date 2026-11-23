"""Human-reviewed configuration (YAML in git): loaded once per build, never from the database."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .models import Audience, EligibilityRule, Provenance, SectionKind

CONFIG_DIR = Path(os.getenv("CATALOG_CONFIG_DIR") or Path(__file__).resolve().parents[2] / "config")


def _load(name: str, directory: Path | None = None) -> dict[str, Any]:
    p = (directory or CONFIG_DIR) / name
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class CatalogConfig:
    exclude: tuple[re.Pattern, ...] = ()
    rules: tuple[tuple[re.Pattern, SectionKind], ...] = ()
    shared_exclude: tuple[re.Pattern, ...] = ()
    topics: dict[str, list[dict]] = field(default_factory=dict)          # topic -> [{content_id, campus_id}]
    facility_topics: dict[str, list[str]] = field(default_factory=dict)
    aliases: tuple[dict, ...] = ()
    brochures: dict[int, str] = field(default_factory=dict)
    current_ids: frozenset[int] = frozenset()
    hide_ids: frozenset[int] = frozenset()
    eligibility_rules: tuple[EligibilityRule, ...] = ()
    marks: dict[str, Any] = field(default_factory=dict)

    @property
    def topic_content_ids(self) -> list[int]:
        return sorted({int(e["content_id"]) for items in self.topics.values() for e in items})


def parse_rule(d: dict[str, Any]) -> EligibilityRule | None:
    """A rule counts only when admissions has reviewed it (reviewed_by and reviewed_on both set)."""
    if not d.get("reviewed_by") or not d.get("reviewed_on"):
        return None
    ro = d["reviewed_on"]
    if isinstance(ro, str):
        ro = date.fromisoformat(ro)
    return EligibilityRule(
        program_id=int(d["program_id"]), audience=Audience(d.get("audience", "all")), academic_year=str(d.get("academic_year", "")),
        min_qualification=str(d.get("min_qualification", "")), min_percent=_f(d.get("min_percent")), min_cgpa=_f(d.get("min_cgpa")),
        required_subjects=tuple(d.get("required_subjects") or ()),
        accepted_boards=tuple(d["accepted_boards"]) if d.get("accepted_boards") else None,
        entrance_tests=tuple(d.get("entrance_tests") or ()), relaxations=dict(d.get("relaxations") or {}), notes=str(d.get("notes") or ""),
        reviewed_by=str(d["reviewed_by"]), reviewed_on=ro,
        source_section=Provenance(source="config", table="eligibility_rules.yaml", row_id=int(d["program_id"])))


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def load_config(directory: Path | None = None) -> CatalogConfig:
    sk = _load("section_kinds.yaml", directory)
    tm = _load("topic_mapping.yaml", directory)
    ov = _load("section_overrides.yaml", directory)
    rules = (parse_rule(r) for r in (_load("eligibility_rules.yaml", directory).get("rules") or []))
    return CatalogConfig(
        exclude=tuple(re.compile(p, re.I) for p in sk.get("exclude", [])),
        rules=tuple((re.compile(r["pattern"], re.I), SectionKind(r["kind"])) for r in sk.get("rules", [])),
        shared_exclude=tuple(re.compile(p, re.I) for p in sk.get("shared_exclude", [])),
        topics={k: list(v or []) for k, v in (tm.get("topics") or {}).items()},
        facility_topics={k: [w.lower() for w in v] for k, v in (tm.get("facility_topics") or {}).items()},
        aliases=tuple(_load("aliases.yaml", directory).get("aliases") or ()),
        brochures={int(k): str(v) for k, v in (_load("brochures.yaml", directory).get("brochures") or {}).items()},
        current_ids=frozenset(int(x) for x in ov.get("current") or ()), hide_ids=frozenset(int(x) for x in ov.get("hide") or ()),
        eligibility_rules=tuple(r for r in rules if r is not None), marks=_load("marks_normalization.yaml", directory))

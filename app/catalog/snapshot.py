"""Optional JSONL snapshot of the catalog (one line per programme) and the diff between two builds."""
from __future__ import annotations

import json
from pathlib import Path

from .models import Catalog


def _digest(p) -> str:
    h = [s.provenance.content_hash for ss in p.sections.values() for s in ss if s.provenance]
    return "|".join(sorted(h)) + f"#{len(p.curriculum)}#{len(p.specializations)}#{len(p.documents)}#{p.name}#{p.campus_id}"


def diff_programs(old: Catalog | None, new: Catalog) -> tuple[list[int], list[int], list[int]]:
    """(added, updated, removed) programme ids between two catalogs."""
    if old is None:
        return sorted(new.programs), [], []
    added = sorted(set(new.programs) - set(old.programs))
    removed = sorted(set(old.programs) - set(new.programs))
    updated = sorted(pid for pid in set(new.programs) & set(old.programs) if _digest(new.programs[pid]) != _digest(old.programs[pid]))
    return added, updated, removed


def write_snapshot(cat: Catalog, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_build": {"built_at": cat.build.built_at.isoformat(), "counts": dict(cat.build.counts), "warnings": list(cat.build.warnings)}}) + "\n")
        for pid, pr in sorted(cat.programs.items()):
            f.write(json.dumps({"id": pid, "name": pr.name, "level": pr.level, "campus_id": pr.campus_id, "url": pr.url, "digest": _digest(pr),
                                "sections": {k.value: len(v) for k, v in pr.sections.items()}, "quality": {"missing": [k.value for k in pr.quality.missing],
                                "expired_sections": pr.quality.expired_sections, "no_erp_id": pr.quality.no_erp_id}}) + "\n")

"""Extracted PDF text, cached on disk by file hash. Produced offline by `python -m app.documents.build`; read by the catalog builder.

Extraction never runs at chat time. If app servers cannot write to disk, run the build in CI and commit/ship `data/documents/extracted/`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..catalog.models import Provenance, Section, SectionKind

ROOT = Path(os.getenv("DOCUMENTS_DIR") or Path(__file__).resolve().parents[2] / "data" / "documents")
EXTRACTED = ROOT / "extracted"
FILES = ROOT / "files"


def _name(filename: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)


def save_extracted(filename: str, sha256: str, kind: str, sections: list[dict]) -> Path:
    EXTRACTED.mkdir(parents=True, exist_ok=True)
    p = EXTRACTED / f"{_name(filename)}.json"
    p.write_text(json.dumps({"filename": filename, "sha256": sha256, "kind": kind, "sections": sections}, ensure_ascii=False), encoding="utf-8")
    return p


def cached_hash(filename: str) -> str | None:
    p = EXTRACTED / f"{_name(filename)}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("sha256")
    except (OSError, ValueError):
        return None


def load_extracted() -> dict[str, tuple[Section, ...]]:
    """filename -> sections with provenance (file, page). Brochure/PDF text ranks below the website sections and never carries fees or dates."""
    out: dict[str, tuple[Section, ...]] = {}
    if not EXTRACTED.exists():
        return out
    for p in sorted(EXTRACTED.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = SectionKind.PROGRAMME_STRUCTURE if d.get("kind") in ("programme_structure", "syllabus") else SectionKind.MISC
        out[d["filename"]] = tuple(
            Section(kind=kind, title=f"{d['filename']} p.{s['page']}", text=s["text"],
                    provenance=Provenance(source="brochure", file=d["filename"], page=s["page"], content_hash=d.get("sha256", "")[:16]))
            for s in d.get("sections", []) if s.get("text"))
    return out

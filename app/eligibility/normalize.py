"""Marks and board normalisation (conversion factors come from config/marks_normalization.yaml, to be confirmed by admissions)."""
from __future__ import annotations

import re
from typing import Any

from ..catalog.config import _load

QUAL_RANK = {"10+2": 1, "UG degree": 2, "PG degree": 3}
_QUAL_ALIASES = {
    "10+2": ("10+2", "12th", "class 12", "class xii", "plus two", "+2", "puc", "pre-university", "pre university", "senior secondary", "intermediate", "higher secondary", "xii"),
    "UG degree": ("ug degree", "undergraduate degree", "bachelor", "graduate", "graduation", "ug", "degree", "b.tech", "btech", "b.com", "bcom", "b.sc", "bsc", "b.a", "bba", "bca"),
    "PG degree": ("pg degree", "postgraduate degree", "master", "post graduate", "pg", "m.tech", "mtech", "m.com", "mcom", "m.sc", "msc", "mba", "mca"),
}


def marks_config() -> dict[str, Any]:
    return _load("marks_normalization.yaml")


def normalize_qualification(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    for canon in ("PG degree", "UG degree", "10+2"):         # most specific first ("post graduate" contains "graduate")
        if any(a == t or a in t for a in _QUAL_ALIASES[canon]):
            return canon
    return None


def normalize_board(board: str | None) -> str | None:
    b = re.sub(r"[^a-z]", "", (board or "").lower())
    table = {"cbse": "CBSE", "icse": "ICSE", "isc": "ISC", "state": "STATE", "stateboard": "STATE", "ib": "IB", "internationalbaccalaureate": "IB",
             "cambridge": "CAMBRIDGE", "igcse": "IGCSE", "alevel": "CAMBRIDGE", "alevels": "CAMBRIDGE"}
    if not b:
        return None
    for k, v in table.items():
        if b == k or k in b:
            return v
    return "STATE" if any(s in b for s in ("karnataka", "kerala", "tamil", "andhra", "telangana", "maharashtra", "puc", "board")) else "OTHER"


def is_foreign_board(board: str | None) -> bool:
    nb = normalize_board(board)
    return nb is not None and nb in set(marks_config().get("foreign_boards", ["IB", "CAMBRIDGE", "IGCSE", "OTHER"]))


def to_percent(percent: float | None, cgpa: float | None, scale: int | None) -> float | None:
    """The applicant's marks as a percentage: a given percentage wins, else CGPA converted on its scale."""
    if percent is not None:
        return float(percent)
    if cgpa is None:
        return None
    cfg = marks_config()
    if (scale or 10) == 4:
        return round(cgpa * float(cfg.get("cgpa4_factor", 25.0)), 2)
    return round(cgpa * float(cfg.get("cgpa10_factor", 9.5)), 2)


def norm_subject(s: str) -> str:
    t = re.sub(r"[^a-z ]", "", (s or "").lower()).strip()
    return {"maths": "mathematics", "math": "mathematics", "bio": "biology", "chem": "chemistry", "phy": "physics", "cs": "computer science",
            "comp sci": "computer science", "eco": "economics", "accounts": "accountancy", "accounting": "accountancy"}.get(t, t)

"""Topic page candidates for the admissions/web-team review list (never loaded into the catalog).

    python -m app.catalog.candidates      # writes config/topic_candidates.yaml

Lists active `content` pages in the admission-relevant categories (or page ids 4, 6, 7, 8), rejecting titles that name an older cycle.
Reviewers move approved content ids into config/topic_mapping.yaml.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from .classify import detect_year, year_value
from .config import CONFIG_DIR
from .reader import MySqlReader

TOPIC_WORDS = {
    "hostel": r"hostel|accommodation|residen", "dining": r"dining|mess|canteen|cafeteria|food", "scholarship": r"scholarship",
    "nri": r"\bnri\b", "international": r"international", "cancellation_refund": r"cancellation|refund", "transport": r"transport|bus",
    "anti_ragging": r"ragging", "grievance": r"grievance", "placement": r"placement", "sports_clubs": r"sport|club", "medical": r"medical|health",
    "library": r"library", "contact": r"contact",
}


def candidates(rows: list[dict], min_year: int = 2025) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in TOPIC_WORDS}
    for r in rows:
        title = (r.get("content_title") or "").strip()
        y = year_value(detect_year(title))
        if y is not None and y < min_year:        # "Admissions 2020" and similar are rejected
            continue
        for topic, rx in TOPIC_WORDS.items():
            if re.search(rx, title, re.I):
                out[topic].append({"content_id": int(r["content_id"]), "campus_id": r.get("campus_id"), "title": title, "category": r.get("content_category"),
                                   "updated": str(r.get("content_updateddate") or "")})
    return {k: v for k, v in out.items() if v}


def main() -> int:
    reader = MySqlReader.from_env()
    if reader is None:
        print("no database account configured (AGENT_DB_USER / AGENT_DB_PASSWORD)")
        return 1
    found = candidates(reader.rows("topic_candidates"))
    path = Path(CONFIG_DIR) / "topic_candidates.yaml"
    path.write_text("# Candidates for review: move approved {content_id, campus_id} entries into topic_mapping.yaml\n" + yaml.safe_dump({"candidates": found}, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    print({k: len(v) for k, v in found.items()}, "->", path)
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(main())

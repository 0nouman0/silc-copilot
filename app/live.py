"""Live ERP facts: fees, application dates, seats, hostel vacancy, applicant status.

DEFERRED. The ERP client and the aggregation layer (precedence, TTL cache, circuit breaker, conflict log) are a later phase, so every
live tool currently answers `available=False` with the admissions contact. This is the same envelope the real layer will return, so
no tool, prompt or test changes when it is connected: replace the bodies below with calls through the aggregation layer.

The catalog never holds any of these values, and the assistant must never state one from memory.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .catalog.clean import clean_html, parse_tables
from .catalog.classify import detect_year, year_value
from .catalog.refresh import current_catalog, get_reader

NOT_CONNECTED = "Live admissions data (fees, dates, seats) is not connected to this assistant yet."


@dataclass(frozen=True)
class LiveFact:
    field: str                    # "fees" | "application_dates" | "seats" | "hostel_vacancy" | "application_status"
    value: Any = None             # as returned by the ERP, normalised; None when unavailable
    source: str = "erp"
    as_of: datetime | None = None
    available: bool = False
    note: str | None = None       # e.g. "ERP timed out; contact admissions"

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "value": self.value, "source": self.source, "as_of": self.as_of.isoformat() if self.as_of else None,
                "available": self.available, "note": self.note}


def admissions_contact(campus_id: int | None = None) -> dict[str, Any]:
    """Email and phones of the campus admissions office (or of every campus), from the catalog."""
    cat = current_catalog()
    if cat is None:
        return {}
    rows = [c for c in cat.campuses.values() if campus_id is None or c.id == campus_id]
    return {"campuses": [{"campus": c.name, "email": c.admissions_email or None, "phones": list(c.phones), "website": c.website or None} for c in rows]}


def _unavailable(field: str, what: str, campus_id: int | None = None) -> LiveFact:
    return LiveFact(field=field, available=False, as_of=datetime.now(timezone.utc),
                    note=f"{NOT_CONNECTED} {what} Please contact the admissions office.")


# ---------------------------------------------------------------------------------------------- fees
# FEES_SOURCE=database (default): the fee structure published for the programme in the CHRIST database (course_related_links, category F), shown exactly
# as stored with its academic year and a clear note when its stored validity date has passed. FEES_SOURCE=none: always "not available".
# When the ERP is connected it becomes the primary source and this stays the fallback (see config/source_of_truth.yaml).
FEE_TTL_S = 900.0


@dataclass(frozen=True)
class FeeRecord:
    row_id: int
    course_id: int
    title: str
    text: str
    academic_year: str | None
    expires_on: date | None
    added: date | None

    def stale(self, today: date | None = None) -> bool:
        return bool(self.expires_on and self.expires_on < (today or date.today()))


_fee_cache: dict[int, tuple[float, FeeRecord | None]] = {}


def clear_fee_cache() -> None:
    _fee_cache.clear()


def fees_enabled() -> bool:
    return (os.getenv("FEES_SOURCE") or "database").lower() == "database"


def _d(v: Any) -> date | None:
    s = str(v) if v else ""
    if not s or s.startswith("0000"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def fee_records(ids: Iterable[int]) -> dict[int, FeeRecord | None]:
    """The current published fee structure per course id (newest academic year, then newest row), cached. Missing = None, never zero."""
    want = [int(i) for i in dict.fromkeys(ids)]
    now = time.monotonic()
    missing = [i for i in want if i not in _fee_cache or now - _fee_cache[i][0] > FEE_TTL_S]
    if missing and fees_enabled():
        reader = get_reader()
        if reader is not None:
            rows = reader.rows("fees", tuple(missing))
            best: dict[int, FeeRecord] = {}
            for r in rows:
                text = clean_html(r.get("rel_link_description"))
                if not text:
                    continue
                title = clean_html(r.get("rel_link_name")).strip()
                rec = FeeRecord(int(r["rel_link_id"]), int(r["course_id"]), title, text, detect_year(title), _d(r.get("rel_link_expire_date")), _d(r.get("rel_link_added_date")))
                cur = best.get(rec.course_id)
                if cur is None or (year_value(rec.academic_year) or 0, rec.row_id) > (year_value(cur.academic_year) or 0, cur.row_id):
                    best[rec.course_id] = rec
            for i in missing:
                _fee_cache[i] = (now, best.get(i))
    return {i: (_fee_cache.get(i) or (0, None))[1] for i in want}


def _listing_ids(program_id: int) -> list[int]:
    """The programme's own course id first, then other listings of the same programme (same name, same entry mode)."""
    cat = current_catalog()
    p = cat.programs.get(program_id) if cat else None
    if p is None:
        return [program_id]
    return [program_id] + [x.id for x in cat.programs.values() if x.dedupe_key == p.dedupe_key and x.entry == p.entry and x.id != program_id]


def fee_for(program_id: int) -> FeeRecord | None:
    recs = fee_records(_listing_ids(program_id))
    return next((recs[i] for i in _listing_ids(program_id) if recs.get(i)), None)


def stale_note(rec: FeeRecord) -> str:
    bits = ["As published in the CHRIST programme database" + (f" for {rec.academic_year}" if rec.academic_year else "") + "."]
    if rec.stale():
        bits.append(f"The stored validity date ({rec.expires_on.isoformat()}) has passed, so please confirm the current fees with the admissions office.")
    return " ".join(bits)


def fee_year1(rec: FeeRecord) -> list[tuple[str, str]]:
    """Year-1 amounts per applicant category from the stored fee table, as stored (e.g. [("Karnataka", "3,00,000 INR"), ...])."""
    for t in parse_tables(rec.text):
        head = [h.lstrip("# ").strip() for h in t.header]
        if not head or not head[0].lower().startswith("year"):
            continue
        row = next((r for r in t.rows if r and re.fullmatch(r"(year\s*)?1", r[0].strip(), re.I)), None)
        if row is None:
            continue
        cols = [(h, v) for h, v in zip(head[1:], row[1:]) if v and "payment" not in h.lower()]
        if cols:
            return cols
    return []


def fee_summary(rec: FeeRecord, categories: int = 3) -> str:
    """One compact line for a list: year-1 amounts for the first categories of the stored table (the full table is shown for a single programme)."""
    cols = fee_year1(rec)
    if cols:
        return "Year 1: " + " · ".join(f"{h} {v}" for h, v in cols[:categories]) + (" · …" if len(cols) > categories else "")
    first = " ".join(rec.text.split())
    return first[:140] + ("…" if len(first) > 140 else "")


def get_fees(program_id: int, audience: str | None = None, year: str | None = None) -> LiveFact:
    if not fees_enabled():
        return _unavailable("fees", "Fee information is switched off for this assistant.")
    if get_reader() is None:
        return _unavailable("fees", "No programme database is connected, so no fee structure can be read.")
    try:
        rec = fee_for(program_id)
    except Exception:  # noqa: BLE001  database unreachable: say so, never guess
        return _unavailable("fees", "The fee data could not be read just now.")
    if rec is None:
        return LiveFact(field="fees", available=False, source="website_database", as_of=datetime.now(timezone.utc),
                        note="No fee structure is recorded for this programme in the CHRIST programme database. Please contact the admissions office.")
    return LiveFact(field="fees", source="website_database", available=True, as_of=datetime.now(timezone.utc), note=stale_note(rec),
                    value={"title": rec.title, "academic_year": rec.academic_year, "text": rec.text, "summary": fee_summary(rec), "expires_on": rec.expires_on.isoformat() if rec.expires_on else None,
                           "validity_passed": rec.stale(), "row_id": rec.row_id, "course_id": rec.course_id})


def get_application_dates(program_id: int) -> LiveFact:
    return _unavailable("application_dates", "Dates recorded on the website are out of date and are not used.")


def get_seats(program_id: int) -> LiveFact:
    return _unavailable("seats", "Seat and intake numbers are not available from the website data.")


def get_hostel_vacancy(campus_id: int | None = None) -> LiveFact:
    return _unavailable("hostel_vacancy", "Contact the hostel office for vacancy.", campus_id)


def get_application_status() -> LiveFact:
    return LiveFact(field="application_status", available=False, as_of=datetime.now(timezone.utc),
                    note="Application status is only available to a logged-in applicant on the university portal; this assistant cannot see applications.")

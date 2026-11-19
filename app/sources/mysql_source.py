"""Read-only MySQL source. Every query here is predefined and parameterised; nothing the model says is ever
concatenated into SQL. The account (`agent_ro`) has SELECT on public-content tables only, the session is forced
read-only, and each query has a row cap and a server-side time limit.
"""
from __future__ import annotations

import os
import re
from typing import Any

from .base import SourceUnavailable
from .text import html_to_text

ROW_CAP = 200
CATALOGUE_CAP = 2000   # the whole offered-programme list is read in one query
MAX_MS = 5000

# Section kinds -> SQL LIKE patterns on course_related_links.rel_link_name
SECTION_PATTERNS: dict[str, list[str]] = {
    "eligibility": ["Eligib%"],
    "fees": ["Fee%"],
    "selection": ["Selection%"],
    "outcomes": ["%Outcome%"],
    "structure": ["%Structure%"],
    "entrance_test": ["%Entrance%"],
    "placements": ["Placement%"],
    "scholarship": ["Merit Scholarship%", "Scholarship%"],
}
LEVELS = {"undergraduate": "UNDERGRADUATE", "postgraduate": "POSTGRADUATE", "doctoral": "DOCTORAL%", "international": "International%"}
HIGHLIGHT_TYPES = {1: "why_choose", 2: "what_you_will_learn", 3: "careers", 4: "modules"}


_FILE_ONLY = re.compile(r"[^\n]{1,200}\.(pdf|docx?|pptx?|xlsx?)", re.I)


def _like(term: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", term) + "%"


class MySqlSource:
    name = "mysql"

    def __init__(self, host: str, port: int, db: str, user: str, password: str):
        self._cfg = dict(host=host, port=port, database=db, user=user, password=password)

    @classmethod
    def from_env(cls) -> "MySqlSource | None":
        user, pw = os.getenv("AGENT_DB_USER"), os.getenv("AGENT_DB_PASSWORD")
        if not user or not pw:
            return None
        return cls(os.getenv("APPLICANT_DB_HOST", "127.0.0.1"), int(os.getenv("APPLICANT_DB_PORT", "3306")),
                   os.getenv("APPLICANT_DB_NAME", "applicant"), user, pw)

    # ------------------------------------------------------------------ plumbing
    def _rows(self, sql: str, params: tuple = (), cap: int = ROW_CAP) -> list[dict[str, Any]]:
        try:
            import pymysql
            from pymysql.cursors import DictCursor
            conn = pymysql.connect(**self._cfg, charset="utf8mb4", cursorclass=DictCursor, connect_timeout=3, read_timeout=6,
                                   autocommit=True)
        except Exception as e:  # noqa: BLE001
            raise SourceUnavailable(f"database unreachable: {type(e).__name__}") from e
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME={MAX_MS}")
                cur.execute(sql, params)
                return list(cur.fetchmany(cap))
        except Exception as e:  # noqa: BLE001
            raise SourceUnavailable(f"query failed: {type(e).__name__}") from e
        finally:
            conn.close()

    def ping(self) -> bool:
        try:
            return bool(self._rows("SELECT 1 AS ok"))
        except SourceUnavailable:
            return False

    @staticmethod
    def _iso(d: Any) -> str | None:
        s = str(d) if d else None
        return None if not s or s.startswith("0000") else s[:10]

    # ------------------------------------------------------------------ queries
    _COURSE = ("SELECT c.course_id, c.course_name, c.chrCourseCode, cc.course_cat_name AS level, cm.campus_name AS campus, "
               "d.department_name AS department, c.intDuration, c.chrTermType, c.intCredits, c.intApprovedIntake, "
               "c.course_appl_avail_on, c.course_appl_close_on, c.course_modified_date "
               "FROM courses c LEFT JOIN course_category cc ON cc.course_cat_id=c.course_cat_id "
               "LEFT JOIN campus_master cm ON cm.campus_id=c.campus_id LEFT JOIN departments d ON d.department_id=c.department_id ")

    def _shape(self, r: dict[str, Any]) -> dict[str, Any]:
        term = {"T": "trimester", "S": "semester", "Y": "year"}.get((r.get("chrTermType") or "").upper())
        return {"course_id": r["course_id"], "name": (r["course_name"] or "").strip(), "code": r.get("chrCourseCode") or None,
                "level": (r.get("level") or "").strip().title() or None, "campus": r.get("campus"), "department": r.get("department"),
                "duration_years": r.get("intDuration") or None, "term_type": term, "credits": r.get("intCredits") or None,
                "approved_intake": r.get("intApprovedIntake") or None,
                "application_open": self._iso(r.get("course_appl_avail_on")), "application_close": self._iso(r.get("course_appl_close_on")),
                "record_last_modified": self._iso(r.get("course_modified_date"))}

    _OFFERED = "c.course_status='Y' AND cc.course_cat_status='Y'"   # active course in a category the university currently offers

    def list_catalogue(self) -> list[dict[str, Any]]:
        """Every programme currently on offer (one row per campus listing), for name resolution and the browse pages."""
        rows = self._rows(self._COURSE + "WHERE " + self._OFFERED + " ORDER BY c.course_name, c.course_id", cap=CATALOGUE_CAP)
        return [self._shape(r) for r in rows]

    def search_courses(self, query: str, level: str | None, campus: str | None, limit: int) -> list[dict[str, Any]]:
        where: list[str] = [self._OFFERED]
        params: list[Any] = []
        for tok in re.findall(r"[A-Za-z0-9&]+", query or "")[:6]:
            where.append("c.course_name REGEXP %s")  # whole-word match: 'mba' must not hit 'Mumbai'
            params.append(r"\b" + re.escape(tok) + r"\b")
        if level and level.lower() in LEVELS:
            where.append("cc.course_cat_name LIKE %s")
            params.append(LEVELS[level.lower()])
        if campus:
            where.append("cm.campus_name LIKE %s")
            params.append(_like(campus))
        sql = self._COURSE + "WHERE " + " AND ".join(where) + " ORDER BY CHAR_LENGTH(c.course_name), c.course_name LIMIT %s"
        return [self._shape(r) for r in self._rows(sql, (*params, max(1, min(limit, 50))))]

    def get_course(self, course_id: int) -> dict[str, Any] | None:
        rows = self._rows(self._COURSE + "WHERE c.course_id=%s AND c.course_status='Y'", (course_id,))
        if not rows:
            return None
        out = self._shape(rows[0])
        extra = self._rows("SELECT course_shortdesc, course_description, selection_process, chrPgmBrochure "
                           "FROM courses WHERE course_id=%s", (course_id,))
        if extra:
            e = extra[0]
            out.update(summary=html_to_text(e["course_shortdesc"]) or None, overview=html_to_text(e["course_description"]) or None,
                       selection_process=html_to_text(e["selection_process"]) or None, brochure=e.get("chrPgmBrochure") or None)
        return out

    def get_sections(self, course_id: int, kind: str | None) -> list[dict[str, Any]]:
        pats = SECTION_PATTERNS.get(kind or "", [])
        cond, params = "", []
        if pats:
            cond = " AND (" + " OR ".join("rel_link_name LIKE %s" for _ in pats) + ")"
            params = list(pats)
        rows = self._rows("SELECT rel_link_id, rel_link_name, rel_link_description, rel_link_added_date "
                          "FROM course_related_links WHERE course_id=%s AND rel_link_status='Y' AND rel_link_type IN ('T','F')"
                          + cond + " ORDER BY rel_link_id DESC", (course_id, *params))
        seen, out = set(), []
        for r in rows:  # newest active row per section name
            key = (r["rel_link_name"] or "").strip().lower()
            text = html_to_text(r["rel_link_description"])
            if key in seen or not text or _FILE_ONLY.fullmatch(text):  # skip empty rows and rows that only name an attached file
                continue
            seen.add(key)
            out.append({"section": r["rel_link_name"].strip(), "text": text, "added": self._iso(r["rel_link_added_date"])})
        return out

    def get_highlights(self, course_id: int) -> list[dict[str, Any]]:
        rows = self._rows("SELECT chrType, chrTitle, chrDescription FROM course_info WHERE intCourseID=%s AND status=1 ORDER BY intAutoNo", (course_id,))
        out = []
        for r in rows:
            try:
                kind = HIGHLIGHT_TYPES.get(int(r["chrType"]), "other")
            except (TypeError, ValueError):
                kind = "other"
            out.append({"kind": kind, "title": html_to_text(r["chrTitle"]).replace("\n", " "), "text": html_to_text(r["chrDescription"])})
        return out

    def get_curriculum(self, course_id: int, year: str | None) -> list[dict[str, Any]]:
        cond, params = "", []
        if year:
            cond, params = " AND m.chrYear LIKE %s", [_like(year)]
        masters = self._rows("SELECT m.intAutoNo, m.chrYear, m.chrSem, m.chrTitle FROM academic_curriculum_master m "
                             "WHERE m.course_id=%s AND m.chrStatus='1'" + cond + " ORDER BY m.chrYear, m.chrSem", (course_id, *params))
        out = []
        for m in masters[:12]:
            subs = self._rows("SELECT chrCategory, chrSubject, credits FROM academic_curriculum_details WHERE curriculum_id=%s ORDER BY intAutoNo", (m["intAutoNo"],))
            out.append({"year": m["chrYear"], "term": m["chrSem"], "focus": m["chrTitle"],
                        "subjects": [{"category": s["chrCategory"], "subject": (s["chrSubject"] or "").strip(), "credits": s["credits"]} for s in subs]})
        return out

    def get_roadmap(self, course_id: int) -> list[dict[str, Any]]:
        rows = self._rows("SELECT specID, chrTitle, chrShortDesc, term_range FROM programme_roadmap WHERE course_id=%s AND chrStatus='Y' ORDER BY specID, intAutoNo", (course_id,))
        return [{"specialisation_ref": r["specID"], "phase": r["chrTitle"], "terms": r["term_range"], "summary": html_to_text(r["chrShortDesc"])} for r in rows]

    def get_admission_process(self, deanery: str | None) -> list[dict[str, Any]]:
        cond, params = ("WHERE pgm_deanery LIKE %s", (_like(deanery),)) if deanery else ("", ())
        rows = self._rows("SELECT pgm_deanery, ap_desc, el_desc, se_desc, ad_desc, ca_desc FROM adm_links " + cond + " ORDER BY f_id LIMIT 12", params)
        return [{"group": (r["pgm_deanery"] or "").strip(), "how_to_apply": html_to_text(r["ap_desc"]), "eligibility": html_to_text(r["el_desc"]),
                 "selection": html_to_text(r["se_desc"]), "admission_dates": html_to_text(r["ad_desc"]), "contact_note": html_to_text(r["ca_desc"])} for r in rows]

    def list_campuses(self) -> list[dict[str, Any]]:
        rows = self._rows("SELECT campus_id, campus_name FROM campus_master WHERE campus_status='Y' ORDER BY campus_id")
        return [{"campus_id": r["campus_id"], "name": r["campus_name"]} for r in rows]

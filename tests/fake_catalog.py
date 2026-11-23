"""A synthetic replica for the catalog builder: the same row shapes the fixed SQL returns, no database needed."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.catalog.config import CatalogConfig, load_config
from app.sources.base import DataNotGranted

PAST, FUTURE = date(2026, 10, 1), date(2027, 6, 1)
TODAY = date(2026, 11, 22)


def _p(cid, name, cat, campus, dept, **kw):
    base = {"course_id": cid, "department_id": str(dept), "course_cat_id": cat, "campus_id": campus, "main_campus_deptID": dept, "chrCourseCode": None,
            "course_name": name, "course_shortdesc": "", "course_description": f"<p>{name} overview.</p>", "urlparam": None, "course_web_prog_id": 9000 + cid,
            "offline": 0, "intDuration": 2, "chrTermType": "T", "intSemester": 6, "intSpecialisations": 0, "chrOthers": None, "chrPlacement": None,
            "chrPgmBrochure": None, "course_modified_date": date(2026, 9, 1)}
    base.update(kw)
    return base


def _s(rid, cid, name, typ, desc, cat, expire=None, short=None):
    return {"rel_link_id": rid, "course_id": cid, "rel_link_name": name, "rel_link_type": typ, "rel_link_description": desc, "rel_link_category": cat,
            "rel_link_added_date": date(2026, 1, 1), "rel_link_expire_date": expire, "sort_order": rid, "short_description": short}


ROWS: dict[str, list[dict[str, Any]]] = {
    "categories": [{"course_cat_id": 1, "course_cat_name": "UNDERGRADUATE", "course_cat_code": "UG", "course_cat_description": ""},
                   {"course_cat_id": 2, "course_cat_name": "POSTGRADUATE", "course_cat_code": "PG", "course_cat_description": ""},
                   {"course_cat_id": 8, "course_cat_name": "DOCTORAL (PHD)", "course_cat_code": "PHD", "course_cat_description": ""}],
    "campuses": [{"campus_id": 1, "campus_name": "Bangalore Central Campus", "short_code": "BCC", "campus_website": "https://christuniversity.in", "campus_address": "<p>Hosur Road</p>",
                  "campus_location": "", "campus_description": "<p>Main campus</p>", "campus_email1": "mail@christuniversity.in", "campus_email2": None,
                  "campus_phone1": "080 40129100", "campus_phone2": None, "upload_folder": "x"},
                 {"campus_id": 24, "campus_name": "Bangalore Kengeri Campus", "short_code": "BKC", "campus_website": "https://bkc.christuniversity.in", "campus_address": "Kanmanike",
                  "campus_location": "", "campus_description": "", "campus_email1": "kengeri@christuniversity.in", "campus_email2": None,
                  "campus_phone1": "080 62689800", "campus_phone2": "08 40129828", "upload_folder": "y"}],
    "settings": [{"campus_id": 1, "settings_email": "admissions@christuniversity.in", "settings_phone_number": "080 40129000", "settings_phone_number2": None, "settings_mobile": None, "settings_address": ""}],
    "schools": [{"division_id": 5, "campus_id": 1, "division_name": "School of Business and Management", "division_description": "Business school"}],
    "departments": [{"department_id": 10, "campus_id": 1, "division_id": "5", "department_name": "Business and Management", "department_code": "BM", "shortDesc": "", "introduction": "<p>Intro</p>",
                     "department_email": "bm@christuniversity.in", "department_phone1": "", "department_url_param": "bm"},
                    {"department_id": 11, "campus_id": 24, "division_id": "", "department_name": "Computer Science", "department_code": "CS", "shortDesc": "", "introduction": "",
                     "department_email": None, "department_phone1": None, "department_url_param": "cs"}],
    "department_pages": [{"department_id": 10, "submenu_id": 9, "dept_spec_description": "<p>Placement page</p>"}],
    "programs": [
        _p(334, "Master of Business Administration (MBA)", 2, 1, 10, course_description="<p>MBA overview with <img src=\"data:image/png;base64,AAAA\"> finance.</p>", chrCourseCode="MBA27",
           chrOthers="Top recruiters`Industry mentors", intSpecialisations=2, chrPgmBrochure="MBA-brochure-2027.pdf"),
        _p(101, "Bachelor of Business Administration (BBA)", 1, 1, 10, intDuration=3, chrTermType="S", course_web_prog_id=0),
        _p(501, "Doctor of Philosophy in Management (PhD)", 8, 0, 10, intDuration=3, chrTermType="Y"),
        _p(601, "Master of Computer Applications (MCA)", 2, 24, 11),
    ],
    "placements": [{"department_id": 10, "course_id": 334, "campus_id": 1, "main_campus_deptID": 10}, {"department_id": 10, "course_id": 101, "campus_id": 1, "main_campus_deptID": 10},
                   {"department_id": 11, "course_id": 101, "campus_id": 24, "main_campus_deptID": 11}, {"department_id": 10, "course_id": 501, "campus_id": 1, "main_campus_deptID": 10},
                   {"department_id": 11, "course_id": 601, "campus_id": 24, "main_campus_deptID": 11}],
    "sections": [
        _s(1, 334, "Eligibility-2027", "T", "<p>Candidates with a Bachelor's degree with a minimum of 50% marks are eligible.</p>", "E", expire=PAST),
        _s(2, 334, "Eligibility-2025", "T", "<p>Old: minimum 45% marks.</p>", "E", expire=PAST),
        _s(3, 334, "Selection Process", "T", "<p>Selection is by the CHRIST entrance test, group discussion and interview.</p>", "R"),
        _s(4, 334, "Fee Structure - 2027", "T", "<p>Registration INR 8000. Programme fee 5,70,000 INR.</p>", "R"),
        _s(5, 334, "Photo Gallery", "U", "http://x", "R"),
        _s(6, 334, "Syllabus 2025-26", "F", "MBA-syllabus-2025.pdf", "R"),
        _s(7, 334, "Syllabus 2026-27", "F", "MBA-syllabus-2026.pdf", "R"),
        _s(8, 334, "Programme Structure", "F", "MBA-structure.pdf", "R"),
        _s(9, 334, "Admission Process 2025", "T", "<p>Old process.</p>", "R"),
        _s(10, 334, "Admission Process 2027", "T", "<p>Apply on the portal. See <a href=\"/uploads/userfiles/guide.pdf\">guide</a>.</p>", "R"),
        _s(11, 334, "Programme Highlights", "T", "<p>Industry projects.</p>", "O"),
        _s(12, 334, "Finance", "T", "<p>Finance track.</p>", "A", short="Track note"),
        _s(13, 334, "Fees 2027", "T", "<p>fees</p>", "F"),
        _s(14, 101, "Eligibility", "T", "<p>Pass the +2 level with 45% marks.</p>", "E", expire=FUTURE),
        _s(15, 101, "NRI Student Category", "T", "<p>NRI applicants should send a sponsor letter.</p>", "R"),
        _s(16, 501, "Programme Outcomes", "T", "<p>Research skills.</p>", "R"),
    ],
    "shared_sections": [
        {"intAutoNo": 1, "chrName": "Application Process 2027 (UG, PG)", "chrTemplateName": "t", "chrType": "T", "chrDescription": "<p>Register online and upload your documents.</p>", "course_ids": "334,101,99999", "campus_id": 1},
        {"intAutoNo": 2, "chrName": "MBA Important Dates - 2027", "chrTemplateName": "t", "chrType": "T", "chrDescription": "<p>dates</p>", "course_ids": "334", "campus_id": 1},
        {"intAutoNo": 3, "chrName": "Cancellation Policy", "chrTemplateName": "t", "chrType": "T", "chrDescription": "<p>Cancellation is possible before the term starts.</p>", "course_ids": "99999", "campus_id": 1},
    ],
    "details": [{"intAutoNo": 1, "chrType": 3, "intCourseID": 334, "chrTitle": "Business Analyst", "chrDescription": ""},
                {"intAutoNo": 2, "chrType": 1, "intCourseID": 334, "chrTitle": "Why choose", "chrDescription": "<p>Strong industry links.</p>"},
                {"intAutoNo": 3, "chrType": 5, "intCourseID": 334, "chrTitle": "Consultant", "chrDescription": ""}],
    "roadmap": [{"intAutoNo": 1, "course_id": 334, "specID": 12, "chrTitle": "Foundation", "chrDescription": "<p>Core</p>", "chrShortDesc": "", "term_range": "S1-S2",
                 "chrOutcomes": "Analyst`Associate", "chrPackage": "INR 6 LPA (as published)"}],
    "curriculum_master": [{"intAutoNo": 7, "course_id": 334, "campus_id": 1, "chrYear": "Year 1", "chrType": "T", "chrSem": "1", "chrTitle": "Core"}],
    "curriculum_details": [{"intAutoNo": 1, "curriculum_id": 7, "chrCategory": "Core", "chrSubject": "Managerial Economics", "credits": 3}],
    "facilities": [{"cam_fecilities_id": 1, "campus_id": 1, "cam_fecilities_name": "Accommodation", "cam_fecilities_menutype": "T",
                    "cam_fecilities_description": "<p>Hostel rooms are available for outstation students on campus.</p>", "cam_fecilities_short_description": ""},
                   {"cam_fecilities_id": 2, "campus_id": 24, "cam_fecilities_name": "Library", "cam_fecilities_menutype": "T",
                    "cam_fecilities_description": "<p>An up-to-date library with books in a variety of domains.</p>", "cam_fecilities_short_description": ""},
                   {"cam_fecilities_id": 3, "campus_id": 24, "cam_fecilities_name": "Gym", "cam_fecilities_menutype": "T", "cam_fecilities_description": "tiny", "cam_fecilities_short_description": ""}],
    "topic_pages": [],
}


class FakeReader:
    """Same two methods the builder uses on the real reader. `denied` lists query names whose table the account may not read."""

    def __init__(self, rows: dict | None = None, denied: set[str] | None = None, checksum: int = 1):
        self.data = {k: [dict(r) for r in v] for k, v in (rows or ROWS).items()}
        self.denied, self._sum, self._read = denied or set(), checksum, set()
        self.calls: list[str] = []

    def rows(self, name: str, params: tuple = ()):
        from app.catalog.queries import QUERIES
        self.calls.append(name)
        if name in self.denied:
            raise DataNotGranted(f"no SELECT grant ({name})")
        self._read.add(QUERIES[name][0])
        rows = [dict(r) for r in self.data.get(name, [])]
        if name == "fees" and params:
            rows = [r for r in rows if r["course_id"] in params]
        return rows

    def checksums(self):
        return {"courses": self._sum, "course_related_links": self._sum}

    def tables_read(self):
        return set(self._read)

    def bump(self):
        self._sum += 1


def cfg_with(**over) -> CatalogConfig:
    from dataclasses import replace
    return replace(load_config(), **over)


def build(reader=None, cfg=None, **kw):
    from app.catalog import build_catalog
    return build_catalog(reader or FakeReader(), cfg or load_config(), today=TODAY, **kw)

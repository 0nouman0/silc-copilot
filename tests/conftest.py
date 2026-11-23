import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.catalogue import reset_catalogue
from app.llm.base import LLMResponse, ToolCall
from app.main import app
from app.sources import DataNotGranted, SourceUnavailable, set_source

# application window relative to today so "open now / closed" logic never makes tests time-dependent
OPEN = (date.today() - timedelta(days=30)).isoformat()
CLOSE = (date.today() + timedelta(days=60)).isoformat()


def fmt(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def _row(cid, name, level, campus, *, dur=2, term="trimester", open_=OPEN, close=CLOSE, dept="Business and Management"):
    return {"course_id": cid, "name": name, "code": None, "level": level, "campus": campus, "department": dept, "duration_years": dur,
            "term_type": term, "credits": None, "approved_intake": 120, "application_open": open_, "application_close": close,
            "record_last_modified": "2026-10-07"}


CENTRAL, KENGERI = "Bangalore Central Campus", "Bangalore Kengeri Campus"
COURSES = [
    _row(334, "Master of Business Administration (MBA)", "Postgraduate", CENTRAL),
    _row(341, "Master of Business Administration (MBA) (Executive)", "Postgraduate", CENTRAL),
    _row(101, "Bachelor of Business Administration (BBA)", "Undergraduate", CENTRAL, dur=3, term="semester"),
    _row(102, "Bachelor of Business Administration (BBA)", "Undergraduate", KENGERI, dur=3, term="semester"),
    _row(501, "Doctor of Philosophy in Management (PhD)", "Doctoral (Phd)", CENTRAL, dur=3, term="year", open_=None, close=None),
    _row(201, "Bachelor of Commerce (B.Com.)", "Undergraduate", CENTRAL, dur=3, term="semester", dept="Commerce"),
    _row(601, "Master of Computer Applications (MCA)", "Postgraduate", CENTRAL, dept="Computer Science"),
]

FEES_MBA = ("1. Admission Registration Fee: INR 8000/- (Non-Refundable)\n2. Programme Fee (as per the table below)\n"
            "| Year | Karnataka | NRI |\n|---|---|---|\n| 1 | 5,70,000 INR | 6,70,000 INR |")
ELIG_MBA = ("Candidates who have passed a Bachelor's degree with a minimum of 50% marks in aggregate are eligible.\n"
            "Candidates in the final year may apply provisionally.")
SECTIONS = {
    334: {"eligibility": [("Eligibility-2027", ELIG_MBA)], "fees": [("Fee Structure - 2027", FEES_MBA)],
          "selection": [("Selection Process", "Selection is based on the CHRIST entrance test, group discussion and interview.")],
          "placements": [("Placements", "Top recruiters visit the campus every year.")]},
    341: {"fees": [("Fee Structure", FEES_MBA)]},
    101: {"eligibility": [("Eligibility", "Pass at the +2 level with 45% marks.")], "fees": [("Fees", FEES_MBA)]},
    102: {"eligibility": [("Eligibility", "Pass at the +2 level with 45% marks.")], "fees": [("Fees", FEES_MBA)]},
    201: {"eligibility": [("Eligibility", "Pass at the +2 level with 50% marks.")], "fees": [("Fees", FEES_MBA)]},
}
SECTION_KEY = {"eligibility": "eligibility", "fees": "fees", "selection": "selection", "placements": "placements"}
ADMISSION_STATUS_URL = "https://appstat.christuniversity.in"
PROCESS = [
    {"group": "ug comm", "how_to_apply": "Register on the application portal and apply online for undergraduate programmes.",
     "eligibility": "", "selection": "", "admission_dates": f"Results are published on the status page {ADMISSION_STATUS_URL} .", "contact_note": "The university collects no capitation fee."},
    {"group": "pg commer", "how_to_apply": "Register on the application portal and apply online for postgraduate programmes.",
     "eligibility": "", "selection": "", "admission_dates": "", "contact_note": ""},
]


CAMPUS_DETAILS = [
    {"campus_id": 1, "name": CENTRAL, "address": "Hosur Road, Bangalore 560029", "phones": ["080 40129100"], "emails": ["mail@christuniversity.in"],
     "website": "https://christuniversity.in", "description": "The main campus."},
    {"campus_id": 24, "name": KENGERI, "address": "Kanmanike, Kumbalgodu, Mysore Road, Bangalore 560074", "phones": ["080 62689800", "08 40129828"],
     "emails": ["mail@christuniversity.in"], "website": "https://bkc.christuniversity.in", "description": None},
]
FACILITIES = [
    {"campus_id": 24, "campus": KENGERI, "facility": "Library", "text": "An up-to-date library with books in a variety of domains.", "link": None},
    {"campus_id": 24, "campus": KENGERI, "facility": "Transportation", "text": "A full-fledged transport department runs buses for students and staff.", "link": None},
    {"campus_id": 1, "campus": CENTRAL, "facility": "Accommodation", "text": "Hostel rooms are available for outstation students.", "link": "https://christuniversity.in/hostel"},
]

class FakeSource:
    """In-memory CatalogueSource with the same dict shapes as MySqlSource."""
    name = "fake"

    def __init__(self, down=False, facilities_granted=True):
        self.down, self.facilities_granted = down, facilities_granted

    def _chk(self):
        if self.down:
            raise SourceUnavailable("down")

    def list_catalogue(self):
        self._chk()
        return [dict(c) for c in COURSES]

    def search_courses(self, query, level, campus, limit):
        self._chk()
        return [c for c in COURSES if query.lower() in c["name"].lower()][:limit]

    def get_course(self, cid):
        self._chk()
        c = next((c for c in COURSES if c["course_id"] == cid), None)
        if not c:
            return None
        return {**c, "summary": f"{c['name']} is a programme at CHRIST.", "overview": f"Overview of {c['name']}. " + "x" * 3000,
                "selection_process": None, "brochure": None}

    def get_sections(self, cid, kind):
        self._chk()
        secs = SECTIONS.get(cid, {})
        keys = [SECTION_KEY[kind]] if kind in SECTION_KEY else list(secs) if kind is None else []
        return [{"section": n, "text": t, "added": "2026-01-01"} for k in keys for n, t in secs.get(k, [])]

    def get_highlights(self, cid):
        self._chk()
        return [{"kind": "careers", "title": "Business Analyst", "text": ""}] if cid == 334 else []

    def get_curriculum(self, cid, year):
        self._chk()
        if cid != 334:
            return []
        terms = [{"year": "Year 1", "term": "1", "focus": "Core", "subjects": [{"category": "Core", "subject": "Managerial Economics", "credits": 3}]},
                 {"year": "Year 2", "term": "4", "focus": "Electives", "subjects": [{"category": "Elec", "subject": "Strategy", "credits": 3}]}]
        return [t for t in terms if not year or t["year"].lower() == year.lower()]

    def get_roadmap(self, cid):
        return []

    def get_admission_process(self, group):
        self._chk()
        return [r for r in PROCESS if not group or group.lower() in r["group"]]

    def list_campuses(self):
        self._chk()
        return [{"campus_id": 1, "name": CENTRAL}, {"campus_id": 24, "name": KENGERI}]

    def list_campus_details(self):
        self._chk()
        return [dict(c) for c in CAMPUS_DETAILS]

    def list_facilities(self, campus_id):
        self._chk()
        if not self.facilities_granted:
            raise DataNotGranted("no SELECT grant")
        return [dict(f) for f in FACILITIES if campus_id is None or f["campus_id"] == campus_id]

    def ping(self):
        return not self.down


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    """Tests never touch a real database: no DB credentials, and every test starts with no catalog and no injected reader."""
    from app.catalog.refresh import set_catalog, set_reader
    for k in ("AGENT_DB_USER", "AGENT_DB_PASSWORD", "CATALOGUE_SOURCE", "ADMIN_TOKEN", "CATALOG_SNAPSHOT"):
        monkeypatch.delenv(k, raising=False)
    set_catalog(None)
    set_reader(None)
    from app.routers import api
    api._hits.clear()
    api._DONE.clear()
    api._INFLIGHT.clear()
    api._VOICE.update(misses=0, down_until=0.0)
    from app import live
    live.clear_fee_cache()
    yield
    set_catalog(None)
    set_reader(None)


@pytest.fixture(autouse=True)
def catalogue_source():
    set_source(FakeSource())
    reset_catalogue()
    yield
    set_source(None)
    reset_catalogue()


class FakeProvider:
    """Scripted chat LLM (tools mode)."""
    name, model = "fake", "fake-model"

    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def complete(self, messages, tools=None, temperature=0.2):
        self.seen.append((messages, tools))
        return self.script.pop(0)


def call(name, **args):
    return LLMResponse(None, [ToolCall(id=f"c-{name}", name=name, arguments=json.dumps(args))])


def text(t):
    return LLMResponse(t, [])


@pytest.fixture(autouse=True)
def demo_env(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "demo")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for k in ("LLM_MODEL", "AGENT_MODE", "LLM_PROVIDER", "LLM_BASE_URL", "LLM_DECISIONS_URL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def client():
    return TestClient(app)

"""Structured-source tools: fake source for logic, plus a live check of the read-only database account (skipped without a DB)."""
import json
import os

import pytest

from app.sources import SourceUnavailable, set_source
from app.sources.mysql_source import MySqlSource, _like
from app.sources.text import html_to_text
from app.tools.registry import ToolContext, available_tools, execute

CTX = ToolContext()


class Fake:
    name = "fake"

    def __init__(self, down=False):
        self.down = down

    def _chk(self):
        if self.down:
            raise SourceUnavailable("down")

    def search_courses(self, query, level, campus, limit):
        self._chk()
        return [{"course_id": 334, "name": "Master of Business Administration (MBA)", "level": "Postgraduate", "campus": "Central",
                 "department": "Business", "duration_years": 2}] if "mba" in query.lower() else []

    def get_course(self, cid):
        self._chk()
        return {"course_id": cid, "name": "MBA", "overview": "x" * 5000, "selection_process": None} if cid == 334 else None

    def get_sections(self, cid, kind):
        self._chk()
        return [{"section": "Eligibility-2027", "text": "50% aggregate", "added": "2026-01-01"}] if kind == "eligibility" else []

    def get_highlights(self, cid):
        return []

    def get_curriculum(self, cid, year):
        return [{"year": "Year 1", "term": "1", "focus": "Core", "subjects": [{"category": "Core", "subject": "Accounting", "credits": 3}]}]

    def get_roadmap(self, cid):
        return []

    def get_admission_process(self, g):
        return [{"group": "pg mangement", "how_to_apply": "online", "eligibility": "", "selection": "", "admission_dates": "", "contact_note": ""}]

    def list_campuses(self):
        return [{"campus_id": 1, "name": "Central"}]

    def ping(self):
        return True


@pytest.fixture
def fake():
    set_source(Fake())
    yield
    set_source(None)


def test_db_tools_hidden_without_source():
    assert not [t for t in available_tools(CTX) if t.name.startswith("db_")]
    out = execute("db_search_programmes", "{}", CTX)
    assert out.data["error"] == "tool_unavailable"


def test_db_tools_offered_with_source(fake):
    names = {t.name for t in available_tools(CTX)}
    assert {"db_search_programmes", "db_get_programme_section", "db_get_curriculum"} <= names


def test_search_and_details(fake):
    r = execute("db_search_programmes", json.dumps({"query": "MBA"}), CTX).data
    assert r["programmes"][0]["course_id"] == 334
    assert execute("db_search_programmes", json.dumps({"query": "zzz"}), CTX).data["found"] is False
    d = execute("db_get_programme_details", json.dumps({"course_id": 334}), CTX).data
    assert len(d["programme"]["overview"]) < 2200 and "snapshot" in d["note"]
    assert execute("db_get_programme_details", json.dumps({"course_id": 1}), CTX).data["found"] is False


def test_section_validates_parameters(fake):
    ok = execute("db_get_programme_section", json.dumps({"course_id": 334, "section": "eligibility"}), CTX).data
    assert ok["sections"][0]["text"] == "50% aggregate"
    bad = execute("db_get_programme_section", json.dumps({"course_id": 334, "section": "'; DROP TABLE courses;--"}), CTX).data
    assert bad["error"] == "invalid_arguments"
    assert execute("db_get_programme_section", json.dumps({"course_id": "abc", "section": "fees"}), CTX).data["error"] == "invalid_arguments"
    assert execute("db_get_programme_section", json.dumps({"course_id": 334, "section": "fees"}), CTX).data["found"] is False


def test_source_down_is_reported_not_guessed():
    set_source(Fake(down=True))
    try:
        r = execute("db_get_programme_details", json.dumps({"course_id": 334}), CTX).data
    finally:
        set_source(None)
    assert r["error"] == "catalogue_unavailable"


def test_html_to_text_and_like_escape():
    t = html_to_text("<p>Fees</p><table><tr><td>A</td><td>1,000</td></tr></table><ul><li>x</li></ul><script>bad()</script>")
    assert "A | 1,000" in t and "- x" in t and "bad" not in t
    assert _like("50%_x") == r"%50\%\_x%"


# ------------------------------------------------------------------ live DB (only when the local server + agent_ro exist)
def _live():
    from dotenv import dotenv_values
    env = dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not env.get("AGENT_DB_USER"):
        return None
    for k in ("AGENT_DB_USER", "AGENT_DB_PASSWORD", "APPLICANT_DB_HOST", "APPLICANT_DB_PORT", "APPLICANT_DB_NAME"):
        if env.get(k):
            os.environ[k] = env[k]
    src = MySqlSource.from_env()
    return src if src and src.ping() else None


@pytest.fixture
def live():
    src = _live()
    if not src:
        pytest.skip("local MySQL / agent_ro not available")
    return src


def test_live_whole_word_search_and_sections(live):
    names = [r["name"] for r in live.search_courses("mba", None, None, 10)]
    assert names and not any("Mumbai" in n for n in names)
    assert any(s["section"].lower().startswith("eligib") for s in live.get_sections(334, "eligibility"))


def test_live_account_is_really_read_only(live):
    with pytest.raises(SourceUnavailable):
        live._rows("DELETE FROM courses WHERE 1=0")
    with pytest.raises(SourceUnavailable):
        live._rows("SELECT COUNT(*) FROM enquiry")  # personal table: no grant
    with pytest.raises(SourceUnavailable):
        live._rows("SELECT * FROM courses; DROP TABLE courses")  # multi-statements are not enabled


def test_live_injection_text_is_just_data(live):
    assert live.search_courses("x' OR '1'='1", None, None, 5) == []

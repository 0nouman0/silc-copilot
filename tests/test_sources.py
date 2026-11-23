"""Structured-source tools: fake source for logic, plus a live check of the read-only database account (skipped without a DB)."""
import json
import os

import pytest

from app.sources import DataNotGranted, SourceUnavailable, set_source
from app.sources.mysql_source import MySqlSource, _like
from app.sources.text import html_to_text, tidy
from app.tools.registry import ToolContext, execute

CTX = ToolContext()
NL = chr(10)


def test_tools_report_unavailable_without_a_source(monkeypatch):
    set_source(None)
    monkeypatch.setenv("CATALOGUE_SOURCE", "none")
    assert execute("search_programmes", json.dumps({"query": "mba"}), CTX).data["error"] == "catalogue_unavailable"


def test_source_instance_is_shared_per_configuration(monkeypatch):
    from app.sources import get_source
    set_source(None)
    monkeypatch.setenv("CATALOGUE_SOURCE", "mysql")
    monkeypatch.setenv("AGENT_DB_USER", "u")
    monkeypatch.setenv("AGENT_DB_PASSWORD", "p")
    assert get_source() is get_source()  # the catalogue caches derived data against this instance


def test_section_validates_parameters():
    ok = execute("get_programme_section", json.dumps({"programme_id": 334, "section": "eligibility"}), CTX).data
    assert "50%" in ok["sections"][0]["text"]
    bad = execute("get_programme_section", json.dumps({"programme_id": 334, "section": "'; DROP TABLE courses;--"}), CTX).data
    assert bad["error"] == "invalid_arguments"
    assert execute("get_programme_section", json.dumps({"programme_id": "abc", "section": "fees"}), CTX).data["error"] == "invalid_arguments"
    assert execute("get_programme_section", json.dumps({"programme_id": 501, "section": "eligibility"}), CTX).data["found"] is False


def test_html_to_text_builds_markdown_tables_lists_and_drops_scripts():
    t = html_to_text("<p>Fees</p><table><tr><th><div>Year</div></th><th>NRI</th></tr><tr><td>1</td><td><div>6,70,000 INR</div></td></tr></table>"
                     "<ul><li>x</li></ul><script>bad()</script>")
    assert NL.join(["| Year | NRI |", "|---|---|", "| 1 | 6,70,000 INR |"]) in t and "- x" in t and "bad" not in t
    assert html_to_text("<table><tr><td>only one column</td></tr></table>") == "only one column"  # layout table stays plain text
    assert html_to_text(None) == ""


def test_html_to_text_removes_stray_encoding_byte_and_tidy_keeps_wording():
    assert html_to_text("<p>Risk and Â Financial Advisory</p>") == "Risk and Financial Advisory"
    out = tidy(NL.join(["Intro", "- a", "- b", "| H | I |", "|---|---|", "| 1 | 2 |", "Outro"]))
    assert out == (NL * 2).join(["Intro", NL.join(["- a", "- b"]), NL.join(["| H | I |", "|---|---|", "| 1 | 2 |"]), "Outro"]) and tidy(None) == ""


def test_like_escape():
    assert _like("50%_x") == "%50" + chr(92) + "%" + chr(92) + "_x%"


def test_file_only_rows_are_recognised():
    from app.sources.mysql_source import _FILE_ONLY
    assert _FILE_ONLY.fullmatch("Placement Highlights_pptx-2_20260722111258.pdf")
    assert not _FILE_ONLY.fullmatch("Top recruiters visit. See brochure.pdf for more details" + NL + "and more")



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
    refused = (SourceUnavailable, DataNotGranted)
    with pytest.raises(refused):
        live._rows("DELETE FROM courses WHERE 1=0")
    with pytest.raises(refused):
        live._rows("SELECT COUNT(*) FROM enquiry")  # personal table: no grant
    with pytest.raises(refused):
        live._rows("SELECT * FROM courses; DROP TABLE courses")  # multi-statements are not enabled


def test_live_campus_details_are_real_contact_data(live):
    rows = live.list_campus_details()
    assert len(rows) >= 5 and all(r["name"] for r in rows)
    kengeri = next(r for r in rows if "Kengeri" in r["name"])
    assert kengeri["phones"] and kengeri["emails"] and kengeri["website"].startswith("http") and "\n" not in kengeri["address"]


def test_live_facilities_work_when_granted_and_say_so_when_not(live):
    try:
        rows = live.list_facilities(None)
    except DataNotGranted:
        return   # campus_fecilities not granted yet: the app reports 'not available' (docs/agent_ro_grants.sql)
    assert rows and all(r["text"] or r["link"] for r in rows)


def test_live_injection_text_is_just_data(live):
    assert live.search_courses("x' OR '1'='1", None, None, 5) == []

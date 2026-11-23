"""Lateral entry as an admission route: derived only from stored facts, never assumed, honest about what is not stored."""
import copy
import json

import pytest

from app.catalog import set_catalog, set_reader
from app.catalog.lateral import audit, lateral_for, lateral_index
from app.tools import ToolContext, execute
from tests.fake_catalog import FakeReader, _s, build
from tests.test_dialog import converse, eng_rows

ELIG_LAT = "<p>Lateral Entry: Candidates who have successfully completed the 3-year diploma in Computer Science are eligible to apply for lateral entry into engineering programmes.</p>"


def lat_rows():
    r = eng_rows()
    r["sections"] += [
        _s(60, 511, "Eligibility", "T", ELIG_LAT, "E"),                                                              # the lateral listing carries its own eligibility
        _s(61, 81, "Eligibility", "T", "<p>A pass at +2 with 50% marks.</p><p>Lateral Entry: Diploma holders (3 years) may apply for the second year.</p>", "E"),   # text-only mention
        _s(62, 18, "Eligibility", "T", "<p>A pass at +2 level with 50% marks in PCM.</p>", "E"),                      # civil: nothing about lateral entry
        _s(63, 536, "Application Process 2027", "T", "<p>Apply on the portal.</p>", "R"),
    ]
    return r


@pytest.fixture
def lat(monkeypatch):
    from app.catalogue import reset_catalogue
    from app.sources import set_source
    set_source(None)
    monkeypatch.setenv("CATALOGUE_SOURCE", "catalog")
    reset_catalogue()
    rd = FakeReader(lat_rows())
    set_reader(rd)
    c = build(rd)
    set_catalog(c)
    return c


def run(name, **a):
    return execute(name, json.dumps(a), ToolContext()).data


def test_only_programmes_with_stored_evidence_have_lateral_entry(lat):
    idx = lateral_index(lat)
    names = {e.name for e in idx.values()}
    assert any("Artificial Intelligence and Machine Learning" in n for n in names) and any(n.endswith("Computer Science and Engineering") for n in names)
    assert not any("Civil" in n or "Mechanical" in n or "Automotive" in n for n in names)           # being a B.Tech is not evidence
    ai = lateral_for(lat, 457)
    assert ai.listing_id == 511 and ai.regular_listing_id == 457 and "listing" in ai.evidence and "3-year diploma in Computer Science" in ai.eligibility
    cs = lateral_for(lat, 81)
    assert cs.evidence == ("eligibility_text",) and cs.listing_id is None and cs.eligibility.startswith("Lateral Entry: Diploma holders")
    assert lateral_for(lat, 18) is None and lateral_for(lat, 73) is None


def test_missing_fields_stay_missing(lat):
    ai = lateral_for(lat, 457)
    assert ai.entry_year is None and ai.admission_process is None
    assert {"entry year or semester", "admission procedure", "required documents"} <= set(ai.unspecified)
    a = audit(lat)
    assert a["lateral_listings"] == 1 and a["programmes_with_lateral_entry"] == 2 and a["by_evidence"] == {"listing": 1, "eligibility_text_only": 1}
    assert a["with_entry_year"] == 1 and lateral_for(lat, 81).entry_year == "second year"       # stated in 81's eligibility text; absent for the AI/ML listing


def test_the_lateral_tool_checks_one_programme_or_the_filters(lat):
    one = run("search_lateral_entry", programme_id=457)
    row = one["programmes_offering_lateral_entry"][0]
    assert one["found"] and row["lateral_listing_id"] == 511 and row["programme_id"] == 511 and "diploma" in row["eligibility"].lower() and row["entry_year_or_semester"] is None
    civil = run("search_lateral_entry", programme_id=18)
    assert civil["found"] is False and civil["programmes_offering_lateral_entry"] == [] and civil["admissions_contact"]
    cs = run("search_lateral_entry", degree_type="B.Tech", discipline="Computer Science")
    got = {r["name"] for r in cs["programmes_offering_lateral_entry"]}
    assert len(got) == 2 and all("Computer Science" in n for n in got)
    assert any("Data Science" in n for n in cs["matching_programmes_without_a_lateral_entry_listing"])          # matching, but no lateral entry listed: said so
    everything = run("search_lateral_entry")
    assert len(everything["programmes_offering_lateral_entry"]) == 2


def test_eligibility_tool_by_route(lat):
    lateral = run("get_eligibility_criteria", programme_id=457, entry_type="lateral")
    assert lateral["listing_id"] == 511 and "3-year diploma" in lateral["sections"][0]["text"]
    regular = run("get_eligibility_criteria", programme_id=457, entry_type="regular")
    assert regular["listing_id"] == 457
    para = run("get_eligibility_criteria", programme_id=81, entry_type="lateral")                      # no lateral listing: only the paragraph that mentions it
    assert para["sections"][0]["text"].startswith("Lateral Entry: Diploma holders") and "50% marks" not in para["sections"][0]["text"]
    assert run("get_eligibility_criteria", programme_id=18, entry_type="lateral")["found"] is False


def test_admission_process_tool(lat):
    d = run("search_admission_process", query="how to apply", programme_id=536)
    assert d["found"] and "portal" in d["passages"][0]["text"]
    none = run("search_admission_process", query="how to apply", programme_id=18)
    assert none["found"] is False and none["admissions_contact"]
    assert run("search_admission_process", query="lateral", entry_type="lateral", programme_id=536)["found"] is False


def test_programme_details_tool_carries_the_clean_record_and_the_route(lat):
    rec = run("get_programme_details", programme_id=457)["record"]
    assert rec["degree_type"] == "B.Tech" and rec["lateral_entry"]["available"] is True and "fees" in rec and "eligibility" in rec
    assert run("get_programme_details", programme_id=18)["record"]["lateral_entry"]["available"] is False


# ---------------------------------------------------------------- the conversations
def test_E_lateral_entry_without_context_lists_only_programmes_that_list_it(client, lat):
    r = converse(client, ["How do I take lateral entry?"])[0]
    assert r["intent"] == "lateral_entry" and r["tools_used"] == ["search_lateral_entry"] and r["state"]["entry"] == "lateral"
    assert len(r["state"]["last_ids"]) == 2 and r["state"]["pending"]["requested"] == "lateral"
    assert "Civil" not in r["reply"] and "Mechanical" not in r["reply"] and "not assumed" in r["reply"]


def test_F_btech_computer_science_then_lateral_entry(client, lat):
    r = converse(client, ["I want B.Tech.", "Computer Science.", "How do I take lateral entry?"])
    assert r[0]["intent"] == "programme_search" and r[1]["state"]["concepts"] == ["Computer Science"]
    f = r[2]
    assert f["intent"] == "lateral_entry" and f["state"]["degree"] == "B.Tech" and f["state"]["concepts"] == ["Computer Science"]
    assert all("Computer Science" in lat.programs[i].name for i in f["state"]["last_ids"]) and len(f["state"]["last_ids"]) == 2


def test_G_is_lateral_entry_available_for_aiml(client, lat):
    r = converse(client, ["I want B.Tech.", "Is lateral entry available for AI/ML?"])[1]
    assert r["intent"] == "lateral_entry" and r["state"]["concepts"] == ["AI/ML"]
    ids = r["state"]["last_ids"]
    assert ids and all("Artificial Intelligence" in lat.programs[i].name for i in ids) and r["state"]["degree"] == "B.Tech"
    assert "Lateral Entry" in r["reply"] and "diploma" in r["reply"].lower()


def test_H_I_eligibility_and_fees_follow_the_selected_lateral_programme(client, lat):
    r = converse(client, ["I want B.Tech Computer Science.", "Lateral entry.", "1", "What is the eligibility?", "What are the fees?"])
    detail = r[2]
    assert "Yes, lateral entry is listed" in detail["reply"] and "does **not** state" in detail["reply"] and "entry point at the **second year**" in detail["reply"] and "admission procedure" in detail["reply"]
    assert "I won't guess" in detail["reply"] and detail["state"]["programme_id"] and detail["sources"][0]["url"]
    elig = r[3]
    assert elig["intent"] == "eligibility_criteria" and "lateral entry" in elig["reply"].lower() and ("3-year diploma" in elig["reply"] or "Diploma holders" in elig["reply"])
    assert r[4]["intent"] == "programme_fees" and elig["state"]["programme_id"] == r[4]["state"]["programme_id"] and elig["state"]["entry"] == "lateral"


def test_switching_route_and_degree_updates_the_entry(client, lat):
    r = converse(client, ["B.Tech lateral entry", "without lateral entry", "BSc"])
    assert r[0]["state"]["entry"] == "lateral" and r[1]["state"]["entry"] == "regular" and r[2]["state"]["degree"] == "BSc" and r[2]["state"]["entry"] is None


def test_lateral_for_a_programme_without_it_is_not_invented(client, lat):
    r = converse(client, ["B.Tech Civil Engineering", "lateral entry"])
    assert r[1]["intent"] == "lateral_entry" and "couldn't find lateral entry listed" in r[1]["reply"] or "can't find lateral entry listed" in r[1]["reply"]
    assert "Yes, lateral entry is listed" not in r[1]["reply"] and r[1]["state"]["last_ids"] == []


def test_diploma_holders_are_not_assumed_eligible(client, lat):
    r = converse(client, ["can diploma holders join B.Tech Civil Engineering"])[0]
    assert r["intent"] == "lateral_entry" and "Yes, lateral entry is listed" not in r["reply"] and "diploma holders are eligible" not in r["reply"].lower()


def test_admission_process_question_uses_the_search_tool(client, lat):
    r = converse(client, ["B.Tech AI/ML", "Tell me about Bachelor of Technology in Artificial Intelligence and Machine Learning", "how do i apply"])
    assert r[2]["intent"] == "admission_process" and "search_admission_process" in r[2]["tools_used"] and "portal" in r[2]["reply"]
    generic = converse(client, ["how do i apply"])[0]               # no programme: only the university-wide block, never one programme's own text
    assert generic["intent"] == "admission_process" and "Register online and upload your documents" in generic["reply"] and "See guide" not in generic["reply"]
    civil = converse(client, ["B.Tech Civil Engineering", "how do i apply"])[1]
    assert civil["intent"] == "admission_process" and "I won't guess" in civil["reply"]

"""Fees from the university database: the active result set, per-row availability, stored text exactly, honest staleness, no invention."""
import copy
import json
from datetime import date

import pytest

from app import live
from app.catalog import set_catalog, set_reader
from tests.fake_catalog import FakeReader, build
from tests.test_dialog import converse, eng_rows

TABLE = ("<p>1. Admission Registration fee of INR 8000 /- (Non-Refundable)</p><p>2. Programme fee as per the below table</p>"
         "<table><tr><th>Year</th><th># Karnataka</th><th>Other Indian States</th><th>NRI</th><th>Time of Payment</th></tr>"
         "<tr><td>1</td><td>3,00,000 INR</td><td>3,20,000 INR</td><td>4,00,000 INR</td><td>Within seven days</td></tr>"
         "<tr><td>2</td><td>3,10,000 INR</td><td>3,30,000 INR</td><td>4,10,000 INR</td><td>March 15, 2028</td></tr></table>")


def fee_row(rid, cid, name, html=TABLE, expire=date(2026, 10, 14)):
    return {"rel_link_id": rid, "course_id": cid, "rel_link_name": name, "rel_link_description": html, "rel_link_added_date": date(2026, 1, 1), "rel_link_expire_date": expire}


@pytest.fixture
def fees(monkeypatch):
    from app.catalogue import reset_catalogue
    from app.sources import set_source
    live.clear_fee_cache()
    set_source(None)
    monkeypatch.setenv("CATALOGUE_SOURCE", "catalog")
    reset_catalogue()
    rows = eng_rows()
    rows["fees"] = [fee_row(1, 81, "Fee Structure - 2027"), fee_row(2, 81, "Fee Structure - 2026", html="<p>old 2026 fee INR 1</p>"),
                    fee_row(3, 457, "Fee Structure - 2027", html=TABLE.replace("3,00,000", "3,50,000")), fee_row(4, 18, "Fee Structure - 2027"),
                    fee_row(5, 73, "Fee Structure - 2027", expire=date(2030, 1, 1))]
    rd = FakeReader(rows)
    set_reader(rd)
    c = build(rd)
    set_catalog(c)
    return c


def test_the_fee_tool_returns_the_stored_text_exactly_with_year_and_a_staleness_note(fees):
    f = live.get_fees(81)
    assert f.available and f.source == "website_database" and f.value["academic_year"] == "2027" and "3,00,000 INR" in f.value["text"]
    assert "old 2026" not in f.value["text"]                                         # the newest academic year wins
    assert "validity date (2026-10-14) has passed" in f.note and "confirm" in f.note
    assert not live.get_fees(73).value["validity_passed"] and "has passed" not in live.get_fees(73).note
    assert live.get_fees(536).available is False and "No fee structure is recorded" in live.get_fees(536).note        # missing is missing, not zero
    assert live.fee_summary(live.fee_for(81)) == "Year 1: Karnataka 3,00,000 INR · Other Indian States 3,20,000 INR · NRI 4,00,000 INR"


def test_fees_source_can_be_switched_off(fees, monkeypatch):
    monkeypatch.setenv("FEES_SOURCE", "none")
    assert live.get_fees(81).available is False


def test_a_single_programme_shows_the_full_stored_table(client, fees):
    r = converse(client, ["Tell me about Bachelor of Technology in Computer Science and Engineering", "What are the fees?"])
    reply = r[1]["reply"]
    assert r[1]["intent"] == "programme_fees" and "3,00,000 INR" in reply and "Other Indian States" in reply and "NRI" in reply and "4,00,000 INR" in reply
    assert "| Year |" in reply and "Admission Registration fee of INR 8000" in reply and "for 2027" in reply and "has passed" in reply
    assert "old 2026" not in reply and "not available" not in reply


def test_fees_list_for_the_active_results_with_per_row_availability(client, fees):
    first, fee = converse(client, ["Show me B.Tech Computer Science", "Tell me fees list"])
    shown = first["state"]["last_ids"]
    assert fee["intent"] == "fees_list" and fee["state"]["last_ids"] == shown
    rows = [l.split("|") for l in fee["reply"].split("\n") if l.startswith("| ") and "---" not in l and " Programme " not in l]
    by = {r[2].strip().split(" · ")[0]: r[5].strip() for r in rows}
    assert by["Bachelor of Technology in Computer Science and Engineering"].startswith("Year 1: Karnataka 3,00,000 INR")
    assert by["Bachelor of Technology in Computer Science and Engineering (Artificial Intelligence and Machine Learning)"].startswith("Year 1: Karnataka 3,50,000 INR")
    assert by["Bachelor of Technology in Computer Science and Engineering (Data Science)"] == "Fee information currently unavailable."   # one gap does not condemn the degree
    assert "Mechanical" not in fee["reply"] and "Civil" not in fee["reply"] and "Where a row says unavailable" in fee["reply"] and "validity date has passed" in fee["reply"]
    assert not any(c in by["Bachelor of Technology in Computer Science and Engineering (Data Science)"] for c in "0123456789")


def test_a_number_after_the_fees_list_shows_that_programmes_table(client, fees):
    first = converse(client, ["Show me B.Tech Computer Science", "Tell me fees list"])[1]
    opts = first["state"]["pending"]["options"]
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "fees list"}, {"role": "assistant", "content": first["reply"]}, {"role": "user", "content": "1"}], "state": first["state"]}).json()
    pid = opts[0]["programme_id"]
    assert r["state"]["programme_id"] == pid and "Taking **" in r["reply"] and ("INR" in r["reply"] or "not available in the current university database" in r["reply"])


def test_fee_figures_match_the_database_rows(client, fees):
    """Every rupee amount in a reply must come verbatim from a stored fee row."""
    import re
    stored = " ".join(r["rel_link_description"] for r in FakeReader(dict(eng_rows(), fees=[fee_row(1, 81, "x"), fee_row(3, 457, "x", html=TABLE.replace("3,00,000", "3,50,000"))])).data.get("fees", []))
    stored += TABLE + TABLE.replace("3,00,000", "3,50,000")
    r = converse(client, ["B.Tech AI/ML", "Tell me fees list"])[1]
    amounts = re.findall(r"\d[\d,]{3,} INR", r["reply"])
    assert amounts and all(a in stored for a in amounts)


def test_database_down_during_fee_lookup_is_reported_not_guessed(fees, monkeypatch):
    class Down(FakeReader):
        def rows(self, name, params=()):
            if name == "fees":
                raise RuntimeError("db down")
            return super().rows(name, params)
    live.clear_fee_cache()
    set_reader(Down(eng_rows()))
    f = live.get_fees(81)
    assert f.available is False and "could not be read" in f.note


def test_fee_content_never_enters_the_catalog(fees):
    assert all("INR" not in c.text for c in fees.indexes.chunks) and "fees" not in " ".join(k.value for p in fees.programs.values() for k in p.sections)

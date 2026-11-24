"""Hostel / campus-facility questions are answered from the campus facility records; a composed answer never opens with a greeting."""
import pytest

from app import live
from app.agent.composer import Composer, strip_greeting
from app.catalog import set_catalog, set_reader
from tests.fake_catalog import FakeReader, build
from tests.test_conversation import _Prov, conv, conv_rows, no_ui_clutter  # noqa: F401  (fixtures)
from tests.test_dialog import converse


# ---------------------------------------------------------------- hostel intent
def test_hostel_question_is_a_facility_question_not_a_programme_search(client, conv):
    r = converse(client, ["Is there a hostel at Bangalore Central Campus?"])[0]
    assert r["intent"] == "facilities" and r["tools_used"] == ["get_campus_facilities"]
    assert "Hostel rooms are available for outstation students" in r["reply"] and "Bangalore Central Campus" in r["reply"]
    assert "programme records" not in r["reply"] and no_ui_clutter(r)


def test_the_hostel_of_another_campus_is_not_shown(client, conv):
    r = converse(client, ["hostel at Kengeri campus"])[0]
    assert r["intent"] == "facilities" and "Hostel rooms are available" not in r["reply"] and "no hostel" in r["reply"].lower()


def test_hostel_fees_are_never_invented(client, conv):
    r = converse(client, ["Tell me the hostel fees"])[0]
    assert r["intent"] == "facilities" and "Hostel rooms are available" in r["reply"]
    assert "do not state a fee" in r["reply"] and "INR" not in r["reply"]


def test_a_stored_hostel_fee_is_quoted_exactly(client, conv):
    rows = conv_rows()
    rows["facilities"][0]["cam_fecilities_description"] = "<p>Hostel rooms are available for outstation students. Triple sharing costs INR 85,000 per year.</p>"
    rd = FakeReader(rows)
    set_reader(rd)
    set_catalog(build(rd))
    r = converse(client, ["What are the hostel charges?"])[0]
    assert "INR 85,000 per year" in r["reply"] and "exactly as stored" in r["reply"]


def test_library_and_general_facilities(client, conv):
    lib = converse(client, ["Does the university have a library?"])[0]
    assert lib["intent"] == "facilities" and "up-to-date library" in lib["reply"]
    allf = converse(client, ["What facilities are there on campus?"])[0]
    assert allf["intent"] == "facilities" and "Hostel rooms" in allf["reply"] and "library" in allf["reply"]


def test_unreadable_facilities_table_is_said_plainly(client, monkeypatch):
    monkeypatch.setenv("CATALOGUE_SOURCE", "catalog")
    monkeypatch.setenv("COMPOSE", "off")
    live.clear_fee_cache()
    rd = FakeReader(conv_rows(), denied={"facilities"})            # the database account has no SELECT on campus_fecilities
    set_reader(rd)
    set_catalog(build(rd))
    r = converse(client, ["Is there a hostel?"])[0]
    assert r["intent"] == "facilities" and "can't read the campus facility records" in r["reply"] and "won't guess" in r["reply"]


def test_programme_questions_that_mention_a_facility_word_still_search_programmes(client, conv):
    r = converse(client, ["Is there a Library Science programme?"])[0]
    assert r["intent"] != "facilities"
    r = converse(client, ["Tell me about MBA."])[0]
    assert r["intent"] == "programme_overview"


def test_hostel_does_not_erase_the_active_search(client, conv):
    r = converse(client, ["B.Tech", "Tell me about the hostel", "What about fees?"])
    assert r[1]["intent"] == "facilities" and r[1]["state"]["degree"] == "B.Tech" and r[2]["state"]["degree"] == "B.Tech"


# ---------------------------------------------------------------- no greeting in a composed answer
@pytest.mark.parametrize("raw,expected", [
    ("Hi there! Thanks for asking about the MBA fees. The year-1 fee is 3,30,000.", "The year-1 fee is 3,30,000."),
    ("Hello, the MBA is offered at two campuses.", "The MBA is offered at two campuses."),
    ("Thanks for asking about hostels! Rooms are available.", "Rooms are available."),
    ("Great question. The MBA is offered at two campuses.", "The MBA is offered at two campuses."),
    ("The MBA is offered at two campuses.", "The MBA is offered at two campuses."),
    ("Hindi is not a programme here.", "Hindi is not a programme here."),
    ("Hi!", "Hi!"),
])
def test_strip_greeting(raw, expected):
    assert strip_greeting(raw) == expected


def test_a_model_that_greets_is_cleaned_before_it_reaches_the_applicant(client, conv, monkeypatch):
    import re
    monkeypatch.setenv("COMPOSE", "on")
    n = re.search(r"\*\*(\d+) programme listings", converse(client, ["How many programmes are there?"])[0]["reply"]).group(1)
    greeter = _Prov(f"Hi there! Thanks for asking. Right now our records show {n} programme listings in total, spread across several campuses.")
    monkeypatch.setattr("app.routers.api.get_composer", lambda: Composer([greeter]))
    r = converse(client, ["How many programmes are there?"])[0]
    assert greeter.calls == 1 and r["reply"].startswith("Right now our records show") and "Hi there" not in r["reply"]


# ---------------------------------------------------------------- availability: a keyword nothing is about is not "yes"
def test_an_unmatched_keyword_is_not_reported_as_available(client, conv):
    r = converse(client, ["Is there a Library Science programme?"])[0]
    assert r["intent"] == "availability_check" and "can't find" in r["reply"] and not r["reply"].startswith("Yes")
    ok = converse(client, ["Is Computer Science available in B.Tech?"])[0]
    assert ok["reply"].startswith("Yes")


def test_is_there_is_not_spell_corrected_to_is_where(client, conv):
    r = converse(client, ["is there a hostel at kengeri"])[0]
    assert r["understanding"]["corrected_message"] == "is there a hostel at kengeri"

"""Conversation first, search second: small talk and questions are answered directly, programme lists/tables/cards appear only when asked for."""
import json
import re
from datetime import date

import pytest

from app import live
from app.agent import understanding as U
from app.agent.composer import Composer
from app.catalog import set_catalog, set_reader
from app.llm.base import LLMResponse
from tests.fake_catalog import FakeReader, _p, _s, build
from tests.test_dialog import converse, eng_rows
from tests.test_fees import TABLE, fee_row

ELIG_MBA = "<p>Candidates who have passed a Bachelor's degree with a minimum of 50% marks in aggregate are eligible.</p>"


def conv_rows():
    r = eng_rows()
    r["programs"] += [
        _p(78, "Master of Technology in Computer Science and Engineering", 2, 24, 11, intDuration=2),
        _p(538, "Master of Technology in Data Science", 2, 24, 11, intDuration=2),
        _p(341, "Master of Business Administration (MBA) (Executive)", 2, 1, 10, intDuration=1),
        _p(246, "Master of Business Administration (MBA) - CHRIST (Deemed to be University) + Credits Exchange with the MBA of THWS, Germany) (Bangalore Central Campus)", 2, 1, 10),
        _p(947, "Master of Business Administration (MBA) (Finance and International Business / Marketing and International Business)", 2, 24, 11),
    ]
    r["sections"] += [
        _s(70, 78, "Eligibility", "T", "<p>Candidates with a B.Tech / B.E degree and 55% aggregate marks are eligible.</p>", "E"),
        _s(71, 538, "Eligibility", "T", "<p>A B.Tech with 60% in a relevant branch is required.</p>", "E"),
        _s(72, 78, "Selection Process", "T", "<p>Selection is by a written test and a personal interview.</p>", "R"),
        _s(73, 341, "Eligibility-2027", "T", ELIG_MBA, "E"), _s(74, 246, "Eligibility-2027", "T", "<p>A degree with 55% and a valid CAT score.</p>", "E"),
        _s(75, 947, "Eligibility-2027", "T", ELIG_MBA, "E"),
    ]
    r["fees"] = [fee_row(1, 334, "Fee Structure - 2027"), fee_row(2, 341, "Fee Structure - 2027", html=TABLE.replace("3,00,000", "6,50,000")),
                 fee_row(3, 246, "Fee Structure - 2027", html=TABLE.replace("3,00,000", "7,00,000"))]
    return r


@pytest.fixture
def conv(monkeypatch):
    from app.catalogue import reset_catalogue
    from app.sources import set_source
    live.clear_fee_cache()
    set_source(None)
    monkeypatch.setenv("CATALOGUE_SOURCE", "catalog")
    monkeypatch.setenv("COMPOSE", "off")
    reset_catalogue()
    rd = FakeReader(conv_rows())
    set_reader(rd)
    c = build(rd)
    set_catalog(c)
    return c


def no_ui_clutter(r):
    return r["cards"] == [] and "|" not in r["reply"] and len(r["follow_ups"]) <= 3


# ---------------------------------------------------------------- A: normal conversation
def test_A_normal_conversation_never_forces_a_search(client, conv):
    r = converse(client, ["Hello.", "How are you?", "I am fine.", "Just exploring."])
    assert [x["intent"] for x in r] == ["smalltalk_greeting", "conversation_how_are_you", "conversation_doing_well", "conversation_exploring"]
    assert all(x["tools_used"] == [] and x["cards"] == [] and "|" not in x["reply"] for x in r)
    assert r[1]["reply"].startswith("I'm doing well") and r[2]["reply"].startswith("Glad to hear") and "undergraduate" in r[3]["reply"]
    assert len({x["reply"] for x in r}) == 4 and all(x["state"]["last_ids"] == [] for x in r)


def test_a_bare_fine_answers_how_are_you_but_is_a_plain_ack_otherwise(client, conv):
    r = converse(client, ["how are you", "fine"])
    assert r[1]["reply"].startswith("Glad to hear") and r[1]["intent"] == "smalltalk_ack"
    assert converse(client, ["fine"])[0]["reply"] != r[1]["reply"]


def test_identity_capabilities_confusion_and_unrelated_questions(client, conv):
    who, can, conf, off = (converse(client, [m])[0] for m in ["Who are you?", "What can you do?", "I don't know what to study", "What is the weather like in Delhi today?"])
    assert "assistant" in who["reply"] and "programme records" in who["reply"] and "lateral entry" in can["reply"]
    assert "interests" in conf["reply"] and conf["cards"] == [] and off["intent"] == "unsupported" and "CHRIST admissions" in off["reply"]
    assert all(x["tools_used"] == [] and x["cards"] == [] for x in (who, can, conf, off))


# ---------------------------------------------------------------- B: statistics from the database
def test_B_programme_count_is_aggregated_from_the_data_not_hardcoded(client, conv):
    from app.catalogue import get_catalogue
    r = converse(client, ["How many programmes does the university have?"])[0]
    legacy = get_catalogue()
    n_listings, n_distinct = len(legacy.programmes), len(conv.programs)
    lv = legacy.by_level()
    assert r["intent"] == "programme_count" and f"**{n_listings} programme listings**" in r["reply"] and f"{n_distinct} distinct programmes" in r["reply"]
    assert f"Undergraduate: {lv['undergraduate']}" in r["reply"] and f"Postgraduate: {lv['postgraduate']}" in r["reply"] and f"Doctoral: {lv['doctoral']}" in r["reply"]
    assert "once per campus" in r["reply"] and r["cards"] == [] and "|" not in r["reply"] and r["tools_used"] == ["get_university_statistics"]
    import dataclasses
    set_catalog(dataclasses.replace(conv, programs={k: v for k, v in conv.programs.items() if k != 601}))     # the numbers follow the data
    r2 = converse(client, ["how many programmes are there"])[0]
    assert f"{n_distinct - 1} distinct programmes" in r2["reply"] and r2["reply"] != r["reply"]


def test_a_statistics_question_does_not_disturb_the_active_search(client, conv):
    r = converse(client, ["B.Tech", "How many programmes does CHRIST have?", "What about fees?"])
    assert r[1]["intent"] == "programme_count" and r[1]["state"]["degree"] == "B.Tech" and r[1]["state"]["last_ids"] == r[0]["state"]["last_ids"]
    assert "B.Tech" in r[2]["reply"] and r[2]["state"]["degree"] == "B.Tech"                                  # the follow-up still refers to B.Tech, not the whole university


def test_a_repeated_statistics_question_gets_a_short_recap(client, conv):
    r = converse(client, ["How many programmes are there?", "ok", "How many programmes are there?"])
    assert r[2]["reply"].startswith("As I mentioned") and len(r[2]["reply"]) < len(r[0]["reply"])


# ---------------------------------------------------------------- C, D: discovery
def test_C_browsing_gives_a_short_numbered_list_with_an_offer_to_show_more(client, conv):
    r = converse(client, ["I want to know about B.Tech programmes."])[0]
    assert r["intent"] == "programme_search" and r["reply"].count("\n1. ") == 1 and len([l for l in r["reply"].split("\n") if re.match(r"\d\. ", l)]) == 5
    assert "more" in r["reply"] and "show more" in r["reply"] and len(r["cards"]) == 5 and "|" not in r["reply"] and len(r["follow_ups"]) <= 3
    assert len(r["state"]["last_ids"]) == 8 and len(r["state"]["pending"]["options"]) == 5                       # the full set is remembered, five were listed


def test_D_changing_degree_switches_naturally(client, conv):
    r = converse(client, ["I want to know about B.Tech programmes.", "And what about BSc?"])
    assert r[1]["state"]["degree"] == "BSc" and "Bachelor of Technology" not in r[1]["reply"] and no_ui_clutter(r[1])
    assert not set(r[0]["state"]["last_ids"]) & set(r[1]["state"]["last_ids"])


def test_exploring_in_prose_asks_one_question_and_never_repeats_it(client, conv):
    r = converse(client, ["I am interested in engineering.", "engineering", "B.Tech"])
    assert r[0]["intent"] == "programme_search" and no_ui_clutter(r[0]) and r[0]["reply"].count("?") == 1
    asked = [x["reply"].count("Are you looking at") for x in r]
    assert sum(asked) <= 1                                                                                      # the degree question is asked once
    assert all(x["cards"] == [] and "|" not in x["reply"] for x in r)


def test_availability_questions_get_a_direct_yes_or_no(client, conv):
    yes = converse(client, ["Is Computer Science available in B.Tech?"])[0]
    no = converse(client, ["Is Psychology available in B.Tech?"])[0]
    assert yes["reply"].startswith("Yes") and "Computer Science and Engineering" in yes["reply"] and no_ui_clutter(yes)
    assert yes["intent"] == "availability_check" and ("can't find" in no["reply"] or "couldn't find" in no["reply"]) and no["cards"] == []


# ---------------------------------------------------------------- E: admission questions
def test_E_how_do_i_get_into_mtech_explains_instead_of_listing(client, conv):
    r = converse(client, ["How do I get into M.Tech?"])[0]
    assert r["intent"] == "admission_process" and {"search_admission_process", "get_eligibility_criteria"} <= set(r["tools_used"])
    assert "M.Tech" in r["reply"] and ("55% aggregate marks" in r["reply"] or "60% in a relevant branch" in r["reply"]) and "written test and a personal interview" in r["reply"] and "can differ between the options" in r["reply"]
    assert "Register online and upload your documents" in r["reply"] and r["cards"] == [] and "|" not in r["reply"] and "others differ, for example Master of Technology" in r["reply"]      # one differing example, not the whole list
    assert r["reply"].count("Master of Technology") <= 3


def test_how_do_i_apply_uses_the_university_wide_text_or_says_nothing_is_published(client, conv):
    r = converse(client, ["How do I apply?"])[0]
    assert r["intent"] == "admission_process" and "Register online and upload your documents" in r["reply"] and "Apply on the portal" not in r["reply"]


# ---------------------------------------------------------------- F, G: MBA overview and follow-up memory
def test_F_tell_me_about_mba_is_an_overview_not_a_table(client, conv):
    r = converse(client, ["Tell me about MBA."])[0]
    assert r["intent"] == "programme_overview" and no_ui_clutter(r) and r["tools_used"] == ["search_programs"]
    assert "MBA options" in r["reply"] and "Executive" in r["reply"] and "international credit exchange" in r["reply"] and "THWS" in r["reply"] and "Finance and International Business" in r["reply"]
    assert "Bangalore Central Campus" in r["reply"] and r["reply"].count("?") == 1 and not re.search(r"^\d\. ", r["reply"], re.M)


def test_G_follow_ups_keep_the_mba_context(client, conv):
    r = converse(client, ["Tell me about MBA.", "What is the eligibility?", "What about fees?", "Which campus?"])
    elig, fees, campus = r[1], r[2], r[3]
    assert elig["intent"] == "eligibility_criteria" and "50% marks" in elig["reply"] and "MBA" in elig["reply"] and elig["cards"] == []
    assert "differ" in elig["reply"] and "Tell me which programme" in elig["reply"]                                 # a different version exists and is pointed to
    assert fees["intent"] == "programme_fees" and "Karnataka" in fees["reply"] and ("6,50,000" in fees["reply"] or "7,00,000" in fees["reply"]) and no_ui_clutter(fees)
    assert campus["intent"] == "campus_info" and "Bangalore Central Campus" in campus["reply"] and "Kengeri" in campus["reply"]
    assert all(x["state"]["degree"] == "MBA" for x in r)
    assert sum(x["reply"].count("Would you like") for x in r) == 1                                               # the offer is made once


# ---------------------------------------------------------------- H: concepts and lateral entry
def test_H_what_is_lateral_entry_then_availability_then_eligibility(client, lat_conv=None, conv=None):
    pass


def test_H_lateral_entry_conversation(client, conv):
    set_catalog(None)
    rows = conv_rows()
    rows["sections"] += [_s(80, 511, "Eligibility", "T", "<p>Lateral Entry: Candidates who completed a 3-year diploma are eligible for lateral entry.</p>", "E")]
    rd = FakeReader(rows)
    set_reader(rd)
    set_catalog(build(rd))
    r = converse(client, ["What is lateral entry?", "Is it available in B.Tech?", "What is the eligibility?"])
    what, avail, elig = r
    assert what["intent"] == "concept_question" and "second year" in what["reply"] and "At CHRIST, lateral entry is listed for" in what["reply"] and what["cards"] == []
    assert avail["intent"] in ("availability_check", "lateral_entry") and "Bachelor of Technology" in avail["reply"] and avail["state"]["entry"] == "lateral"
    assert elig["intent"] in ("eligibility_criteria", "programme_info", "lateral_entry") and "3-year diploma" in elig["reply"]


# ---------------------------------------------------------------- I, J: concepts and comparison
def test_I_difference_between_degrees_is_explained_in_prose(client, conv):
    r = converse(client, ["What is the difference between B.Tech and M.Tech?"])[0]
    assert r["intent"] == "concept_question" and no_ui_clutter(r) and "undergraduate versus postgraduate" in r["reply"]
    assert "At CHRIST there are" in r["reply"] and "B.Tech programmes" in r["reply"] and "M.Tech programmes" in r["reply"]


def test_J_comparison_is_the_one_case_that_gets_a_table(client, conv):
    r = converse(client, ["Compare B.Tech and BSc Computer Science at CHRIST."])[0]
    assert r["intent"] == "comparison" and r["reply"].count("|---|---|---|") == 1 and "| B.Tech | BSc |" in r["reply"] and "Eligibility (as published)" in r["reply"]
    assert "Programmes at CHRIST" in r["reply"] and r["cards"] == []
    one = converse(client, ["What is B.Tech?"])[0]
    assert one["intent"] == "concept_question" and "|" not in one["reply"]


def test_confused_between_degrees_gets_a_counsellor_answer(client, conv):
    r = converse(client, ["I am confused between B.Tech and BSc."])[0]
    assert r["intent"] == "conversation" and "Both are undergraduate options" in r["reply"] and r["reply"].rstrip().endswith("?") and no_ui_clutter(r)
    assert r["tools_used"] == []


# ---------------------------------------------------------------- K: thanks and quick replies
def test_K_thanks_is_just_acknowledged(client, conv):
    r = converse(client, ["B.Tech", "Thank you."])[1]
    assert r["intent"] == "smalltalk_thanks" and r["tools_used"] == [] and r["cards"] == [] and r["follow_ups"] == [] and "programme" not in r["reply"].lower().split("anything")[0]


def test_quick_replies_are_few_contextual_and_never_repeated_back_to_back(client, conv):
    msgs = ["How many programmes are there?", "I want to know about B.Tech programmes.", "Tell me about MBA.", "Is Computer Science available in B.Tech?", "What is lateral entry?"]
    r = converse(client, msgs)
    for a, b in zip(r, r[1:]):
        assert len(a["follow_ups"]) <= 3 and not set(a["follow_ups"]) & set(b["follow_ups"])
    assert all(not re.fullmatch(r"\d+", f) for x in r for f in x["follow_ups"])


# ---------------------------------------------------------------- routing and the model fallback
def test_unrelated_questions_never_trigger_a_programme_search(client, conv):
    for m in ["Hi", "How are you?", "Thanks", "What is lateral entry?", "How many programmes are there?", "What is the difference between B.Tech and M.Tech?"]:
        assert "search_programs" not in converse(client, [m])[0]["tools_used"], m


def test_unclear_messages_get_a_natural_clarification_and_hostel_is_answered_from_data_in_tools_mode(client, conv, monkeypatch):
    from tests.conftest import FakeProvider, text
    monkeypatch.setenv("AGENT_MODE", "decisions")
    r = converse(client, ["asdf qwer"])[0]
    assert r["intent"] in ("clarification", "unsupported") and no_ui_clutter(r)
    fake = FakeProvider([text("model answer")])
    monkeypatch.setenv("AGENT_MODE", "tools")
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: fake)
    assert converse(client, ["is there a hostel at kengeri"])[0]["intent"] == "facilities"                    # hostel is answered from the campus facility records, not by the model


# ---------------------------------------------------------------- the AI interpreter drives the same conversational answers
def _nlu(**kw):
    base = {"corrected_message": "tell me about the MBA", "intent": "programme_overview", "requested_info": None, "degree_type": "MBA", "academic_level": "Postgraduate", "discipline": None,
            "specialization": None, "campus": None, "entry_type": None, "search_keywords": [], "irrelevant_tokens": ["wat", "bout"], "refers_to_active_programme": False,
            "clarification": None, "confidence": 0.9}
    return {**base, **kw}


def test_a_model_interpretation_of_an_unusual_phrasing_yields_the_same_conversational_answer(client, conv, monkeypatch):
    from tests.test_understanding import FakeRunnerProvider
    monkeypatch.setenv("UNDERSTANDING_MODE", "llm")
    U.set_interpreter(U.Runner([FakeRunnerProvider(_nlu())]))
    try:
        r = converse(client, ["wat cn u tel me bout mbaa pls"])[0]
        assert r["understanding"]["source"] == "llm" and r["intent"] == "programme_overview" and r["state"]["degree"] == "MBA" and no_ui_clutter(r) and "MBA options" in r["reply"]
        U.set_interpreter(U.Runner([FakeRunnerProvider(_nlu(intent="concept_question", degree_type=None, academic_level=None, concept="lateral entry", corrected_message="what is lateral entry"))]))
        c = converse(client, ["wat iz lateral entri"])[0]
        assert c["intent"] == "concept_question" and "second year" in c["reply"] and c["tools_used"] == ["get_glossary_term"]
        U.set_interpreter(U.Runner([FakeRunnerProvider(_nlu(intent="programme_count", degree_type=None, academic_level=None, corrected_message="how many programmes"))]))
        n = converse(client, ["hw many progs u hv"])[0]
        assert n["intent"] == "programme_count" and "programme listings" in n["reply"]
        U.set_interpreter(U.Runner([FakeRunnerProvider(_nlu(intent="programme_count", degree_type="B.Quantum"))]))              # a corrupt entity never reaches a filter
        assert converse(client, ["some message the local reading is unsure about zorb"])[0]["state"]["degree"] is None
    finally:
        U.set_interpreter(False)


# ---------------------------------------------------------------- the AI voice is optional and grounded
class _Prov:
    model = "fake-voice"

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def complete(self, messages, tools=None, temperature=0.4):
        self.calls += 1
        return LLMResponse(self.reply, [])


def test_the_ai_voice_may_reword_verified_prose_but_never_adds_figures(client, conv, monkeypatch):
    monkeypatch.setenv("COMPOSE", "on")
    stats = converse(client, ["How many programmes are there?"])[0]["reply"]
    n = re.search(r"\*\*(\d+) programme listings", stats).group(1)
    good = _Prov(f"Right now our records show {n} programme listings, so there is plenty to explore. By level: see the numbers above.")
    monkeypatch.setattr("app.routers.api.get_composer", lambda: Composer([good]))
    r = converse(client, ["How many programmes are there?"])[0]
    assert r["reply"].startswith("Right now our records show") and good.calls == 1
    bad = _Prov("We have 9999 programmes and counting, many more than you would expect from a university of this size.")
    monkeypatch.setattr("app.routers.api.get_composer", lambda: Composer([bad]))
    r2 = converse(client, ["How many programmes are there?"])[0]
    assert "9999" not in r2["reply"] and f"**{n} programme listings**" in r2["reply"]                           # an invented figure is rejected: the verified text stays
    tbl = _Prov("irrelevant")
    monkeypatch.setattr("app.routers.api.get_composer", lambda: Composer([tbl]))
    converse(client, ["Compare B.Tech and BSc Computer Science at CHRIST."])
    assert tbl.calls == 0                                                                                       # tables are never reworded


def test_session_memory_isolated_per_conversation(client, conv):
    a = converse(client, ["Tell me about MBA.", "What is the eligibility?"], cid="mba-chat")
    b = converse(client, ["I want B.Tech.", "Which campus?"], cid="btech-chat")
    assert "MBA" in a[1]["reply"] and "MBA" not in b[1]["reply"] and b[1]["state"]["degree"] == "B.Tech" and a[1]["state"]["degree"] == "MBA"

"""Conversation behaviour: strict degree/specialization filtering, per-chat state, small talk, follow-ups, duplicate submissions."""
import asyncio
import copy

import pytest

from app.catalog import set_catalog
from app.catalog.normalize import classify_programme, norm_key
from app.catalog.search import State, find_programmes, merge, parse_query
from tests.fake_catalog import ROWS, FakeReader, _p, _s, build


def eng_rows():
    r = copy.deepcopy(ROWS)
    n = "Bachelor of Technology in "
    r["programs"] += [
        _p(81, n + "Computer Science and Engineering", 1, 24, 11, intDuration=4, chrTermType="S"),
        _p(457, n + "Computer Science and Engineering (Artificial Intelligence and Machine Learning)", 1, 24, 11, intDuration=4),
        _p(511, n + "Computer Science and Engineering (Artificial Intelligence and Machine Learning) - (Lateral Entry)", 1, 24, 11, intDuration=3),
        _p(536, n + "Artificial Intelligence and Machine Learning", 1, 24, 11, intDuration=4),
        _p(18, n + "Civil Engineering", 1, 24, 11, intDuration=4),
        _p(835, "BSc (Computer Science, Data Science/Honours/Honours with Research)", 1, 1, 10, intDuration=3),
        _p(836, "BSc (Artificial Intelligence and Machine Learning/Honours)", 1, 1, 10, intDuration=3),
        _p(837, "Bachelor of Computer Applications", 1, 1, 10, intDuration=3),
        _p(54, "Master of Science Computer Science and Applications", 2, 1, 10),
        _p(76, "Doctor of Philosophy in Computer Science and Engineering", 8, 24, 11, intDuration=3),
        _p(900, "Master of Business Administration (MBA)", 2, 1, 10),               # same programme listed under a second course id: must collapse
        _p(73, n + "Mechanical Engineering", 1, 24, 11, intDuration=4),
        _p(928, n + "Automotive Technology", 1, 24, 11, intDuration=4),
        _p(468, n + "Electronics and Computer Engineering", 1, 24, 11, intDuration=4),
        _p(458, n + "Computer Science and Engineering (Data Science)", 1, 24, 11, intDuration=4),
    ]
    r["placements"] += [{"department_id": 11, "course_id": 536, "campus_id": 24, "main_campus_deptID": 11},
                        {"department_id": 10, "course_id": 81, "campus_id": 1, "main_campus_deptID": 10}, {"department_id": 11, "course_id": 81, "campus_id": 24, "main_campus_deptID": 11}]
    r["sections"] += [_s(40, 536, "Eligibility", "T", "<p>A pass at the +2 level with Mathematics and Physics and 50% marks.</p>", "E"),
                      _s(41, 457, "Eligibility", "T", "<p>A pass at the +2 level with 55% marks including Mathematics.</p>", "E"),
                      _s(42, 81, "Eligibility", "T", "<p>A pass at the +2 level with 50% marks.</p>", "E")]
    return r


@pytest.fixture
def eng(monkeypatch):
    from app.catalogue import reset_catalogue
    from app.sources import set_source
    set_source(None)
    monkeypatch.setenv("CATALOGUE_SOURCE", "catalog")
    reset_catalogue()
    c = build(FakeReader(eng_rows()))
    set_catalog(c)
    return c


def names(groups):
    return [g.rep.name for g in groups]


# ---------------------------------------------------------------- data model
def test_degree_type_specialization_and_entry_are_derived_from_the_name():
    c = classify_programme("Bachelor of Technology in Computer Science and Engineering (Artificial Intelligence and Machine Learning) - (Lateral Entry)", "UG")
    assert (c.degree_type, c.academic_level, c.field, c.specialization, c.entry) == ("B.Tech", "Undergraduate", "Computer Science and Engineering", "Artificial Intelligence and Machine Learning", "lateral")
    b = classify_programme("BSc (Computer Science, Data Science/Honours/Honours with Research)", "UG")
    assert (b.degree_type, b.field, b.specialization, b.honours) == ("BSc", "Computer Science", "Data Science", True)
    d = classify_programme("Doctor of Philosophy (PhD) in Biotechnology", "PhD")
    assert (d.degree_type, d.academic_level, d.field) == ("PhD", "Doctoral", "Biotechnology")
    assert classify_programme("Master of Technology in Mechanical Engineering (Specialization in Machine Design)", "PG").specialization == "Machine Design"
    assert classify_programme("Bachelor of Hotel Management (BHM)", "UG").degree_type == "Other"        # unknown stays unknown, never guessed
    assert norm_key("Master of Business Administration (MBA) ") == norm_key("master of business administration (mba)") and norm_key("X - (Lateral Entry)") == norm_key("X")


def test_programmes_carry_the_structured_fields(eng):
    p = eng.programs[457]
    assert p.degree_type == "B.Tech" and p.academic_level == "Undergraduate" and p.specialization.startswith("Artificial") and p.entry == "regular" and p.dedupe_key
    assert eng.programs[511].entry == "lateral" and eng.programs[511].dedupe_key == p.dedupe_key and eng.programs[501].academic_level == "Doctoral"


# ---------------------------------------------------------------- strict filtering
def test_only_btech_returns_only_btech_not_bsc_bca_msc_or_phd(eng):
    s = merge(State(), parse_query("Computer Science", eng))
    assert {g.rep.degree_type for g in find_programmes(eng, s.query())} >= {"B.Tech", "BSc", "MSc", "PhD"}       # across levels first
    s = merge(s, parse_query("Only B.Tech", eng))
    got = find_programmes(eng, s.query())
    assert {g.rep.degree_type for g in got} == {"B.Tech"} and all("Computer Science" in g.rep.name for g in got)


def test_btech_aiml_excludes_bsc_aiml_and_other_btech(eng):
    s = merge(merge(State(), parse_query("B.Tech", eng)), parse_query("AI/ML", eng))
    got = find_programmes(eng, s.query())
    assert {g.rep.id for g in got} == {457, 536}                                         # the lateral listing collapses into 457; no BSc AIML (836), no CSE (81)
    assert got[0].lateral_also is True or got[1].lateral_also is True
    assert 836 not in {i for g in got for i in g.ids}
    assert {g.rep.id for g in find_programmes(eng, parse_query("BSc AI/ML", eng))} == {836}


def test_btech_without_a_match_says_so_and_be_is_not_btech(eng):
    assert find_programmes(eng, parse_query("B.Tech in law", eng)) == []
    q = parse_query("B.E in civil engineering", eng)
    assert q.degree == "B.E" and find_programmes(eng, q) == []                          # B.E is never silently treated as B.Tech


def test_duplicates_collapse_and_new_degree_listing_resets_the_topic(eng):
    mba = find_programmes(eng, parse_query("MBA", eng))
    assert len(mba) == 1 and mba[0].ids == (334, 900)                                     # the same programme under two course ids is one entry
    s = merge(merge(State(), parse_query("B.Tech AI/ML", eng)), parse_query("Show me BSc programmes", eng))
    assert s.degree == "BSc" and s.concepts == ()                                        # a fresh listing, not "BSc AI/ML"
    kept = merge(merge(State(), parse_query("B.Tech AI/ML", eng)), parse_query("only BSc", eng))
    assert kept.concepts == ("AI/ML",)                                                   # "only" keeps the topic


def test_state_never_leaks_between_chats(eng):
    a = merge(State(), parse_query("B.Tech AI/ML", eng))
    b = merge(State(), parse_query("BSc", eng))
    assert a.degree == "B.Tech" and b.degree == "BSc" and b.concepts == () and State.from_dict({"degree": "<script>", "programme_id": "x"}).degree is None


# ---------------------------------------------------------------- conversations through the API
def chat(client, messages, state=None, **kw):
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(messages)]
    return client.post("/api/chat", json={"messages": hist, "state": state, **kw}).json()


def converse(client, users, state=None, cid=None):
    hist, out = [], []
    for i, u in enumerate(users):
        hist.append({"role": "user", "content": u})
        r = client.post("/api/chat", json={"messages": hist, "state": state, "conversation_id": cid, "client_msg_id": f"{cid}-{i}" if cid else None}).json()
        state = r["state"]
        hist.append({"role": "assistant", "content": r["reply"]})
        out.append(r)
    return out


def test_greetings_are_natural_and_never_repeat(client, eng):
    r = converse(client, ["Hello", "Hi", "hey", "hello there", "hi", "hello"])
    texts = [x["reply"] for x in r]
    assert texts[0] == "Hello! Welcome to CHRIST University. How can I assist you today?" and texts[1] == "Hi again! What would you like to explore today?"
    assert texts[2] == "Hey! How can I help you with your university admission queries?"
    assert len(set(texts[:5])) == 5 and all(texts[i] != texts[i + 1] for i in range(5))
    assert all("Which course would you like to enrol in" not in t for t in texts)         # the welcome message is not repeated
    assert all(x["intent"] == "smalltalk_greeting" and x["tools_used"] == [] for x in r)


def test_thanks_and_ok_get_acknowledgements_not_greetings(client, eng):
    r = converse(client, ["thanks", "ok", "okay", "thank you so much"])
    assert all(x["intent"].startswith("smalltalk_") and "Welcome to CHRIST" not in x["reply"] for x in r) and len({x["reply"] for x in r}) == 4


def test_test2_computer_science_then_only_btech(client, eng):
    cs, only = converse(client, ["Computer Science", "Only B.Tech"])
    assert "Computer Science" in cs["reply"] and cs["cards"] == [] and "|" not in cs["reply"]                      # prose, no automatic cards or tables
    assert {eng.programs[i].academic_level for i in cs["state"]["last_ids"]} >= {"Undergraduate", "Postgraduate"}   # across levels first, remembered for follow-ups
    assert only["state"]["degree"] == "B.Tech" and {eng.programs[i].degree_type for i in only["state"]["last_ids"]} == {"B.Tech"}
    assert "BSc" not in only["reply"] and "Master of" not in only["reply"] and "Doctor of" not in only["reply"] and "Bachelor of Technology" in only["reply"] and only["cards"] == []


def test_test3_btech_then_aiml(client, eng):
    btech, aiml = converse(client, ["B.Tech", "AI/ML"])
    assert btech["intent"] == "programme_search" and "B.Tech" in btech["reply"] and btech["cards"] == [] and "|" not in btech["reply"]
    got = [eng.programs[i].name for i in aiml["state"]["last_ids"]]
    assert len(got) == 2 and all(n.startswith("Bachelor of Technology") for n in got) and not any("BSc" in n for n in got)
    assert "Artificial Intelligence and Machine Learning" in " ".join(got) and "Artificial Intelligence and Machine Learning" in aiml["reply"] and aiml["cards"] == []


def test_test4_5_follow_up_questions_use_the_programme_under_discussion(client, eng):
    sel = "Tell me about Bachelor of Technology in Artificial Intelligence and Machine Learning"
    r = converse(client, ["B.Tech AI/ML", sel, "What is the eligibility?", "What are the fees?", "thanks", "what is the eligibility"])
    assert r[1]["state"]["programme_id"] == 536 and r[1]["intent"] == "programme_overview"
    assert "Mathematics and Physics" in r[2]["reply"] and "Artificial Intelligence and Machine Learning" in r[2]["reply"] and r[2]["intent"] == "eligibility_criteria"
    assert r[3]["intent"] == "programme_fees" and "not connected" in r[3]["reply"] and "Artificial Intelligence" in r[3]["reply"]       # fees: honest, not invented
    assert r[4]["intent"] == "smalltalk_thanks" and r[4]["cards"] == [] and r[4]["follow_ups"] == []                                 # a thank-you is just acknowledged
    assert r[4]["state"]["programme_id"] == 536 and r[5]["state"]["programme_id"] == 536 and "Mathematics and Physics" in r[5]["reply"]   # context survives small talk


def test_eligibility_with_several_matches_asks_which_instead_of_guessing(client, eng):
    r = converse(client, ["B.Tech AI/ML", "What is the eligibility?"])
    assert r[1]["intent"] == "eligibility_criteria" and "published separately for each programme" in r[1]["reply"] and "Mathematics and Physics" in r[1]["reply"] and "Reply with" not in r[1]["reply"]
    assert r[1]["state"]["programme_id"] is None                    # still no single programme chosen: the answer covered the active results


def test_test6_bsc_only_shows_bsc(client, eng):
    r = converse(client, ["Show me BSc programmes"])[0]
    text = r["reply"] + " ".join(c["name"] for c in r["cards"])
    assert r["state"]["degree"] == "BSc" and "BSc (" in text and "Bachelor of Technology" not in text and "Master" not in text and "Bachelor of Computer Applications" not in text


def test_no_match_is_stated_clearly_with_optional_alternatives(client, eng):
    r = converse(client, ["PhD in civil engineering"])[0]
    assert r["intent"] == "no_match" and "couldn't find a matching" in r["reply"] and r["cards"] == []
    ignored = converse(client, ["B.Tech in zorblax"])[0]          # an unknown word is never a filter, and the answer says it was left out
    assert ignored["intent"] == "programme_search" and "didn't recognise" in ignored["reply"] and "zorblax" in ignored["reply"]
    be = converse(client, ["B.E in civil engineering"])[0]
    assert "couldn't find a matching **B.E** programme" in be["reply"] and any("B.Tech" in f for f in be["follow_ups"])


def test_client_state_is_validated_and_a_new_chat_starts_clean(client, eng):
    r = chat(client, ["hello"], state={"degree": "B.Tech", "programme_id": "oops", "concepts": ["nonsense"], "level": 5})
    assert r["state"]["degree"] == "B.Tech" and r["state"]["programme_id"] is None and r["state"]["concepts"] == []
    fresh = converse(client, ["Only BSc"])[0]
    assert fresh["state"]["degree"] == "BSc" and fresh["state"]["concepts"] == []


def test_unrelated_questions_fall_through_to_the_model(client, eng, monkeypatch):
    from tests.test_decision_engine import FakeDecider, ans
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider(ans("hostel")))
    r = converse(client, ["is there a hostel at kengeri"])[0]
    assert r["intent"] == "facilities"                                                    # not hijacked by the programme search


# ---------------------------------------------------------------- duplicate submissions and loops
def test_a_repeated_submission_is_answered_once(client, eng, monkeypatch):
    from app.routers import api
    calls = []
    real = api.dialog_respond
    monkeypatch.setattr(api, "dialog_respond", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    body = {"messages": [{"role": "user", "content": "B.Tech AI/ML"}], "client_msg_id": "abc-123"}
    first = client.post("/api/chat", json=body).json()
    second = client.post("/api/chat", json=body).json()
    assert first == second and len(calls) == 1
    other = client.post("/api/chat", json={**body, "client_msg_id": "abc-124"}).json()
    assert len(calls) == 2 and other["reply"] == first["reply"]
    assert client.post("/api/chat", json={**body, "client_msg_id": "bad id!"}).status_code == 422


async def test_concurrent_identical_submissions_run_once(eng, monkeypatch):
    import httpx
    from app.main import app
    from app.routers import api
    calls = []
    real = api.dialog_respond

    def slow(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(api, "dialog_respond", slow)
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_MIN", "1000")
    body = {"messages": [{"role": "user", "content": "Only B.Tech AI/ML"}], "client_msg_id": "race-1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        rs = await asyncio.gather(*[c.post("/api/chat", json=body) for _ in range(8)])
    assert {r.status_code for r in rs} == {200} and len({r.text for r in rs}) == 1 and len(calls) == 1


def test_rapid_distinct_messages_each_get_exactly_one_reply(client, eng, monkeypatch):
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_MIN", "1000")
    state, hist, replies = None, [], []
    for i, m in enumerate(["hi", "B.Tech", "AI/ML", "thanks", "BSc", "ok", "hello", "Only B.Tech AI/ML"]):
        hist.append({"role": "user", "content": m})
        r = client.post("/api/chat", json={"messages": hist, "state": state, "client_msg_id": f"m{i}"}).json()
        state = r["state"]
        hist.append({"role": "assistant", "content": r["reply"]})
        replies.append(r["reply"])
    assert len(replies) == 8 and all(replies) and len([h for h in hist if h["role"] == "assistant"]) == 8
    assert all(replies[i] != replies[i + 1] for i in range(7))                             # no consecutive duplicate answers


def test_frontend_guards_against_double_submits():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    assert "if (s.busy) return;" in js and "client_msg_id" in js and "state: s.state" in js and js.count("s.busy = true") >= 2


def test_clean_programme_record_has_every_field_and_invents_nothing(eng):
    from app.catalog.record import programme_record
    r = programme_record(eng, eng.programs[536])
    assert {"programme_id", "programme_name", "degree_type", "academic_level", "specialization", "department", "campus", "duration_years", "eligibility", "fees",
            "programme_description", "curriculum", "career_opportunities", "admission_details"} <= set(r)
    assert r["degree_type"] == "B.Tech" and r["academic_level"] == "Undergraduate" and "Mathematics and Physics" in r["eligibility"][0]["text"]
    assert r["fees"]["available"] is False and r["career_opportunities"] == [] and r["curriculum"] == [] and r["specialization"] is None   # missing stays missing
    assert programme_record(eng, eng.programs[334])["duplicate_listing_ids"] == [900]


def test_a_dialog_bug_never_kills_the_chat(client, eng, monkeypatch):
    from app.routers import api
    from tests.test_decision_engine import FakeDecider, ans
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider(ans("greeting")))
    monkeypatch.setattr(api, "dialog_respond", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "B.Tech"}]})
    assert r.status_code == 200 and r.json()["reply"]

"""Pending numbered selections, fees for the active results, degree switching, stale cards, greetings and the frontend guards."""
import json
from pathlib import Path

import pytest

from tests.test_dialog import converse, eng, eng_rows  # noqa: F401  (fixture re-export)


def ids(r):
    return [c["programme_id"] for c in r["cards"]]


def cat_of(eng_):
    return eng_


# ---------------------------------------------------------------- numeric selection
def test_every_number_resolves_to_its_own_saved_listing_id_and_the_question_is_not_repeated(client, eng):
    first = converse(client, ["Show me the B.Tech programmes"])[0]
    opts = first["state"]["pending"]["options"]
    assert first["intent"] == "programme_search" and len(opts) == 5 and [c["number"] for c in first["cards"]] == [1, 2, 3, 4, 5]     # a browse request: five numbered options + cards
    for n in range(1, 6):
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "Show me the B.Tech programmes"}, {"role": "assistant", "content": first["reply"]},
                                                         {"role": "user", "content": str(n)}], "state": first["state"]}).json()
        name = eng.programs[opts[n - 1]["programme_id"]].name
        assert r["state"]["programme_id"] == opts[n - 1]["programme_id"] and r["intent"] == "programme_overview" and name.split(" in ")[-1].split(" (")[0] in r["reply"], n
        assert "Reply with its number" not in r["reply"] and r["cards"] == []
    more = client.post("/api/chat", json={"messages": [{"role": "user", "content": "Show me the B.Tech programmes"}, {"role": "assistant", "content": first["reply"]}, {"role": "user", "content": "show more"}],
                                          "state": first["state"]}).json()
    assert more["state"]["shown"] > 5 and len(more["state"]["pending"]["options"]) > 5 and "next ones" in more["reply"]


def test_natural_selections(client, eng):
    first = converse(client, ["Show me the B.Tech programmes"])[0]
    opts = first["state"]["pending"]["options"]
    name = lambda n: eng.programs[opts[n - 1]["programme_id"]].name

    def pick(msg):
        return client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": first["reply"]}, {"role": "user", "content": msg}],
                                              "state": first["state"]}).json()
    for msg, n in [("the first one", 1), ("The second programme.", 2), ("Option 3", 3), ("#4", 4), ("the last one", len(opts))]:
        assert name(n) in pick(msg)["reply"], msg
    ds = pick("I want the Data Science programme")
    assert "Computer Science and Engineering (Data Science)" in ds["reply"]
    cse = pick("Computer Science and Engineering")
    assert "Bachelor of Technology in Computer Science and Engineering**" in cse["reply"] or cse["intent"] == "selection_ambiguous"


def test_an_invalid_number_is_explained_and_the_pending_list_survives(client, eng):
    first = converse(client, ["Show me the B.Tech programmes"])[0]
    hist = [{"role": "user", "content": "Show me the B.Tech programmes"}, {"role": "assistant", "content": first["reply"]}, {"role": "user", "content": "99"}]
    bad = client.post("/api/chat", json={"messages": hist, "state": first["state"]}).json()
    assert bad["intent"] == "selection_invalid" and "isn't one of the options" in bad["reply"] and "1 to" in bad["reply"] and bad["reply"] != first["reply"]
    assert bad["state"]["pending"] == first["state"]["pending"]
    again = client.post("/api/chat", json={"messages": hist + [{"role": "assistant", "content": bad["reply"]}, {"role": "user", "content": "2"}], "state": bad["state"]}).json()
    assert again["state"]["programme_id"] == first["state"]["pending"]["options"][1]["programme_id"]


def test_a_number_with_no_pending_question_is_never_a_programme_choice(client, eng, monkeypatch):
    from tests.test_decision_engine import FakeDecider, ans
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider(ans("greeting")))
    r = converse(client, ["2"])[0]
    assert r["intent"] not in ("programme_details", "selection_invalid") and r["state"]["programme_id"] is None and "Welcome" not in r["reply"]


def test_pending_options_survive_a_refresh_and_tampering_is_dropped(client, eng):
    first = converse(client, ["Show me the B.Tech programmes"])[0]
    restored = json.loads(json.dumps(first["state"]))                           # what sessionStorage hands back after a page refresh
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "3"}], "state": restored}).json()
    assert r["state"]["programme_id"] == restored["pending"]["options"][2]["programme_id"]
    evil = {**restored, "pending": {"action": "select_programme", "requested": "fees", "options": [{"programme_id": 99999999}, {"programme_id": 81}, {"programme_id": "x"}, {"number": 1}]},
            "last_ids": [99999999, 81], "programme_id": 424242}
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "state": evil}).json()
    assert 99999999 not in r["state"]["last_ids"] and r["state"]["programme_id"] is None and all(o["programme_id"] != 99999999 for o in (r["state"]["pending"] or {"options": []})["options"])


# ---------------------------------------------------------------- fees use the active results
def test_fees_list_uses_the_same_listings_with_one_row_per_campus(client, eng):
    first, fees = converse(client, ["Show me B.Tech Computer Science", "Tell me fees list"])[0:2]
    shown = first["state"]["last_ids"]
    assert fees["intent"] == "fees_list" and fees["state"]["last_ids"] == shown            # the same ids, not a fresh search
    rows = [l.split("|") for l in fees["reply"].split("\n") if l.startswith("| ") and "---" not in l and " Programme " not in l]
    assert {eng.programs[i].name for i in shown} >= {r[2].strip().split(" · ")[0].split(" (Lateral")[0] for r in rows}
    assert all("Fee information currently unavailable." in r[5] for r in rows)
    cse = [r for r in rows if r[2].strip().startswith("Bachelor of Technology in Computer Science and Engineering ")][:0] or [r for r in rows if r[2].strip().split(" · ")[0] == "Bachelor of Technology in Computer Science and Engineering"]
    assert len(cse) == 2 and {r[3].strip() for r in cse} == {"Bangalore Central Campus", "Bangalore Kengeri Campus"}   # campus-specific rows stay separate
    assert "Mechanical" not in fees["reply"] and "Automotive" not in fees["reply"] and "Civil" not in fees["reply"]        # nothing outside the result set
    assert "won't guess" in fees["reply"] and "5,70,000" not in fees["reply"]


def test_fees_with_a_selected_programme_or_a_later_number(client, eng):
    sel = "Tell me about Bachelor of Technology in Artificial Intelligence and Machine Learning"
    r = converse(client, ["B.Tech AI/ML", sel, "fees?"])
    assert "Artificial Intelligence and Machine Learning" in r[2]["reply"] and r[2]["intent"] == "programme_fees" and "not available in the current university database" in r[2]["reply"]


# ---------------------------------------------------------------- degree switching and stale cards
def test_a_new_degree_replaces_the_old_filter_and_no_old_cards_leak(client, eng):
    r = converse(client, ["I want B.Tech.", "What about BCA?", "What about BSc?", "What about B.E?"])
    btech, bca, bsc, be = r
    assert btech["state"]["degree"] == "B.Tech" and {eng.programs[i].degree_type for i in btech["state"]["last_ids"]} <= {"B.Tech"}
    assert bca["state"]["degree"] == "BCA" and bca["state"]["last_ids"] == [837] and bca["cards"] == []
    assert bsc["state"]["degree"] == "BSc" and not set(btech["state"]["last_ids"]) & set(bsc["state"]["last_ids"]) and "Bachelor of Technology" not in bsc["reply"] + str(bsc["cards"])
    assert be["intent"] == "no_match" and be["cards"] == [] and be["state"]["last_ids"] == [] and be["state"]["pending"] is None
    assert "different degree" in be["reply"] and "B.Tech" in be["reply"]                      # offered, never substituted


def test_no_bca_listing_gives_an_honest_empty_answer_without_cards(client, eng):
    import dataclasses
    from app.catalog import current_catalog, set_catalog
    c = current_catalog()
    set_catalog(dataclasses.replace(c, programs={k: v for k, v in c.programs.items() if v.degree_type != "BCA"}))
    r = converse(client, ["I want B.Tech.", "What about BCA?"])[1]
    assert r["intent"] == "no_match" and r["cards"] == [] and "couldn't find a matching" in r["reply"] and "Bachelor of Technology" not in r["reply"]


def test_the_topic_is_kept_only_when_the_user_says_only(client, eng):
    keep = converse(client, ["B.Tech AI/ML", "only BSc"])[1]
    assert keep["state"]["concepts"] == ["AI/ML"] and keep["state"]["degree"] == "BSc" and keep["state"]["last_ids"] == [836]      # still AI/ML, now only BSc
    fresh = converse(client, ["B.Tech AI/ML", "BSc"])[1]
    assert fresh["state"]["concepts"] == [] and fresh["state"]["degree"] == "BSc"


def test_cards_and_text_come_from_the_same_result(client, eng):
    r = converse(client, ["Show me the B.Tech AI/ML programmes"])[0]
    assert ids(r) == r["state"]["last_ids"] and all(eng.programs[i].name.split(" · ")[0] in r["reply"] for i in ids(r)) and len(ids(r)) == 2
    assert [c["number"] for c in r["cards"]] == list(range(1, len(r["cards"]) + 1))
    prose = converse(client, ["B.Tech AI/ML"])[0]                                  # not a browse request: prose, no cards, but the same result is remembered
    assert prose["cards"] == [] and prose["state"]["last_ids"] == r["state"]["last_ids"]


# ---------------------------------------------------------------- greetings
@pytest.mark.parametrize("msg", ["hello", "hi", "hey", "hello there", "good morning", "good afternoon", "good evening", "howdy", "hellow", "helloo", "hii", "Hallo"])
def test_greeting_variants_are_recognised_without_any_lookup(client, eng, monkeypatch, msg):
    import app.catalogue as cm
    import app.agent.decision_engine as de
    boom = lambda: (_ for _ in ()).throw(AssertionError("a greeting must not touch the database"))
    monkeypatch.setattr(cm, "get_catalogue", boom)
    monkeypatch.setattr(de, "get_catalogue", boom)
    r = converse(client, [msg])[0]
    assert r["intent"] == "smalltalk_greeting" and r["tools_used"] == [] and r["cards"] == [] and "enrol" not in r["reply"]
    if "good" in msg:
        assert r["reply"].startswith(f"Good {msg.split()[1]}! ")


def test_greetings_follow_the_expected_sequence_and_keep_the_context(client, eng):
    welcome = "Hello! Welcome to CHRIST University. How can I help you? Which course would you like to enrol in at our college?"
    hist, state = [{"role": "assistant", "content": welcome}], None
    out = []
    for m in ["Hello", "Hi", "Hey", "Good morning"]:
        hist.append({"role": "user", "content": m})
        r = client.post("/api/chat", json={"messages": hist, "state": state}).json()
        out.append(r["reply"])
        hist.append({"role": "assistant", "content": r["reply"]})
    assert out == ["Hello! Welcome to CHRIST University. How can I assist you today?", "Hi again! What would you like to explore today?",
                   "Hey! How can I help you with your university admission queries?", "Good morning! What information are you looking for?"]
    r = converse(client, ["B.Tech AI/ML", "hi", "fees?"])
    assert r[1]["state"] == r[0]["state"] and "numbered" not in r[1]["reply"] and r[2]["intent"] in ("fees_list", "programme_fees", "clarify")   # a greeting loses nothing
    assert "reply with its number" in r[1]["reply"].lower() or "reply with a number" in r[1]["reply"].lower() or r[1]["state"]["last_ids"]


def test_help_is_not_a_greeting_and_a_greeting_with_a_question_is_a_question(client, eng):
    from app.agent.dialog import smalltalk_kind
    assert smalltalk_kind("help") is None and smalltalk_kind("hello I want B.Tech") is None and smalltalk_kind("hi") == "greeting" and smalltalk_kind("hellow") == "greeting"


def test_the_welcome_message_is_never_a_fallback(client, eng, monkeypatch):
    from tests.test_decision_engine import FakeDecider, ans
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider(ans("greeting")))
    r = converse(client, ["2"])[0]
    assert "How can I help you? Which course would you like to enrol" not in r["reply"]


# ---------------------------------------------------------------- conversations and requests
def test_two_chats_never_share_state(client, eng):
    a = converse(client, ["B.Tech AI/ML"], cid="chat-a")[0]
    b = converse(client, ["BSc"], cid="chat-b")[0]
    assert a["state"]["degree"] == "B.Tech" and b["state"]["degree"] == "BSc" and b["state"]["concepts"] == [] and not set(ids(a)) & set(ids(b))
    assert a["response_id"] != b["response_id"]


def test_responses_carry_ids_and_the_same_message_id_in_two_chats_is_not_deduplicated(client, eng):
    body = lambda cid: {"messages": [{"role": "user", "content": "B.Tech"}], "client_msg_id": "same", "conversation_id": cid}
    a, b = client.post("/api/chat", json=body("c1")).json(), client.post("/api/chat", json=body("c2")).json()
    assert a["in_reply_to"] == "same" and a["response_id"] != b["response_id"]
    assert client.post("/api/chat", json=body("c1")).json() == a                           # the same chat repeating it gets the cached answer


def test_frontend_drops_stale_responses_and_binds_cards_to_their_message():
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    assert "if (mine !== s.seq) return;" in js and "s.seq++;" in js and "conversation_id" in js and "r.response_id" in js
    assert "cards: (r.cards || []).filter((c) => c && c.programme_id)" in js and "m.cards?.length" in js and "const cards = " not in js

"""Spelling, intent and entity understanding; schema validation of the AI interpreter; the tools chosen for each intent."""
import json

import pytest

from app.agent import understanding as U
from app.catalog import set_catalog, set_reader
from app.llm.base import LLMResponse
from tests.fake_catalog import FakeReader, build
from tests.test_dialog import converse, eng, eng_rows  # noqa: F401  (fixture re-export)


def interp(text, cat, **kw):
    return U.interpret(text, [{"role": "user", "content": text}], kw.get("state"), cat)


@pytest.fixture(autouse=True)
def no_ai_by_default(monkeypatch):
    monkeypatch.setenv("UNDERSTANDING_MODE", "auto")
    U.set_interpreter(None)
    yield
    U.set_interpreter(False)


# ---------------------------------------------------------------- spelling and normalisation
@pytest.mark.parametrize("raw,fixed", [
    ("I WAHT TO B.TECH", "i want to b.tech"), ("I WAHT TO LEAR COMPUTER SCIEANCE", "i want to learn computer science"), ("I LIKE COMPUTR SCIENCE", "i like computer science"),
    ("ENGINERING", "engineering"), ("I waht to b.tech in computer scieance", "I want to b.tech in computer science"), ("computr science", "computer science"),
])
def test_spelling_is_corrected_against_the_universitys_own_vocabulary(eng, raw, fixed):
    i = interp(raw, eng)
    assert i.corrected_message == fixed and i.original_message == raw                     # both are kept
    assert not i.unknown_tokens()


def test_context_decides_between_equally_close_words(eng):
    tax = U.taxonomy_for(eng)
    assert U.spell_correct("i waht to study", tax)[0] == "i want to study" and U.spell_correct("waht is the eligibility", tax)[0] == "what is the eligibility"
    assert U.spell_correct("to lear", tax)[0] == "to learn"                               # 'learn' not 'year'
    assert U.spell_correct("hostel", tax)[0] == "hostel"                                   # a valid word is never 'corrected'
    assert U.spell_correct("kengeri", tax)[0] == "kengeri" and U.spell_correct("zzzqx", tax)[0] == "zzzqx"   # unknown stays unknown


def test_osa_distance_counts_a_transposition_as_one():
    assert U.osa("waht", "want") == 1 and U.osa("scieance", "science") == 1 and U.osa("abc", "abc") == 0


def test_conversational_and_misspelt_filler_words_never_become_filters(eng):
    for raw in ("I want to learn BSc", "I waht to b.tech", "I WANT TO LEARN BSC", "please tell me about btech"):
        i = interp(raw, eng)
        assert not i.hard_words and not i.soft_words and not i.concepts, raw
        assert i.degree_type in ("BSc", "B.Tech") and i.intent in ("programme_search", "programme_overview")


def test_all_caps_input_does_not_turn_TO_BE_into_the_degree_BE(eng):
    assert interp("I WANT TO BE AN ENGINEER", eng).degree_type is None
    assert interp("I want to join B.E.", eng).degree_type == "B.E"                        # dotted B.E is a degree, and is never silently B.Tech


def test_restrictions_and_negations_are_preserved(eng):
    assert interp("only btech", eng).keep is True and interp("only btech", eng).degree_type == "B.Tech"
    assert interp("without lateral entry", eng).entry_type == "regular" and interp("not lateral", eng).entry_type == "regular"
    assert interp("btech lateral entry", eng).entry_type == "lateral"


# ---------------------------------------------------------------- intents and entities (general rules, not per-sentence scripts)
@pytest.mark.parametrize("msg,want", [
    ("I waht to b.tech", dict(intent="programme_search", degree_type="B.Tech")),
    ("TECH IN B.TECH", dict(intent="programme_search", degree_type="B.Tech")),
    ("tech courses", dict(intent="clarification_needed")),
    ("computer scieance", dict(intent="programme_search", discipline="Computer Science")),
    ("enginering", dict(intent="programme_search", discipline="engineering")),
    ("I want to learn BSc", dict(intent="programme_search", degree_type="BSc")),
    ("only btech", dict(degree_type="B.Tech", keep=True)),
    ("btech ai ml", dict(degree_type="B.Tech", specialization="AI/ML")),
    ("btech lateral entry", dict(intent="lateral_entry", entry_type="lateral", degree_type="B.Tech")),
    ("how to take lateral entry", dict(intent="lateral_entry")),
    ("direct second year", dict(intent="lateral_entry", entry_type="lateral")),
    ("how can i join directly in 2nd yr", dict(intent="lateral_entry")),
    ("can diploma holders join B.Tech", dict(intent="lateral_entry", degree_type="B.Tech")),
    ("diploma after 3 years admission", dict(intent="lateral_entry")),
    ("fees of this course", dict(intent="programme_info", requested_info="fees")),
    ("what about eligibility", dict(intent="programme_info", requested_info="eligibility")),
    ("bangalore only", dict(intent="programme_search", campus="Bangalore")),
    ("how do i apply", dict(intent="admission_process")),
    ("Good morning", dict(intent="greeting")),
    ("is there a hostel at kengeri", dict(intent="other")),
])
def test_informal_messages(eng, msg, want):
    got = interp(msg, eng).public()
    got["keep"] = interp(msg, eng).keep
    assert all(got.get(k) == v for k, v in want.items()), (msg, got)


def test_unknown_subject_words_are_reported_not_used(eng):
    i = interp("B.Tech in zorblax", eng)
    assert i.degree_type == "B.Tech" and i.unknown_tokens() == ["zorblax"] and not i.hard_words and i.confidence < 0.9


# ---------------------------------------------------------------- the AI interpreter: schema validation and safety
class FakeRunnerProvider:
    model = "fake-nlu"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def complete(self, messages, tools=None, temperature=0.0):
        self.calls.append(messages)
        r = self.replies.pop(0) if self.replies else "{}"
        if isinstance(r, Exception):
            raise r
        return LLMResponse(r if isinstance(r, str) else json.dumps(r), [])


def use(*replies, mode="llm"):
    p = FakeRunnerProvider(*replies)
    U.set_interpreter(U.Runner([p]))
    import os
    os.environ["UNDERSTANDING_MODE"] = mode
    return p


GOOD = {"corrected_message": "I want to study B.Tech in Computer Science", "intent": "programme_search", "requested_info": None, "degree_type": "B.Tech", "academic_level": "Undergraduate",
        "discipline": "Computer Science", "specialization": None, "campus": None, "entry_type": None, "search_keywords": ["Computer Science"], "irrelevant_tokens": ["want", "study"],
        "refers_to_active_programme": False, "clarification": None, "confidence": 0.95}


def test_a_valid_ai_interpretation_is_used_after_validation(eng):
    p = use(GOOD)
    i = interp("wanna do b tek in cs", eng)
    assert i.source == "llm" and i.degree_type == "B.Tech" and i.concepts == ["Computer Science"] and i.intent == "programme_search" and len(p.calls) == 1
    payload = json.loads(p.calls[0][1].content)
    assert "B.Tech" in payload["DEGREES"] and "Bangalore" in " ".join(payload["CAMPUSES"]) and payload["message"] == "wanna do b tek in cs"


def test_hallucinated_entities_are_dropped_before_any_search(eng):
    bad = {**GOOD, "degree_type": "B.Quantum", "campus": "Mars Campus", "discipline": "Zorblax Studies", "search_keywords": ["zorblax", "computer", "learn"], "irrelevant_tokens": []}
    use(bad)
    i = interp("some odd message about zorblax", eng)
    assert i.degree_type is None and i.campus is None and "zorblax" not in i.hard_words + i.soft_words and "learn" not in i.hard_words + i.soft_words
    assert any("unknown degree dropped" in n for n in i.notes) and any("unknown campus dropped" in n for n in i.notes) and "Computers" in i.concepts


def test_the_users_literal_degree_overrides_the_model(eng):
    use({**GOOD, "degree_type": "B.Tech"})
    i = interp("only BSc please", eng)
    assert i.degree_type == "BSc" and i.academic_level == "Undergraduate" and any("overridden" in n for n in i.notes)


def test_a_lateral_route_the_message_does_not_support_is_dropped(eng):
    use({**GOOD, "entry_type": "lateral"})
    assert interp("show btech computer science", eng).entry_type is None
    use({**GOOD, "intent": "lateral_entry", "entry_type": "lateral", "corrected_message": "can I join B.Tech second year directly"})
    assert interp("can i join btech sceond yaer directly", eng).entry_type == "lateral"      # the message itself says so (after correction)


def test_invalid_output_is_retried_once_then_the_local_interpretation_is_used(eng):
    p = use("not json at all", "{still not json", mode="llm")
    i = interp("some message I cannot parse", eng)
    assert i.source == "local" and len(p.calls) == 2 and any("unavailable or invalid" in n for n in i.notes)
    p = use("garbage", GOOD, mode="llm")                                                        # the retry (given the error) succeeds
    assert interp("wanna do b tek in cs", eng).source == "llm" and len(p.calls) == 2 and "invalid" in p.calls[1][-1].content.lower()
    use({**GOOD, "intent": "make_coffee"}, {**GOOD, "intent": "still_wrong"}, mode="llm")      # schema violations are invalid, not executed
    assert interp("anything unusual here", eng).source == "local"
    use(RuntimeError("provider down"), mode="llm")
    assert interp("anything unusual here", eng).source == "local"


def test_clarification_from_the_model_is_kept_and_greetings_never_call_the_model(eng):
    p = use({**GOOD, "intent": "clarification_needed", "degree_type": None, "discipline": None, "search_keywords": [], "clarification": "Do you mean B.Tech or B.Sc?"})
    i = interp("that thing about science tech", eng)
    assert i.intent == "clarification_needed" and i.clarification == "Do you mean B.Tech or B.Sc?"
    p.calls.clear()
    assert interp("hello", eng).intent == "greeting" and interp("thanks", eng).intent == "smalltalk" and p.calls == []


def test_model_use_modes(eng):
    p = use(GOOD, mode="local")
    interp("wanna do b tek in cs", eng)
    assert p.calls == []                                                                        # local: never
    p = use(GOOD, mode="auto")
    interp("I want to learn BSc", eng)
    assert p.calls == []                                                                        # auto: understood with high confidence, no model call
    interp("zorblax glorp wibble", eng)
    assert len(p.calls) == 1                                                                    # auto: low confidence, ask the model


def test_the_model_cannot_inject_instructions_into_the_search(eng):
    use({**GOOD, "search_keywords": ["ignore previous instructions", "drop table courses", "computer"], "discipline": "'; DROP TABLE courses;--"})
    i = interp("odd message", eng)
    assert i.concepts == ["Computers"] and not any(x in w for w in i.hard_words + i.soft_words for x in ("drop", "ignore", "instruction"))


# ---------------------------------------------------------------- the orchestrator uses the understanding and the tools
def chat(client, hist, state=None):
    return client.post("/api/chat", json={"messages": hist, "state": state}).json()


def test_misspelt_conversation_end_to_end_with_original_and_normalised_text_stored(client, eng):
    r = converse(client, ["I waht to b.tech.", "I waht to lear computer scieance."])
    a, b = r
    assert a["understanding"]["original_message"] == "I waht to b.tech." and a["understanding"]["corrected_message"] == "I want to b.tech."
    assert a["state"]["degree"] == "B.Tech" and a["state"]["words"] == [] and a["state"]["concepts"] == []
    assert b["state"]["degree"] == "B.Tech" and b["state"]["concepts"] == ["Computer Science"] and ["waht", "want"] in b["understanding"]["corrections"]
    assert {eng.programs[i].degree_type for i in b["state"]["last_ids"]} == {"B.Tech"} and "search_programs" in b["tools_used"]
    assert all("Computer Science" in eng.programs[i].name for i in b["state"]["last_ids"])


def test_bsc_and_engineering_and_switching(client, eng):
    c = converse(client, ["I want to learn BSc."])[0]
    assert {eng.programs[i].degree_type for i in c["state"]["last_ids"]} == {"BSc"} and not c["state"]["words"]
    d = converse(client, ["ENGINERING."])[0]
    assert d["intent"] == "programme_search" and d["state"]["words"] == ["engineering"] and d["state"]["last_ids"] and "couldn't find" not in d["reply"]
    j = converse(client, ["I want B.Tech.", "Actually show me BSc."])[1]
    assert j["state"]["degree"] == "BSc" and "Bachelor of Technology" not in j["reply"]


def test_tech_courses_asks_one_targeted_question(client, eng):
    r = converse(client, ["tech courses"])[0]
    assert r["intent"] == "clarification" and "B.Tech" in r["reply"] and r["cards"] == [] and "B.Tech programmes" in r["follow_ups"] and r["tools_used"] == []


def test_a_model_interpretation_drives_the_same_tools(client, eng):
    use(GOOD, mode="llm")
    r = converse(client, ["wanna do b tek in cs"])[0]
    assert r["understanding"]["source"] == "llm" and r["state"]["degree"] == "B.Tech" and r["state"]["concepts"] == ["Computer Science"] and "search_programs" in r["tools_used"]
    assert r["understanding"]["hard_constraints"] == ["degree_type", "academic_level", "discipline"]


def test_search_programs_tool_validates_its_arguments(eng):
    from app.tools import ToolContext, execute
    run = lambda **a: execute("search_programs", json.dumps(a), ToolContext()).data
    ok = run(degree_type="B.Tech", discipline="Computer Science")
    assert ok["found"] and all(p["degree_type"] == "B.Tech" for p in ok["programmes"]) and ok["filters"]["concepts"] == ["Computer Science"]
    assert run(degree_type="B.Quantum")["found"] is False and "valid_degrees" in run(degree_type="B.Quantum")
    assert run(degree_type="B.Tech", specialization="AI/ML", entry_type="lateral")["programmes"][0]["entry"] == "lateral"
    assert run(degree_type="BSc", specialization="AI/ML")["programmes"][0]["degree_type"] == "BSc"

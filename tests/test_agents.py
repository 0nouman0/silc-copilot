"""The two agents (information vs recommendation), the router, the welcome message and per-agent models."""
import json

from app.agent.decision_engine import run_decision_agent
from app.agent.prompt import build_system_prompt
from app.agent.router import route
from app.catalogue import detect_level, get_catalogue
from app.tools import ToolContext, available_tools, execute
from tests.conftest import FakeProvider, call, text
from tests.test_decision_engine import FakeDecider, H, ans

CTX = ToolContext()


async def run(intent, history, last_agent=None, **kw):
    return await run_decision_agent(FakeDecider(ans(intent, **kw)), history, last_agent=last_agent)


def chat(client, msg="hi", **extra):
    return client.post("/api/chat", json={"messages": [{"role": "user", "content": msg}], **extra})


def card_names(result):
    return [c["name"] for c in result.cards]


# ---------------------------------------------------------------- routing
def test_router_sends_choosing_questions_to_the_recommender_and_facts_to_the_info_agent():
    for t in ("Which course should I choose?", "I'm interested in law", "suggest something for me", "I love coding", "what should I study after 12th",
              "I'm confused about what to do", "help me choose a programme"):
        assert route(H(("user", t)), None) == "recommend", t
    for t in ("What are the fees for the MBA?", "eligibility for BBA", "how do I apply", "show the curriculum of MBA", "application dates", "status of my application"):
        assert route(H(("user", t)), None) == "info", t


def test_router_is_sticky_while_the_applicant_answers_the_recommender():
    assert route(H(("user", "undergraduate in bangalore")), "recommend") == "recommend"
    assert route(H(("user", "undergraduate in bangalore")), "info") == "info"
    assert route(H(("user", "what are the fees for the BBA?")), "recommend") == "info"          # a concrete fact question leaves the recommender
    assert route(H(("user", "I'm interested in fees and finance")), "info") == "recommend"      # explicit interest wins over a stray keyword


# ---------------------------------------------------------------- separate tool sets
def test_info_agent_cannot_recommend_and_recommender_cannot_quote_facts():
    info = {t.name for t in available_tools(CTX, "info")}
    rec = {t.name for t in available_tools(CTX, "recommend")}
    assert not info & {"recommend_programmes", "rank_programs"} and {"get_fees", "get_eligibility_criteria", "get_campus_facilities"} <= info
    assert {"recommend_programmes", "rank_programs", "check_eligibility", "search_programmes", "get_campus_details"} <= rec
    assert not rec & {"get_fees", "get_eligibility_criteria", "get_programme_section", "get_application_dates", "assess_preliminary_eligibility"}
    assert execute("recommend_programmes", json.dumps({"interests": "business"}), CTX, "info").data["error"] == "tool_unavailable"
    assert execute("get_fees", json.dumps({"programme_id": 334}), CTX, "recommend").data["error"] == "tool_unavailable"
    assert "recommendations" in execute("recommend_programmes", json.dumps({"interests": "business"}), CTX, "recommend").data


def test_each_agent_has_its_own_prompt_and_neither_mentions_accounts_or_demo_data():
    info = build_system_prompt(agent="info")
    rec = build_system_prompt(agent="recommend")
    assert "INFORMATION agent" in info and "do NOT recommend" in info and "recommend_programmes" not in info and "get_campus_details" in info
    assert "programme-suggestion assistant" in rec and "rank_programs" in rec and "Do NOT state fees" in rec and "card" in rec
    for p in (info, rec):
        assert "demo" not in p.lower() and "signed in" not in p.lower() and "get_my_applications" not in p and "get_application_status" not in p
    assert "no access to applicant records" in info


# ---------------------------------------------------------------- recommendation logic (database names only)
def test_recommend_matches_interests_to_database_names_and_collapses_campuses():
    c = get_catalogue()
    r = c.recommend("I like business and management", limit=5)
    names = [x["programme"].name for x in r]
    assert {"Master of Business Administration (MBA)", "Bachelor of Business Administration (BBA)"} <= set(names) and len(names) == len(set(names))
    assert "Bachelor of Commerce (B.Com.)" not in names                                              # unrelated programmes are not suggested
    bba = next(x for x in r if "Bachelor of Business Administration" in x["programme"].name)
    assert bba["campuses"] == ["Bangalore Central Campus", "Bangalore Kengeri Campus"]            # one entry, both campuses listed
    assert all(x["programme"].level == "postgraduate" for x in c.recommend("business", level="postgraduate"))
    assert c.recommend("hello, I'm not sure") == [] and c.recommend("quantum zebra") == []
    assert c.recommend("computers and coding")[0]["programme"].name.startswith("Master of Computer")   # synonyms: coding -> computer science
    assert c.departments(3)


def test_detect_level_understands_how_applicants_talk():
    assert detect_level("I just finished 12th") is None and detect_level("what to study after 12th") == "undergraduate"
    assert detect_level("masters in psychology") == "postgraduate" and detect_level("I want a PhD") == "doctoral" and detect_level("hello") is None


def test_recommend_tool_excludes_what_was_already_suggested_and_drops_requirement_text_from_careers():
    first = execute("recommend_programmes", json.dumps({"interests": "business"}), CTX, "recommend").data["recommendations"]
    nxt = execute("recommend_programmes", json.dumps({"interests": "business", "exclude_names": [first[0]["name"]]}), CTX, "recommend").data["recommendations"]
    assert first[0]["name"] not in [r["name"] for r in nxt]
    from app.tools.registry import _NOT_A_CAREER
    assert _NOT_A_CAREER.search("Candidates who have completed Bachelors degree") and _NOT_A_CAREER.search("Passion for counselling service")
    assert not _NOT_A_CAREER.search("Career guidance counsellor") and not _NOT_A_CAREER.search("Corporate lawyers")


# ---------------------------------------------------------------- decider mode: the recommendation agent
async def test_recommender_first_gets_to_know_the_applicant():
    r = await run("recommend_programmes", H(("user", "I need help choosing a course")))
    assert r.agent == "recommend" and r.intent == "recommend_programmes" and r.cards == []
    assert "What subjects or fields" in r.reply and "undergraduate" in r.reply and any(f.startswith("I'm interested in ") for f in r.follow_ups)
    assert r.tools_used == ["recommend_programmes"] and "get_fees" not in r.tools_used


async def test_recommender_returns_cards_from_the_answer_and_is_sticky():
    hist = H(("user", "help me choose"), ("assistant", "What do you enjoy?"), ("user", "I like business and finance"))
    r = await run("off_topic", hist, last_agent="recommend")                       # the decider cannot classify a bare answer: stickiness does
    assert r.intent == "recommend_programmes" and "Programmes that may suit you" in r.reply and "not admission advice" in r.reply
    assert "Master of Business Administration (MBA)" in card_names(r)
    card = next(c for c in r.cards if c["name"] == "Master of Business Administration (MBA)")
    assert card["programme_id"] == 334 and card["level"] == "postgraduate" and card["campuses"] == ["Bangalore Central Campus"]
    assert isinstance(card["career_paths"], list) and card["matched_interests"]
    top3 = [c for c in r.cards[:3] if c["programme_id"] == 334]                  # careers are looked up for the first three cards only
    assert all(c["career_paths"] == ["Business Analyst"] for c in top3)
    assert r.follow_ups[0].startswith("Tell me about") and not {"get_fees", "get_fee_structure", "get_eligibility_criteria"} & set(r.tools_used)
    assert (await run("off_topic", hist, last_agent="info")).intent == "off_topic"


async def test_a_fact_question_leaves_the_recommender():
    hist = H(("user", "business"), ("assistant", "Here are options"), ("user", "what are the fees for the MBA?"))
    r = await run("programme_fees", hist, last_agent="recommend")
    assert r.intent == "programme_fees" and r.agent == "info" and "not connected" in r.reply and "5,70,000" not in r.reply and r.cards == []


async def test_show_more_gives_new_programmes():
    first = await run("recommend_programmes", H(("user", "I like business")))
    hist = H(("user", "I like business"), ("assistant", ", ".join(card_names(first))), ("user", "show me more options"))
    more = await run("recommend_programmes", hist, last_agent="recommend")
    assert card_names(first) and not set(card_names(first)) & set(card_names(more))


async def test_nothing_matches_falls_back_to_questions_not_inventions():
    r = await run("recommend_programmes", H(("user", "I want to be a zebra astronaut")))
    assert "What subjects or fields" in r.reply and "Programmes that may suit you" not in r.reply and r.cards == []


# ---------------------------------------------------------------- tools mode: separate prompt, tools and model per agent
def test_tools_mode_routes_to_the_recommender_with_its_own_prompt_tools_and_cards(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([call("recommend_programmes", interests="business"), text("Try the MBA.")])
    asked = []
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: (asked.append(agent), fake)[1])
    j = chat(client, "I'm interested in business, what should I study?").json()
    assert j["agent"] == "recommend" and asked == ["recommend"] and j["tools_used"] == ["recommend_programmes"]
    assert any(c["name"] == "Master of Business Administration (MBA)" for c in j["cards"])      # the UI shows the tool's suggestions as cards
    system = fake.seen[0][0][0].content
    assert "programme-suggestion assistant" in system and "INFORMATION agent" not in system
    assert {t.name for t in fake.seen[0][1]} == {t.name for t in available_tools(CTX, "recommend", "recommend")} and j["skill"] == "recommend"


def test_tools_mode_info_agent_gets_the_fact_tools_and_last_agent_is_honoured(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([text("ok")])
    asked = []
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: (asked.append(agent), fake)[1])
    j = chat(client, "What are the fees for the MBA?", last_agent="recommend").json()
    assert j["agent"] == "info" and asked == ["info"] and j["cards"] == []
    names = {t.name for t in fake.seen[0][1]}
    assert "get_fees" in names and "recommend_programmes" not in names and j["skill"] == "fees_deadlines"
    fake2 = FakeProvider([text("ok")])
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: fake2)
    assert chat(client, "undergraduate please", last_agent="recommend").json()["agent"] == "recommend"      # sticky
    assert chat(client, "hi", last_agent="bogus").status_code == 422


def test_recommender_cannot_call_an_info_tool_even_if_the_model_tries(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([call("get_fees", programme_id=334), text("Sorry, I can't quote fees.")])
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: fake)
    chat(client, "suggest a programme for me")
    tool_msg = next(m for m in fake.seen[1][0] if m.role == "tool")
    assert "tool_unavailable" in tool_msg.content and "5,70,000" not in tool_msg.content


def test_each_agent_can_use_its_own_model(monkeypatch):
    from app.config import llm_config
    monkeypatch.setenv("LLM_MODEL", "base/model")
    assert llm_config("recommend").model == "base/model" and llm_config().model == "base/model"
    monkeypatch.setenv("LLM_MODEL_RECOMMEND", "strong/chat-model")
    assert llm_config("recommend").model == "strong/chat-model" and llm_config("info").model == "base/model" and llm_config().model == "base/model"
    monkeypatch.setenv("LLM_MODEL_INFO", "cheap/chat-model")
    assert llm_config("info").model == "cheap/chat-model"


# ---------------------------------------------------------------- the opening message
def test_welcome_is_the_product_greeting_with_three_starting_points(client):
    j = client.get("/api/welcome").json()
    assert j["reply"] == "Hello! Welcome to CHRIST University. How can I help you? Which course would you like to enrol in at our college?"
    assert j["agent"] == "recommend" and set(j) == {"reply", "follow_ups", "agent"}               # no accounts, no applications, no demo flags
    assert j["follow_ups"] == ["How many programmes are there?", "Tell me about B.Tech", "How do I apply?"]       # at most three, not a department menu


def test_welcome_works_when_the_database_is_down(client):
    from app.catalogue import reset_catalogue
    from app.sources import set_source
    from tests.conftest import FakeSource
    set_source(FakeSource(down=True))
    reset_catalogue()
    j = client.get("/api/welcome").json()
    assert j["reply"].startswith("Hello! Welcome to CHRIST University.") and len(j["follow_ups"]) == 3


def test_decisions_mode_chat_reports_the_agent_and_cards(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider(ans("recommend_programmes")))
    j = chat(client, "I like business").json()
    assert j["agent"] == "recommend" and "Programmes that may suit you" in j["reply"] and j["cards"]
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider(ans("programme_fees")))
    j2 = chat(client, "mba fees", last_agent="recommend").json()
    assert j2["agent"] == "info" and j2["cards"] == []

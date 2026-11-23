import json

from tests.conftest import FakeProvider, call, text


def chat(client, msg="hi", **extra):
    return client.post("/api/chat", json={"messages": [{"role": "user", "content": msg}], **extra})


class FakeDecider:
    model = "fake"

    def __init__(self, intent):
        self.intent = intent

    async def decide(self, state, questions):
        return {k: {"type": "choice", "choice": v, "confidence": 0.95} for k, v in {"intent": self.intent, "level": "any", "category": "unspecified"}.items()}


# ---- configuration / errors
def test_chat_without_key_returns_clear_503_in_both_modes(client, monkeypatch):
    for mode in ("tools", "decisions"):
        monkeypatch.setenv("AGENT_MODE", mode)
        r = chat(client, "tell me about the mba")          # a greeting is answered in code, so ask something that needs the model
        assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_empty_and_invalid_messages_rejected(client):
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": ""}]}).status_code == 422
    assert client.post("/api/chat", json={"messages": []}).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422


def test_meta_has_data_info_and_no_secret(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")
    j = client.get("/api/meta").json()
    assert "super-secret-key" not in json.dumps(j) and j["llm"]["configured"] is True
    assert j["data"]["ready"] is True and j["data"]["programmes"] == 7 and j["data"]["source"] == "CHRIST programme database" and "CHRIST" in j["university"] and "unofficial" in j["product"]


def test_meta_and_pages_report_a_database_outage(client):
    from app.catalogue import reset_catalogue
    from app.sources import set_source
    from tests.conftest import FakeSource
    set_source(FakeSource(down=True))
    reset_catalogue()
    assert client.get("/api/meta").json()["data"]["ready"] is False
    for path in ("/api/programmes", "/api/programmes/334", "/api/dates", "/api/admission-process", "/api/campuses"):
        r = client.get(path)
        assert r.status_code == 503 and r.json()["error"]["code"] == "data_unavailable", path


# ---- no accounts, no demo data
def test_account_and_application_endpoints_are_gone(client):
    for method, path in [("get", "/api/session"), ("post", "/api/session/demo-login"), ("post", "/api/session/logout"), ("get", "/api/applications")]:
        r = getattr(client, method)(path)
        assert r.status_code in (404, 405), path
    j = client.get("/api/meta").json()
    assert "demo_login" not in j and "mode" not in j


# ---- chat end-to-end
def test_decisions_mode_chat_returns_grounded_reply_with_links(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider("programme_fees"))
    j = chat(client, "mba fees").json()
    assert "not connected" in j["reply"] and "5,70,000" not in j["reply"] and j["intent"] == "programme_fees" and "demo" not in j and "cards" in j
    assert j["sources"][0]["url"] is None and "CHRIST programme database" in j["sources"][0]["title"] and j["follow_ups"]


def test_tools_mode_runs_tool_loop(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([call("get_fees", programme_id=334), text("Fees are not available right now.")])
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: fake)
    j = chat(client, "MBA fees?").json()
    assert j["tools_used"] == ["get_fees"] and "CHRIST programme database" in j["sources"][0]["title"] and j["skill"] == "fees_deadlines"
    assert any(m.role == "tool" and '"available": false' in m.content and "5,70,000" not in m.content for m in fake.seen[1][0])
    assert "CHRIST" in fake.seen[0][0][0].content and "ONLY about" in fake.seen[0][0][0].content


def test_personal_application_questions_never_reach_the_model(client, monkeypatch):
    """The pre-router sends 'my application' cases to a human in code: the model is not called and no tool can read an application."""
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([text("should never be used")])
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda agent=None: fake)
    j = chat(client, "status of my application?").json()
    assert fake.seen == [] and j["skill"] == "escalate" and "can't see" in j["reply"] and j["intent"] == "escalate"


def test_provider_errors_surface_cleanly(client, monkeypatch):
    from app.llm import LLMError

    class Boom:
        model = "x"

        async def decide(self, *a, **k):
            raise LLMError("rate_limited", "slow down")

    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: Boom())
    r = chat(client, "tell me about the mba")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


# ---- data pages (all read from the database)
def test_programme_list_filters_and_pagination(client):
    j = client.get("/api/programmes").json()
    assert j["total"] == 7 and "Bangalore Kengeri Campus" in j["campuses"] and j["source"] == "CHRIST programme database"
    assert client.get("/api/programmes?level=doctoral").json()["total"] == 1
    assert client.get("/api/programmes?q=commerce").json()["programmes"][0]["id"] == 201
    assert len(client.get("/api/programmes?limit=2&offset=2").json()["programmes"]) == 2


def test_programme_detail_and_not_found(client):
    p = client.get("/api/programmes/334").json()["programme"]
    assert p["fees"] is None and "live admissions data" in p["fees_note"] and "approved_intake" not in p and "50% marks" in p["eligibility"] and p["duration_years"] == 2 and p["department"] == "Business and Management"
    titles = [t["title"] for t in p["tabs"]]
    assert "Curriculum" in titles and "Career paths" in titles and "Placements" in titles and "url" not in p
    assert "Managerial Economics" in next(t for t in p["tabs"] if t["title"] == "Curriculum")["text"]
    assert not any(t["title"].lower().startswith(("eligib", "fee")) for t in p["tabs"])  # shown as their own sections
    assert client.get("/api/programmes/99999").status_code == 404
    assert client.get("/api/programmes/zzz").status_code == 422


def test_dates_admission_process_campuses_and_eligibility_endpoints(client):
    d = client.get("/api/dates").json()
    assert d["windows"] == [] and "live admissions data" in d["note"]
    ap = client.get("/api/admission-process").json()["rows"]
    assert {r["group"] for r in ap} == {"ug comm", "pg commer"} and "apply online" in ap[0]["how_to_apply"]
    assert [c["name"] for c in client.get("/api/campuses").json()["campuses"]] == ["Bangalore Central Campus", "Bangalore Kengeri Campus"]
    r = client.post("/api/eligibility", json={"programme_id": 201, "percentage": 40}).json()
    assert r["result"]["outcome"] == "may_not_meet" and "not an admission" in r["result"]["disclaimer"] and "50% marks" in r["eligibility"]
    assert client.post("/api/eligibility", json={"programme_id": 201, "percentage": 140}).status_code == 422
    assert client.post("/api/eligibility", json={"programme_id": 99999}).status_code == 404


def test_scraped_topic_endpoint_is_gone(client):
    assert client.get("/api/topic/hostel").status_code == 404


def test_unknown_api_path_is_json_404_not_spa(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_long_assistant_history_is_accepted_but_long_user_messages_are_not(client, monkeypatch):
    """Regression: a long fee answer echoed back as history used to make the NEXT question fail with HTTP 422."""
    monkeypatch.setenv("AGENT_MODE", "decisions")
    seen = {}

    class Spy(FakeDecider):
        async def decide(self, state, questions):
            seen["state"] = state
            return await super().decide(state, questions)

    monkeypatch.setattr("app.routers.api.get_decider", lambda: Spy("greeting"))
    long_answer = "fee table row | 5,70,000 INR |\n" * 400            # ~13 000 characters
    ok = client.post("/api/chat", json={"messages": [{"role": "user", "content": "mba fees"}, {"role": "assistant", "content": long_answer},
                                                       {"role": "user", "content": "and the eligibility?"}]})
    assert ok.status_code == 200 and "and the eligibility?" in seen["state"][-1]
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "x" * 4001}]}).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "y" * 20001}, {"role": "user", "content": "q"}]}).status_code == 422

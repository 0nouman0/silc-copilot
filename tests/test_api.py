import json

from app import auth
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
        r = chat(client)
        assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_empty_and_invalid_messages_rejected(client):
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": ""}]}).status_code == 422
    assert client.post("/api/chat", json={"messages": []}).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422


def test_meta_has_data_info_and_no_secret(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")
    j = client.get("/api/meta").json()
    assert "super-secret-key" not in json.dumps(j) and j["llm"]["configured"] is True
    assert j["data"]["ready"] is True and j["data"]["programmes"] == 6 and "CHRIST" in j["university"] and "unofficial" in j["product"]


def test_meta_reports_missing_data(client, monkeypatch, tmp_path):
    from app.christ.store import reset_corpus
    monkeypatch.setenv("CHRIST_DATA_DIR", str(tmp_path / "x"))
    reset_corpus()
    assert client.get("/api/meta").json()["data"]["ready"] is False
    assert client.get("/api/programmes").status_code == 503


# ---- auth + applications
def test_applications_require_authentication(client):
    assert client.get("/api/applications").status_code == 401


def test_user_with_three_applications(client, login):
    login("demo-applicant-1")
    j = client.get("/api/applications").json()
    assert j["count"] == 3 and j["demo"] is True


def test_forged_and_tampered_cookies_rejected(client):
    client.cookies.set(auth.COOKIE, "demo-applicant-1")
    assert client.get("/api/applications").status_code == 401
    client.cookies.set(auth.COOKIE, "demo-applicant-1.deadbeef")
    assert client.get("/api/applications").status_code == 401


def test_unknown_demo_user_and_production_login_rejected(client, monkeypatch):
    assert client.post("/api/session/demo-login", json={"user_id": "admin"}).status_code == 403
    monkeypatch.setenv("INTEGRATION_MODE", "production")
    monkeypatch.setenv("SESSION_SECRET", "s")
    assert client.post("/api/session/demo-login", json={"user_id": "demo-applicant-1"}).status_code == 403


# ---- chat end-to-end
def test_decisions_mode_chat_returns_grounded_reply_with_links(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider("programme_fees"))
    j = chat(client, "mba fees").json()
    assert "5,70,000" in j["reply"] and j["intent"] == "programme_fees" and j["demo"] is False
    assert j["sources"][0]["url"].endswith("/courses/334") and j["follow_ups"]


def test_tools_mode_runs_tool_loop(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([call("get_fee_structure", programme_id="334"), text("Fees are in the table.")])
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda: fake)
    j = chat(client, "MBA fees?").json()
    assert j["tools_used"] == ["get_fee_structure"] and j["sources"][0]["url"].endswith("/courses/334")
    assert any(m.role == "tool" and "5,70,000" in m.content for m in fake.seen[1][0])
    assert "CHRIST" in fake.seen[0][0][0].content and "ONLY about" in fake.seen[0][0][0].content


def test_tools_mode_anonymous_not_offered_applicant_tools(client, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "tools")
    fake = FakeProvider([text("Please sign in.")])
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda: fake)
    chat(client, "status of my application?")
    assert "get_application_status" not in {t.name for t in fake.seen[0][1]}


def test_active_application_verified_and_cross_user_ignored(client, login, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: FakeDecider("my_application_status"))
    login("demo-applicant-1")
    ok = chat(client, "status?", active_application_id="DEMO-APP-0001").json()
    assert ok["active_application_id"] == "DEMO-APP-0001" and "Under Review" in ok["reply"]
    login("demo-applicant-2")
    bad = chat(client, "status?", active_application_id="DEMO-APP-0001").json()
    assert bad["active_application_id"] is None and "Under Review" not in bad["reply"]


def test_provider_errors_surface_cleanly(client, monkeypatch):
    from app.llm import LLMError

    class Boom:
        model = "x"

        async def decide(self, *a, **k):
            raise LLMError("rate_limited", "slow down")

    monkeypatch.setenv("AGENT_MODE", "decisions")
    monkeypatch.setattr("app.routers.api.get_decider", lambda: Boom())
    r = chat(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


# ---- data pages
def test_programme_list_filters_and_pagination(client):
    j = client.get("/api/programmes").json()
    assert j["total"] == 6 and "Bangalore Kengeri Campus" in j["campuses"]
    assert client.get("/api/programmes?level=doctoral").json()["total"] == 1
    assert client.get("/api/programmes?q=commerce").json()["programmes"][0]["id"] == "201"
    assert len(client.get("/api/programmes?limit=2&offset=2").json()["programmes"]) == 2


def test_programme_detail_and_not_found(client):
    p = client.get("/api/programmes/334").json()["programme"]
    assert "5,70,000" in p["fees"] and p["tabs"][0]["title"] == "Selection Process" and p["url"].endswith("/courses/334")
    assert client.get("/api/programmes/zzz").status_code == 404


def test_dates_topics_and_eligibility_endpoints(client):
    assert client.get("/api/dates").json()["windows"]
    t = client.get("/api/topic/hostel").json()["results"]
    assert t and t[0]["url"].endswith("/hostel")
    assert client.get("/api/topic/unknown").status_code == 404
    r = client.post("/api/eligibility", json={"programme_id": "201", "percentage": 40}).json()
    assert r["result"]["outcome"] == "may_not_meet" and "not an admission" in r["result"]["disclaimer"] and "50% marks" in r["eligibility"]
    assert client.post("/api/eligibility", json={"programme_id": "201", "percentage": 140}).status_code == 422
    assert client.post("/api/eligibility", json={"programme_id": "nope"}).status_code == 404


def test_unknown_api_path_is_json_404_not_spa(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_apply_topic_returns_guidance_and_the_shared_apply_link(client):
    d = client.get("/api/topic/apply").json()
    assert d["apply_url"] == "https://espro.christuniversity.in/Application/" and d["results"]
    assert "apply_url" not in client.get("/api/topic/hostel").json()

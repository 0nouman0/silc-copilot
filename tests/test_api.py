import json

from app import auth
from tests.conftest import FakeProvider, call, text


def patch_llm(monkeypatch, script):
    fake = FakeProvider(script)
    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda: fake)
    return fake


def chat(client, msg="hi", **extra):
    return client.post("/api/chat", json={"messages": [{"role": "user", "content": msg}], **extra})


# ---- configuration / errors
def test_chat_without_key_returns_clear_503(client):
    r = chat(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_empty_and_invalid_messages_rejected(client):
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": ""}]}).status_code == 422
    assert client.post("/api/chat", json={"messages": []}).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422


def test_meta_exposes_no_secret(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret")
    j = client.get("/api/meta").json()
    assert "super-secret" not in json.dumps(j) and j["llm"]["configured"] is True


# ---- auth + applications
def test_applications_require_authentication(client):
    assert client.get("/api/applications").status_code == 401


def test_user_with_three_applications(client, login):
    login("demo-applicant-1")
    j = client.get("/api/applications").json()
    assert j["count"] == 3 and j["demo"] is True
    assert {a["status"] for a in j["applications"]} == {"Under Review", "Application Submitted", "Shortlisted"}


def test_forged_and_tampered_cookies_rejected(client):
    client.cookies.set(auth.COOKIE, "demo-applicant-1")
    assert client.get("/api/applications").status_code == 401
    client.cookies.set(auth.COOKIE, "demo-applicant-1.deadbeef")
    assert client.get("/api/applications").status_code == 401


def test_unknown_demo_user_rejected(client):
    assert client.post("/api/session/demo-login", json={"user_id": "admin"}).status_code == 403


def test_demo_login_disabled_in_production(client, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "production")
    monkeypatch.setenv("SESSION_SECRET", "s")
    assert client.post("/api/session/demo-login", json={"user_id": "demo-applicant-1"}).status_code == 403
    assert client.get("/api/applications").status_code == 401


# ---- chat + tools end-to-end (with scripted LLM)
def test_chat_runs_tool_loop_and_returns_sources(client, monkeypatch):
    fake = patch_llm(monkeypatch, [call("get_fee_structure", programme_id="mba"), text("MBA tuition is shown (demo).")])
    j = chat(client, "MBA fees?").json()
    assert j["reply"].startswith("MBA tuition") and j["tools_used"] == ["get_fee_structure"]
    assert j["sources"][0]["title"].startswith("Fee Structure") and j["sources"][0]["demo"] is True
    assert j["demo"] is True and j["follow_ups"]
    # second LLM turn received the tool result
    assert any(m.role == "tool" for m in fake.seen[1][0])


def test_anonymous_llm_not_offered_applicant_tools(client, monkeypatch):
    fake = patch_llm(monkeypatch, [text("Please sign in.")])
    chat(client, "status of my application?")
    names = {t.name for t in fake.seen[0][1]}
    assert "get_application_status" not in names
    assert "NOT signed in" in fake.seen[0][0][0].content


def test_active_application_verified_and_injected(client, login, monkeypatch):
    login("demo-applicant-1")
    fake = patch_llm(monkeypatch, [call("get_application_status"), text("Your application is Under Review.")])
    j = chat(client, "what's the status?", active_application_id="DEMO-APP-0001").json()
    assert j["active_application_id"] == "DEMO-APP-0001"
    sys_prompt = fake.seen[0][0][0].content
    assert "DEMO-APP-0001" in sys_prompt and "programme_id=mba" in sys_prompt
    tool_msg = next(m for m in fake.seen[1][0] if m.role == "tool")
    assert "Under Review" in tool_msg.content


def test_cross_user_active_application_is_ignored(client, login, monkeypatch):
    login("demo-applicant-2")  # does NOT own DEMO-APP-0001
    fake = patch_llm(monkeypatch, [text("ok")])
    j = chat(client, "status?", active_application_id="DEMO-APP-0001").json()
    assert j["active_application_id"] is None
    assert "DEMO-APP-0001" not in fake.seen[0][0][0].content


def test_provider_errors_surface_cleanly(client, monkeypatch):
    from app.llm import LLMError

    class Boom:
        name = model = "x"

        async def complete(self, *a, **k):
            raise LLMError("rate_limited", "slow down")

    monkeypatch.setattr("app.routers.api.get_llm_provider", lambda: Boom())
    r = chat(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_runaway_tool_loop_is_bounded(client, monkeypatch):
    patch_llm(monkeypatch, [call("get_contact_information")] * 10)
    j = chat(client).json()
    assert "Admissions Office" in j["reply"]


# ---- data pages / eligibility endpoint
def test_data_endpoints_and_labels(client):
    assert client.get("/api/programmes").json()["demo"] is True
    assert client.get("/api/programmes/mba").status_code == 200
    assert client.get("/api/programmes/zzz").status_code == 404
    assert client.get("/api/contacts").json()["contact"]["email"] is None


def test_eligibility_endpoint_has_disclaimer(client):
    r = client.post("/api/eligibility", json={"programme_id": "mba", "highest_qualification": "bachelor",
                                             "percentage": 65, "entrance_taken": True, "entrance_score": 55}).json()
    assert r["result"]["outcome"] == "appears_to_meet" and "not an admission" in r["result"]["disclaimer"]
    bad = client.post("/api/eligibility", json={"programme_id": "mba", "percentage": 140})
    assert bad.status_code == 422


def test_unknown_api_path_is_json_404_not_spa(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")

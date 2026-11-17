import json

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMResponse, ToolCall
from app.main import app


class FakeProvider:
    """Scripted LLM: yields queued responses; records the messages it was given."""
    name, model = "fake", "fake-model"

    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def complete(self, messages, tools=None, temperature=0.2):
        self.seen.append((messages, tools))
        return self.script.pop(0)


def call(name, **args):
    return LLMResponse(None, [ToolCall(id=f"c-{name}", name=name, arguments=json.dumps(args))])


def text(t):
    return LLMResponse(t, [])


@pytest.fixture(autouse=True)
def demo_env(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "demo")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    # isolate tests from the developer's real .env (loaded at import time)
    for k in ("LLM_MODEL", "AGENT_MODE", "LLM_PROVIDER", "LLM_BASE_URL", "LLM_DECISIONS_URL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def login(client):
    def _login(uid):
        r = client.post("/api/session/demo-login", json={"user_id": uid})
        assert r.status_code == 200
        return client
    return _login

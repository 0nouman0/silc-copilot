"""The natural-language composer may only reword verified answers; anything ungrounded falls back to the template."""
from app.agent.composer import Composer, grounded, should_compose
from app.agent.decision_engine import run_decision_agent
from app.config import compose_models
from app.llm.base import LLMError, LLMResponse
from tests.test_decision_engine import FakeDecider, H, ans

FACTS = "Eligibility: 50 % aggregate marks. Apply at https://christuniversity.in/apply by 5 May 2027. Fee 5,70,000 INR."


class FakeProv:
    def __init__(self, out=None, err=None):
        self.out, self.err, self.calls = out, err, 0
        self.model = "fake"

    async def complete(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        if self.err:
            raise LLMError(self.err, "x")
        return LLMResponse(self.out, [])


def test_grounded_accepts_same_numbers_and_links():
    assert grounded("You need 50 % aggregate. Apply at https://christuniversity.in/apply. Fee is 5,70,000 INR (2027).", FACTS, "q")


def test_grounded_rejects_invented_numbers_and_links():
    assert not grounded("You need 60 % aggregate.", FACTS, "q")
    assert not grounded("Fee is 4,00,000 INR.", FACTS, "q")
    assert not grounded("See https://evil.example/x", FACTS, "q")


def test_should_compose_skips_sensitive_tables_and_choices():
    assert should_compose("programme_details", "plain text", False)
    assert not should_compose("application_status", "x", False)
    assert not should_compose("guarantee_request", "x", False)
    assert not should_compose("programme_fees", "| a | b |\n|---|---|", False)
    assert not should_compose("programme_fees", "intro\n| a | b |", False)
    assert not should_compose("programme_details", "which one?", True)
    assert not should_compose("programme_details", "x" * 7000, False)


async def test_compose_returns_model_text_when_grounded():
    good = "Good news! You need 50 % aggregate marks, and you can apply at https://christuniversity.in/apply."
    assert await Composer([FakeProv(good)]).compose("who can apply", [{"role": "user", "content": "q"}], FACTS, "eligibility_criteria") == good


async def test_compose_falls_back_on_hallucination_error_or_empty():
    h = [{"role": "user", "content": "q"}]
    assert await Composer([FakeProv("You need 75 % aggregate marks, which is easy.")]).compose("q", h, FACTS, "x") is None
    assert await Composer([FakeProv(err="rate_limited")]).compose("q", h, FACTS, "x") is None
    assert await Composer([FakeProv("")]).compose("q", h, FACTS, "x") is None
    assert await Composer([FakeProv("short")]).compose("q", h, FACTS, "x") is None


async def test_compose_tries_next_model_but_stops_on_bad_key():
    h = [{"role": "user", "content": "q"}]
    ok = "You need 50 % aggregate marks to be considered for the programme."
    a, b = FakeProv(err="unavailable"), FakeProv(ok)
    assert await Composer([a, b]).compose("q", h, FACTS, "x") == ok and a.calls == 1 and b.calls == 1
    a, b = FakeProv(err="not_configured"), FakeProv(ok)
    assert await Composer([a, b]).compose("q", h, FACTS, "x") is None and b.calls == 0


async def test_engine_uses_composer_but_keeps_sources_and_falls_back():
    hist = H(("user", "who can apply for the mba"))
    plain = await run_decision_agent(FakeDecider(ans("eligibility_criteria")), hist)
    nums = " ".join(sorted(set(__import__("re").findall(r"\d[\d,.]*\d|\d", plain.reply))))
    nice = f"Sure! Here is what CHRIST publishes ({nums})."
    r = await run_decision_agent(FakeDecider(ans("eligibility_criteria")), hist,
                                 composer=Composer([FakeProv(nice)]))
    assert r.reply == nice and r.sources == plain.sources and r.intent == plain.intent
    bad = await run_decision_agent(FakeDecider(ans("eligibility_criteria")), hist,
                                   composer=Composer([FakeProv("It costs 999999 rupees for everybody always.")]))
    assert bad.reply == plain.reply


async def test_sensitive_intent_never_reaches_the_model():
    p = FakeProv("anything goes here, honestly, 100% guaranteed")
    r = await run_decision_agent(FakeDecider(ans("guarantee_request")), H(("user", "guarantee me a seat")), composer=Composer([p]))
    assert p.calls == 0 and "100% guaranteed" not in r.reply


def test_compose_models_config(monkeypatch):
    assert compose_models()[0].startswith("nvidia/")
    monkeypatch.setenv("COMPOSE_MODELS", "a/b:free, c/d")
    assert compose_models() == ["a/b:free", "c/d"]
    monkeypatch.setenv("COMPOSE", "off")
    assert compose_models() == []


def test_list_numbering_is_not_treated_as_a_fact():
    assert grounded("Steps:\n1. Apply online\n2. Pay the fee of 5,70,000 INR", FACTS, "q")
    assert not grounded("Steps:\n1. Apply online\n2. Pay 7 INR", FACTS, "q")

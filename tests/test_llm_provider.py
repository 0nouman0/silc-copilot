import json

import httpx
import pytest

from app.llm import LLMError, Message, ToolDef, get_llm_provider
from app.llm.openai_compatible import OpenAICompatibleProvider


def provider(handler):
    return OpenAICompatibleProvider(name="openrouter", api_key="k", model="m", base_url="https://x/api/v1",
                                    timeout_s=5, transport=httpx.MockTransport(handler))


TOOLS = [ToolDef("t", "d", {"type": "object", "properties": {}})]


async def test_parses_tool_calls_and_sends_openai_format():
    seen = {}

    def h(req: httpx.Request):
        seen["body"], seen["auth"] = json.loads(req.content), req.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "t", "arguments": "{\"a\":1}"}}]}}]})

    r = await provider(h).complete([Message("user", "hi")], TOOLS)
    assert r.tool_calls[0].name == "t" and r.tool_calls[0].arguments == '{"a":1}'
    assert seen["auth"] == "Bearer k" and seen["body"]["tools"][0]["function"]["name"] == "t"
    assert seen["body"]["tool_choice"] == "auto"


@pytest.mark.parametrize("status,code", [(401, "not_configured"), (429, "rate_limited"), (500, "unavailable")])
async def test_http_errors_map_to_codes(status, code):
    with pytest.raises(LLMError) as e:
        await provider(lambda r: httpx.Response(status, text="boom")).complete([Message("user", "hi")])
    assert e.value.code == code


async def test_openrouter_200_with_error_body():
    with pytest.raises(LLMError) as e:
        await provider(lambda r: httpx.Response(200, json={"error": {"message": "x"}})).complete([Message("user", "hi")])
    assert e.value.code == "unavailable"


async def test_model_without_tool_support():
    with pytest.raises(LLMError) as e:
        await provider(lambda r: httpx.Response(404, text="No endpoints found that support tool use")).complete(
            [Message("user", "hi")], TOOLS)
    assert e.value.code == "tools_unsupported"


async def test_timeout_and_network_errors():
    def boom(r):
        raise httpx.ConnectTimeout("t")
    with pytest.raises(LLMError) as e:
        await provider(boom).complete([Message("user", "hi")])
    assert e.value.code == "timeout"


def test_missing_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMError) as e:
        get_llm_provider()
    assert e.value.code == "not_configured"


def test_provider_switch_is_config_only(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    p = get_llm_provider()
    assert p.name == "openai" and p.model == "gpt-4o-mini"
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    assert get_llm_provider().name == "openrouter"

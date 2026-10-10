"""ChatUnifier sends a call with no key of the install's own to the gateway
route, and never asks the gateway when there is one."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
import structlog

from services.gateway.routing import GatewayRoute
from services.llm import registry
from services.llm.protocol import LLMError, LLMErrorCategory, LLMResponse, Message
from services.llm.registry import ProviderSpec
from services.llm.unifier import ChatUnifier
from services.plugin import NodeUserError

ROUTE = GatewayRoute(api_key="sk-gateway", base_url="https://gw.example/v1", model_prefix="groq/")
HI = [Message(role="user", content="hi")]


class _Router:
    def __init__(self, route):
        self._route = route
        self.asked = []

    async def route(self, provider):
        self.asked.append(provider)
        return self._route


def _provider(monkeypatch, name, *, chat=None):
    """A fake provider in the registry for this test only (monkeypatch removes it)."""
    created = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = chat or AsyncMock(return_value=LLMResponse(content="ok"))
            created.append(self)

    monkeypatch.setitem(
        registry._REGISTRY, name, ProviderSpec(name=name, factory=Client, sdk_exception_refs=("openai:OpenAIError",))
    )
    return created


def _unifier(router):
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value=None)
    return ChatUnifier(defaults={"providers": {}}, auth_service=auth, gateway=router), auth


async def test_a_call_with_no_own_key_goes_to_the_gateway(monkeypatch):
    created = _provider(monkeypatch, "gateway-route-test")
    router = _Router(ROUTE)
    unifier, auth = _unifier(router)

    with structlog.contextvars.bound_contextvars(chat_run_id="r_1", workflow_id="wf"):
        await unifier.chat(provider="gateway-route-test", api_key="", messages=HI, model="llama-3.3-70b")

    client = created[0]
    assert client.kwargs["api_key"] == "sk-gateway"
    assert client.kwargs["proxy_url"] == "https://gw.example/v1"
    assert client.kwargs["model_prefix"] == "groq/"
    sent = client.chat.call_args.kwargs
    # The provider adds the prefix on the wire; capability lookups keep bare ids.
    assert sent["model"] == "llama-3.3-70b"
    assert sent["extra_headers"]["x-litellm-session-id"] == "r_1"
    # Outside a Temporal activity there is nothing to replay.
    assert "extra_body" not in sent
    # A gateway call never reads a saved {provider}_proxy base URL.
    auth.resolve_api_key.assert_not_awaited()


async def test_an_own_key_is_used_as_before_and_the_gateway_is_not_asked(monkeypatch):
    created = _provider(monkeypatch, "gateway-own-key-test")
    router = _Router(ROUTE)
    unifier, _ = _unifier(router)

    await unifier.chat(provider="gateway-own-key-test", api_key="sk-own", messages=HI, model="m")

    assert router.asked == []
    assert created[0].kwargs["api_key"] == "sk-own"
    assert "model_prefix" not in created[0].kwargs
    assert "extra_headers" not in created[0].chat.call_args.kwargs


async def test_inside_an_activity_the_call_opts_into_replay(monkeypatch):
    created = _provider(monkeypatch, "gateway-replay-test")
    unifier, _ = _unifier(_Router(ROUTE))

    with structlog.contextvars.bound_contextvars(
        temporal_workflow_id="wf", temporal_run_id="run", temporal_activity_id="llm-step-1"
    ):
        await unifier.chat(provider="gateway-replay-test", api_key="", messages=HI, model="m")

    cache = created[0].chat.call_args.kwargs["extra_body"]["cache"]
    assert cache["use-cache"] is True and cache["namespace"].startswith("oc1-")


async def test_no_own_key_and_no_gateway_still_refuses(monkeypatch):
    _provider(monkeypatch, "gateway-none-test")
    unifier, _ = _unifier(_Router(None))
    with pytest.raises(NodeUserError, match="No API key is configured"):
        await unifier.chat(provider="gateway-none-test", api_key="", messages=HI, model="m")


async def test_a_gateway_failure_gets_gateway_wording(monkeypatch):
    body = {"message": "Budget has been exceeded!", "type": "budget_exceeded", "param": None, "code": "429"}
    response = httpx.Response(429, request=httpx.Request("POST", "https://gw.example/v1/chat/completions"), json={"error": body})
    refused = openai.RateLimitError("Budget has been exceeded!", response=response, body=body)
    _provider(monkeypatch, "gateway-failure-test", chat=AsyncMock(side_effect=refused))
    unifier, _ = _unifier(_Router(ROUTE))

    with pytest.raises(LLMError) as raised:
        await unifier.chat(
            provider="gateway-failure-test", api_key="", messages=HI, model="m", translate_errors=False
        )
    assert raised.value.category is LLMErrorCategory.BILLING
    assert raised.value.retryable is False
    assert raised.value.user_message == "Your OpenCompany allowance for this month is used up."


async def test_openai_provider_prefixes_the_wire_model_only():
    from services.llm.providers.openai import OpenAIProvider

    provider = OpenAIProvider("sk-gateway", proxy_url="https://gw.example/v1", model_prefix="openai/")
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="ok", reasoning_content=None, tool_calls=None), finish_reason="stop")]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2, completion_tokens_details=None)
    provider._client.chat.completions.create = AsyncMock(return_value=resp)

    result = await provider.chat(
        HI,
        model="gpt-4.1-mini",
        extra_headers={"x-litellm-session-id": "s"},
        extra_body={"cache": {"use-cache": True}},
    )

    sent = provider._client.chat.completions.create.call_args.kwargs
    assert sent["model"] == "openai/gpt-4.1-mini"
    assert sent["extra_headers"] == {"x-litellm-session-id": "s"}
    assert sent["extra_body"] == {"cache": {"use-cache": True}}
    assert result.model == "gpt-4.1-mini"

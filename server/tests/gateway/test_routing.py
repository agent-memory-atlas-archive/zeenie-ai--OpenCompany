"""services/gateway/routing.py: own keys first; the gateway serves only the
providers llm_defaults.json marks, and only when enabled and connected."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.gateway.routing import GATEWAY_CREDENTIAL_ID, GatewayRoute, GatewayRouter, gateway_covers

DEFAULTS = {
    "providers": {
        "openai": {"gateway": {"model_prefix": "openai/", "base_path": "/v1"}},
        "anthropic": {"gateway": {"model_prefix": "", "base_path": ""}},
        "deepseek": {"default_model": "deepseek-v4.1-flash"},
    }
}


def _router(*, enabled=True, distributed=False, key="sk-gateway", defaults=DEFAULTS):
    settings = SimpleNamespace(gateway_enabled=enabled, distributed_mode=distributed, gateway_url="https://gw.example/")
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=key)
    return GatewayRouter(settings=settings, auth_service=auth, defaults=defaults), auth


async def test_a_served_provider_gets_the_gateway_route():
    router, auth = _router()
    assert await router.route("openai") == GatewayRoute(
        api_key="sk-gateway", base_url="https://gw.example/v1", model_prefix="openai/"
    )
    auth.get_api_key.assert_awaited_with(GATEWAY_CREDENTIAL_ID)


async def test_native_routes_use_the_gateway_root_and_bare_ids():
    router, _ = _router()
    route = await router.route("anthropic")
    assert route is not None
    assert (route.base_url, route.model_prefix) == ("https://gw.example", "")


@pytest.mark.parametrize("provider", ["deepseek", "openai_compatible:lab", "unknown"])
async def test_a_provider_without_a_gateway_block_is_never_routed(provider):
    router, _ = _router()
    assert await router.route(provider) is None


async def test_no_route_without_a_stored_gateway_key():
    router, _ = _router(key=None)
    assert await router.route("openai") is None
    assert await router.covers("openai") is False


@pytest.mark.parametrize("flag", [False, MagicMock(), "true", 1])
async def test_only_a_true_flag_turns_the_gateway_on(flag):
    router, auth = _router(enabled=flag)
    assert await router.route("openai") is None
    auth.get_api_key.assert_not_awaited()


async def test_distributed_mode_turns_the_gateway_off():
    router, _ = _router(distributed=True)
    assert await router.route("openai") is None


def test_served_providers_come_from_the_gateway_blocks():
    router, _ = _router()
    assert router.served_providers() == ["openai", "anthropic"]


def test_llm_defaults_serves_exactly_the_priced_providers():
    """OpenRouter, DeepSeek and Sarvam stay off until their default models
    are priced upstream (gateway/tests/test_pins.py checks the prices)."""
    from services.llm.config import LLM_DEFAULTS

    router, _ = _router(defaults=LLM_DEFAULTS)
    assert set(router.served_providers()) == {
        "openai", "anthropic", "gemini", "groq", "cerebras", "xai", "kimi", "mistral",
    }


async def test_gateway_covers_trusts_only_a_real_router():
    # tests/conftest.py stubs core.container with a MagicMock, whose router
    # would claim to cover every provider.
    assert await gateway_covers("openai") is False


async def test_activity_key_lookup_returns_no_key_when_the_gateway_covers(monkeypatch):
    """The gateway key never reaches activity code or a Temporal payload:
    the activity passes no key and ChatUnifier routes the call."""
    from core.container import container
    from services.temporal import agent_activities

    auth = MagicMock(distributed_credentials=False)
    auth.resolve_api_key = AsyncMock(return_value=None)
    monkeypatch.setattr(container, "auth_service", MagicMock(return_value=auth))
    monkeypatch.setattr("services.gateway.routing.gateway_covers", AsyncMock(return_value=True))

    assert await agent_activities._resolve_activity_api_key({"provider": "openai", "user_id": "owner"}) == ""


async def test_activity_key_lookup_still_refuses_when_nothing_covers(monkeypatch):
    from temporalio.exceptions import ApplicationError

    from core.container import container
    from services.temporal import agent_activities

    auth = MagicMock(distributed_credentials=False)
    auth.resolve_api_key = AsyncMock(return_value=None)
    monkeypatch.setattr(container, "auth_service", MagicMock(return_value=auth))
    monkeypatch.setattr("services.gateway.routing.gateway_covers", AsyncMock(return_value=False))

    with pytest.raises(ApplicationError) as raised:
        await agent_activities._resolve_activity_api_key({"provider": "openai", "user_id": "owner"})
    assert raised.value.type == "MissingAgentProviderCredential"

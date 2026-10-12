"""The OpenCompany Gateway's Connectors card: the key probe, the allowance
line, and the card listed only while the gateway is enabled."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.gateway import credential as credential_module
from services.gateway import handlers as handlers_module
from services.gateway.allowance import parse_user_info

USER_INFO = {
    "user_id": "alice",
    "user_info": {
        "spend": 12.4,
        "max_budget": 50,
        "budget_reset_at": "2026-11-01T00:00:00Z",
        "user_email": "alice@example.com",
    },
}


def _response(status, body=None):
    return httpx.Response(status, json=body or {}, request=httpx.Request("GET", "https://gw.example/user/info"))


def test_the_allowance_reads_as_one_line():
    allowance = parse_user_info(USER_INFO)
    assert allowance.summary == "$12.40 of $50.00 used · resets Nov 1"
    assert allowance.account == "alice@example.com"
    assert parse_user_info({"user_info": {"spend": 3}}).summary == "$3.00 used"


async def test_the_probe_reads_the_allowance(monkeypatch):
    monkeypatch.setattr(credential_module, "user_info", AsyncMock(return_value=_response(200, USER_INFO)))
    result = await credential_module.OpenCompanyGatewayCredential._probe("sk-gateway")
    assert result.valid is True
    assert result.message == "Connected: $12.40 of $50.00 used · resets Nov 1."


async def test_the_probe_asks_for_a_default_key(monkeypatch):
    monkeypatch.setattr(credential_module, "user_info", AsyncMock(return_value=_response(403)))
    result = await credential_module.OpenCompanyGatewayCredential._probe("sk-llm-api")
    assert result.valid is False
    assert "type Default" in result.message


async def test_a_rejected_key_is_left_to_validate_to_word(monkeypatch):
    monkeypatch.setattr(credential_module, "user_info", AsyncMock(return_value=_response(401)))
    with pytest.raises(httpx.HTTPStatusError):
        await credential_module.OpenCompanyGatewayCredential._probe("sk-wrong")


async def test_the_card_offers_no_1password_source(monkeypatch):
    """The router reads the key on every LLM call, which a 1Password lookup
    can't serve: a bound key would show as connected and never be used."""
    from services.credential_registry import provider_connection_state
    from services.plugin.credential import CREDENTIAL_REGISTRY

    # A plain API-key credential would get the 1Password tab; this one opts out.
    assert CREDENTIAL_REGISTRY.get("opencompany_gateway") is credential_module.OpenCompanyGatewayCredential
    auth = MagicMock(distributed_credentials=False)
    auth.has_valid_key = AsyncMock(return_value=True)
    auth.get_oauth_tokens = AsyncMock(return_value=None)
    state = await provider_connection_state({"id": "opencompany_gateway", "kind": "apiKey"}, auth)
    assert state["sourceSupported"] is False


async def test_status_without_a_stored_key(monkeypatch):
    from core.container import container

    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    monkeypatch.setattr(container, "auth_service", MagicMock(return_value=auth))
    result = await handlers_module.handle_gateway_status({}, MagicMock())
    assert result == {"success": True, "connected": False}


async def test_status_shows_the_allowance(monkeypatch):
    from core.container import container

    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value="sk-gateway")
    monkeypatch.setattr(container, "auth_service", MagicMock(return_value=auth))
    monkeypatch.setattr(handlers_module, "user_info", AsyncMock(return_value=_response(200, USER_INFO)))
    result = await handlers_module.handle_gateway_status({}, MagicMock())
    assert result["connected"] is True
    assert result["summary"] == "$12.40 of $50.00 used · resets Nov 1"
    assert result["account_label"] == "alice@example.com"


async def test_status_survives_an_unreachable_gateway(monkeypatch):
    from core.container import container

    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value="sk-gateway")
    monkeypatch.setattr(container, "auth_service", MagicMock(return_value=auth))
    monkeypatch.setattr(handlers_module, "user_info", AsyncMock(side_effect=httpx.ConnectError("down")))
    result = await handlers_module.handle_gateway_status({}, MagicMock())
    assert result["connected"] is True
    assert "Couldn't read the allowance" in result["summary"]


def test_the_card_is_listed_only_while_the_gateway_is_enabled(monkeypatch):
    from core.container import container
    from services.credential_registry import CredentialRegistry

    monkeypatch.setattr(container, "settings", MagicMock(return_value=SimpleNamespace(gateway_enabled=True)))
    listed = {provider["id"]: provider for provider in CredentialRegistry().get_all_providers()}
    assert listed["opencompany_gateway"]["kind"] == "apiKey"
    assert listed["opencompany_gateway"]["consumer_category"] == "ai"
    assert listed["opencompany_gateway"]["ws"] == {"status": "gateway_status"}

    monkeypatch.setattr(container, "settings", MagicMock(return_value=SimpleNamespace(gateway_enabled=False)))
    assert "opencompany_gateway" not in {provider["id"] for provider in CredentialRegistry().get_all_providers()}
    # Still resolvable by id, for the connection state of a stored key.
    assert CredentialRegistry().get_provider("opencompany_gateway") is not None


def test_the_card_is_no_llm_provider():
    """No llm_defaults.json block, so it never shows up in model pickers."""
    from services.llm.config import PROVIDER_CONFIGS

    assert "opencompany_gateway" not in PROVIDER_CONFIGS

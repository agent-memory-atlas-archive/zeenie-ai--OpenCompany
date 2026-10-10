"""The RLM agent runs on the gateway's OpenAI-format routes with no own key
(services/rlm): its library reaches a custom URL only in OpenAI's format."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import structlog

from services.gateway.routing import GatewayRoute
from services.rlm.adapters import BackendAdapter, ChatModelExtractor
from services.rlm.service import RLMService

from .test_errors import _gateway_error

ROUTE = GatewayRoute(api_key="sk-gateway", base_url="https://gw.example/v1", model_prefix="anthropic/")


def _auth(key=None):
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value=key)
    return auth


def test_on_the_gateway_every_provider_uses_the_openai_backend():
    with structlog.contextvars.bound_contextvars(chat_run_id="run-1"):
        backend, kwargs = BackendAdapter.adapt("anthropic", "claude-x", None, route=ROUTE)
    assert backend == "openai"
    assert (kwargs["model_name"], kwargs["api_key"], kwargs["base_url"]) == (
        "anthropic/claude-x",
        "sk-gateway",
        "https://gw.example/v1",
    )
    assert kwargs["default_headers"]["x-litellm-session-id"] == "run-1"


def test_an_own_key_keeps_the_native_backend():
    assert BackendAdapter.adapt("anthropic", "claude-x", "sk-own") == (
        "anthropic",
        {"model_name": "claude-x", "api_key": "sk-own"},
    )


async def _execute(monkeypatch, *, own_key=None, error=None):
    """Run RLMService.execute on a fake RLM library; returns (result, RLM kwargs)."""
    route = AsyncMock(return_value=ROUTE)
    monkeypatch.setattr("services.gateway.routing.gateway_route", route)
    seen = {}
    rlm = ModuleType("rlm")

    class RLM:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def completion(self, prompt):
            if error is not None:
                raise error
            usage = SimpleNamespace(total_cost=None, total_input_tokens=1, total_output_tokens=1)
            return SimpleNamespace(response="done", metadata={}, usage_summary=usage)

    rlm.RLM = RLM
    logger = ModuleType("rlm.logger")
    logger.RLMLogger = lambda: object()
    # setitem, not patch.dict: restoring the whole of sys.modules would drop
    # modules first imported during the run (services.handlers.tools) and
    # split later tests' patches from the copies the code imports.
    monkeypatch.setitem(sys.modules, "rlm", rlm)
    monkeypatch.setitem(sys.modules, "rlm.logger", logger)
    with (
        patch("services.llm.config.is_model_valid_for_provider", return_value=True),
        patch("services.skill_runtime.skill_tool_info", return_value=None),
    ):
        result = await RLMService(auth=_auth(own_key)).execute(
            "rlm-1",
            {"prompt": "solve", "provider": "anthropic", "model": "claude-x"},
            workflow_id="wf",
            context={"execution_id": "run-1"},
            database=MagicMock(),
        )
    return result, seen, route


async def test_rlm_without_an_own_key_runs_on_the_gateway(monkeypatch):
    result, seen, route = await _execute(monkeypatch)
    assert result["success"] is True
    assert seen["backend"] == "openai"
    assert seen["backend_kwargs"]["model_name"] == "anthropic/claude-x"
    route.assert_awaited_once_with("anthropic", openai_format=True)


async def test_rlm_with_an_own_key_never_asks_the_gateway(monkeypatch):
    result, seen, route = await _execute(monkeypatch, own_key="sk-own")
    assert result["success"] is True
    assert seen["backend"] == "anthropic"
    route.assert_not_awaited()


async def test_a_spent_allowance_is_worded_as_the_opencompany_allowance(monkeypatch):
    result, _, _ = await _execute(monkeypatch, error=_gateway_error(429, "budget_exceeded"))
    assert result["success"] is False
    assert result["error"].startswith("Your OpenCompany allowance for this month is used up.")


async def test_a_connected_small_model_without_a_key_runs_on_the_gateway(monkeypatch):
    route = GatewayRoute(api_key="sk-gateway", base_url="https://gw.example/v1", model_prefix="openai/")
    monkeypatch.setattr("services.gateway.routing.gateway_route", AsyncMock(return_value=route))
    backends, kwargs = await ChatModelExtractor.extract(
        [{"node_type": "openaiChatModel", "parameters": {"model": "gpt-4o-mini"}}], _auth()
    )
    assert backends == ["openai"]
    assert kwargs[0]["model_name"] == "openai/gpt-4o-mini"

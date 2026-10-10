"""The vision tool runs on the gateway with no own key for its provider
(nodes/vision/vision_analyze)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog
from PIL import Image

from nodes.vision.vision_analyze import VisionAnalyzeNode, VisionAnalyzeParams, VisionAnalyzeToolInput
from services.gateway.routing import GatewayRoute
from services.plugin import NodeContext, NodeUserError
from services.plugin.base import _make_connection_factory

from .test_errors import _gateway_error

ROUTES = {
    "openai": GatewayRoute(api_key="sk-gateway", base_url="https://gw.example/v1", model_prefix="openai/"),
    "anthropic": GatewayRoute(api_key="sk-gateway", base_url="https://gw.example", model_prefix=""),
    "gemini": GatewayRoute(api_key="sk-gateway", base_url="https://gw.example", model_prefix=""),
}


def _ctx(tmp_path: Path, provider: str) -> NodeContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color=(200, 40, 40)).save(workspace / "chart.png")
    ctx = NodeContext(node_id="vision-1", node_type="visionAnalyze", workflow_id="wf-vision", workspace_dir=str(workspace))
    ctx.raw["_tool_config"] = VisionAnalyzeParams(provider=provider, vision_model="vision-model")
    return ctx


@pytest.fixture
def gateway_key(monkeypatch):
    async def key_or_route(ctx, credential_id, provider):
        return "sk-gateway", ROUTES[provider]

    monkeypatch.setattr("services.gateway.routing.key_or_route", key_or_route)


async def _ask(tmp_path, provider):
    with structlog.contextvars.bound_contextvars(workflow_id="wf-vision"):
        return await VisionAnalyzeNode().vision(
            _ctx(tmp_path, provider), VisionAnalyzeToolInput(operation="describe", image="chart.png")
        )


def test_every_provider_key_resolves_through_a_declared_credential():
    """It never declared any, so every real run failed at ctx.connection."""
    factory = _make_connection_factory(VisionAnalyzeNode, {})
    for provider in ("openai", "anthropic", "gemini"):
        assert factory(provider).credential_id == provider


async def test_openai_vision_on_the_gateway(tmp_path, monkeypatch, gateway_key):
    seen = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            seen["model"] = kwargs["model"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="a red chart"))])

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    result = await _ask(tmp_path, "openai")
    assert result.text == "a red chart"
    assert seen["model"] == "openai/vision-model"
    assert (seen["client"]["api_key"], seen["client"]["base_url"]) == ("sk-gateway", "https://gw.example/v1")
    assert "workflow:wf-vision" in seen["client"]["default_headers"]["x-litellm-tags"]
    assert result.vision_model == "vision-model"


async def test_anthropic_vision_on_the_gateway_keeps_bare_ids(tmp_path, monkeypatch, gateway_key):
    seen = {}

    class FakeMessages:
        async def create(self, **kwargs):
            seen["model"] = kwargs["model"]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="chart")])

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", FakeClient)
    assert (await _ask(tmp_path, "anthropic")).text == "chart"
    assert seen["model"] == "vision-model"
    assert seen["client"]["base_url"] == "https://gw.example"


async def test_gemini_vision_on_the_gateway_uses_the_native_route(tmp_path, monkeypatch, gateway_key):
    seen = {}

    class FakeModels:
        async def generate_content(self, **kwargs):
            seen["model"] = kwargs["model"]
            return SimpleNamespace(text="gemini sees a chart")

    class FakeClient:
        def __init__(self, api_key, http_options=None):
            seen["http_options"] = http_options
            self.aio = SimpleNamespace(models=FakeModels())

    monkeypatch.setattr("google.genai.Client", FakeClient)
    assert (await _ask(tmp_path, "gemini")).text == "gemini sees a chart"
    assert seen["model"] == "vision-model"
    assert seen["http_options"].base_url == "https://gw.example"
    assert "x-litellm-tags" in seen["http_options"].headers


async def test_a_spent_allowance_is_worded_as_the_opencompany_allowance(tmp_path, monkeypatch, gateway_key):
    class FakeCompletions:
        async def create(self, **kwargs):
            raise _gateway_error(429, "budget_exceeded")

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    with pytest.raises(NodeUserError, match="Your OpenCompany allowance"):
        await _ask(tmp_path, "openai")

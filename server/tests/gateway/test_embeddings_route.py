"""OpenAI embeddings run on the gateway only with no own key and on OpenAI's
own endpoint (services/memory/vector_store.py, the Embedding Generator)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from services.gateway.routing import GatewayRoute
from services.memory import vector_store
from services.plugin import NodeContext

from .test_errors import _gateway_error

ROUTE = GatewayRoute(api_key="sk-gateway", base_url="https://gw.example/v1", model_prefix="openai/")


def _auth(stored=None):
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value=stored)
    return auth


@pytest.fixture
def gateway(monkeypatch):
    route = AsyncMock(return_value=ROUTE)
    monkeypatch.setattr("services.gateway.routing.gateway_route", route)
    return route


@pytest.fixture
def openai_sdk(monkeypatch):
    """A fake AsyncOpenAI: records the client options and every request."""
    seen = SimpleNamespace(client={}, calls=[], error=None)

    class FakeEmbeddings:
        async def create(self, **kwargs):
            seen.calls.append(kwargs)
            if seen.error is not None:
                raise seen.error
            return SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=[0.1, 0.2]) for i, _ in enumerate(kwargs["input"])]
            )

    class FakeClient:
        def __init__(self, **kwargs):
            seen.client = kwargs
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    return seen


async def test_own_keys_come_first(gateway):
    access = vector_store.openai_embedding_access
    assert await access("sk-given", "", _auth("sk-stored")) == ("sk-given", None)
    assert await access(None, "", _auth("sk-stored")) == ("sk-stored", None)
    gateway.assert_not_awaited()


async def test_without_an_own_key_the_gateway_serves(gateway):
    assert await vector_store.openai_embedding_access(None, "", _auth()) == ("sk-gateway", ROUTE)


async def test_an_endpoint_override_never_gets_the_gateway_key(gateway):
    assert await vector_store.openai_embedding_access(None, "http://localhost:8000/v1", _auth()) == (None, None)
    gateway.assert_not_awaited()


async def test_gateway_calls_carry_the_prefix_and_the_runs_attribution(openai_sdk):
    embedder = vector_store.create_embedder("openai", route=ROUTE)
    with structlog.contextvars.bound_contextvars(chat_run_id="run-1"):
        assert await embedder.embed_query("hello") == [0.1, 0.2]
    assert openai_sdk.client == {"api_key": "sk-gateway", "base_url": "https://gw.example/v1"}
    assert openai_sdk.calls[0]["model"] == "openai/text-embedding-3-small"
    assert openai_sdk.calls[0]["extra_headers"]["x-litellm-session-id"] == "run-1"


async def test_a_spent_allowance_is_worded_as_the_opencompany_allowance(openai_sdk):
    openai_sdk.error = _gateway_error(429, "budget_exceeded")
    embedder = vector_store.create_embedder("openai", route=ROUTE)
    with pytest.raises(vector_store.EmbeddingProviderError, match="Your OpenCompany allowance"):
        await embedder.embed_query("hello")


def test_an_embedder_never_falls_back_to_the_environment_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    with pytest.raises(vector_store.EmbedderUnavailableError):
        vector_store.OpenAIEmbedder("text-embedding-3-small", api_key=None)


async def test_memory_builds_a_gateway_store_with_no_own_key(gateway, openai_sdk):
    store = await vector_store.get_memory_vector_store("gw-session", provider="openai", auth_service=_auth())
    try:
        assert store is not None
        assert openai_sdk.client["base_url"] == "https://gw.example/v1"
        (key,) = [k for k in vector_store._memory_vector_stores if getattr(k, "session_id", None) == "gw-session"]
        assert key.endpoint == "https://gw.example/v1"
    finally:
        await vector_store.clear_memory_vector_stores("gw-session")


async def test_the_embedding_node_runs_on_the_gateway_without_a_key(gateway, openai_sdk, monkeypatch):
    from nodes.document.embedding_generator import EmbeddingGeneratorNode, EmbeddingGeneratorParams

    monkeypatch.setattr("services.plugin.deps.get_auth_service", lambda: _auth())
    out = await EmbeddingGeneratorNode().embed(
        NodeContext(node_id="embed-1", node_type="embeddingGenerator"),
        EmbeddingGeneratorParams(provider="openai", model="text-embedding-3-small", chunks=[{"content": "hi"}]),
    )
    assert out.embedding_count == 1
    assert openai_sdk.calls[0]["model"] == "openai/text-embedding-3-small"

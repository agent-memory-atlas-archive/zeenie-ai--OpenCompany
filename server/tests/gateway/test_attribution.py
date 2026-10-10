"""services/gateway/attribution.py: what a gateway call says about itself,
from the log context, allowlisted ids only."""

import importlib.util
import json
from pathlib import Path

import pytest
import structlog

from services.gateway.attribution import REPLAY_TTL_SECONDS, replay_cache, request_headers


def test_headers_name_the_chat_run_and_carry_only_allowlisted_ids():
    with structlog.contextvars.bound_contextvars(
        chat_run_id="r_1",
        root_execution_id="root",
        workflow_id="wf",
        user_id="owner",
        node_id="agent-1",
        prompt="a secret the user typed",
    ):
        headers = request_headers()
    assert headers["x-litellm-session-id"] == "r_1"
    assert headers["x-litellm-tags"] == "source:chat,workflow:wf,user:owner"
    assert json.loads(headers["x-litellm-spend-logs-metadata"]) == {
        "chat_run_id": "r_1",
        "root_execution_id": "root",
        "workflow_id": "wf",
        "user_id": "owner",
        "node_id": "agent-1",
    }
    assert "a secret the user typed" not in json.dumps(headers)


@pytest.mark.parametrize(
    ("bound", "session"),
    [
        ({"root_execution_id": "root", "execution_id": "exec", "temporal_workflow_id": "t"}, "root"),
        ({"execution_id": "exec", "temporal_workflow_id": "t"}, "exec"),
        ({"temporal_workflow_id": "t"}, "t"),
    ],
)
def test_session_falls_back_from_the_chat_run(bound, session):
    with structlog.contextvars.bound_contextvars(**bound):
        assert request_headers()["x-litellm-session-id"] == session


def test_outside_any_run_only_the_source_is_sent():
    assert request_headers() == {"x-litellm-tags": "source:direct"}


def test_replay_cache_needs_a_temporal_activity():
    with structlog.contextvars.bound_contextvars(chat_run_id="r_1"):
        assert replay_cache("sk-gw") is None


def _namespace(api_key="sk-gw", **ids):
    with structlog.contextvars.bound_contextvars(**ids):
        body = replay_cache(api_key)
    assert body is not None
    assert body["cache"]["use-cache"] is True
    assert body["cache"]["ttl"] == REPLAY_TTL_SECONDS
    return body["cache"]["namespace"]


def test_replay_namespace_is_stable_across_attempts_and_new_per_run_and_key():
    ids = {"temporal_workflow_id": "wf", "temporal_run_id": "run-1", "temporal_activity_id": "llm-step-1"}
    first = _namespace(**ids, temporal_attempt=1)
    assert first.startswith("oc1-")
    assert _namespace(**ids, temporal_attempt=4) == first
    assert _namespace(**{**ids, "temporal_run_id": "run-2"}) != first
    assert _namespace(**{**ids, "temporal_activity_id": "llm-step-2"}) != first
    assert _namespace("sk-other", **ids) != first


def test_log_context_restores_the_outer_ids():
    """A node executed inside another (a tool call) must not wipe its
    parent's ids. tests/conftest.py stubs core.logging, so load the file."""
    path = Path(__file__).resolve().parents[2] / "core" / "logging.py"
    spec = importlib.util.spec_from_file_location("_core_logging_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with module.log_context_sync(chat_run_id="r_outer", node_id="agent"):
        with module.log_context_sync(node_id="tool"):
            assert structlog.contextvars.get_contextvars()["node_id"] == "tool"
        assert structlog.contextvars.get_contextvars()["node_id"] == "agent"
        assert structlog.contextvars.get_contextvars()["chat_run_id"] == "r_outer"
    assert "node_id" not in structlog.contextvars.get_contextvars()


async def test_async_log_context_restores_the_outer_ids():
    path = Path(__file__).resolve().parents[2] / "core" / "logging.py"
    spec = importlib.util.spec_from_file_location("_core_logging_under_test_async", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async with module.log_context(execution_id="outer"):
        async with module.log_context(execution_id="inner"):
            assert structlog.contextvars.get_contextvars()["execution_id"] == "inner"
        assert structlog.contextvars.get_contextvars()["execution_id"] == "outer"

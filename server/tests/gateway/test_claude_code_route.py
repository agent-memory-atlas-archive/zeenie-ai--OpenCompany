"""Claude Code: its own login first, then the install's Anthropic key, then
the gateway (nodes/agent/claude_code_agent/_oauth.py, services/cli_agent)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from nodes.agent.claude_code_agent import _oauth
from nodes.agent.claude_code_agent._oauth import ApiAccess, api_access
from services.gateway.routing import GatewayRoute

ROUTE = GatewayRoute(api_key="sk-gateway", base_url="https://gw.example", model_prefix="")


@pytest.fixture
def install(monkeypatch):
    """Configure the login, the stored Anthropic key and the gateway route."""

    def configure(*, logged_in=False, own_key=None, route=ROUTE):
        from core.container import container

        auth = MagicMock()
        auth.resolve_api_key = AsyncMock(return_value=own_key)
        monkeypatch.setattr(container, "auth_service", MagicMock(return_value=auth))
        monkeypatch.setattr("services.gateway.routing.gateway_route", AsyncMock(return_value=route))
        status = AsyncMock(return_value=logged_in)
        monkeypatch.setattr(_oauth, "claude_auth_status", status)
        return status

    return configure


async def test_the_clis_own_login_comes_first(install):
    install(logged_in=True, own_key="sk-own")
    assert await api_access({}) == ApiAccess()


async def test_without_a_login_the_install_key_comes_next(install):
    install(own_key="sk-own")
    assert await api_access({}) == ApiAccess(env={"ANTHROPIC_API_KEY": "sk-own"})


async def test_without_a_login_or_key_claude_runs_on_the_gateway(install):
    install()
    with structlog.contextvars.bound_contextvars(chat_run_id="run-1", workflow_id="wf-1"):
        access = await api_access({})
    assert access.per_run is True
    assert access.env["ANTHROPIC_BASE_URL"] == "https://gw.example"
    assert access.env["ANTHROPIC_AUTH_TOKEN"] == "sk-gateway"
    headers = dict(line.split(": ", 1) for line in access.env["ANTHROPIC_CUSTOM_HEADERS"].splitlines())
    assert headers["x-litellm-session-id"] == "run-1"
    assert "workflow:wf-1" in headers["x-litellm-tags"]


async def test_without_the_gateway_nothing_changes(install):
    """Not even an Anthropic key: claude uses its login as always."""
    status = install(own_key="sk-own", route=None)
    assert await api_access({}) == ApiAccess()
    status.assert_not_awaited()


async def test_api_settings_in_the_servers_environment_are_left_alone(install):
    status = install()
    assert await api_access({"ANTHROPIC_BASE_URL": "https://proxy.example"}) == ApiAccess()
    status.assert_not_awaited()


@pytest.mark.parametrize("per_run", [True, False])
async def test_a_gateway_process_never_serves_another_run(monkeypatch, tmp_path, per_run):
    from services.cli_agent.service import AICliService
    from services.cli_agent.types import ClaudeTaskSpec

    access = ApiAccess(env={"ANTHROPIC_AUTH_TOKEN": "sk-gateway"} if per_run else {}, per_run=per_run)
    monkeypatch.setattr(_oauth, "api_access", AsyncMock(return_value=access))
    pool = SimpleNamespace(
        start_reaper=AsyncMock(),
        acquire=AsyncMock(return_value="pooled"),
        send_turn=AsyncMock(return_value="result"),
        release=AsyncMock(),
        terminate=AsyncMock(),
    )
    monkeypatch.setattr("services.cli_agent.service.get_session_pool", lambda _: pool)
    await AICliService()._run_pooled_turn(
        task=ClaudeTaskSpec(prompt="task"), session_key="session", cwd=tmp_path, workspace_dir=tmp_path,
        defaults={}, mcp_port=8000, mcp_bearer_token="abcdefgh-token", connected_tools=[],
        connected_skill_names=[], workflow_id="workflow",
    )
    env = pool.acquire.call_args.kwargs["env"]
    assert (env.get("ANTHROPIC_AUTH_TOKEN") == "sk-gateway") is per_run
    if per_run:
        pool.terminate.assert_awaited_once_with("session")
        pool.release.assert_not_awaited()
    else:
        pool.release.assert_awaited_once_with("pooled")
        pool.terminate.assert_not_awaited()


async def test_an_agent_never_gets_the_gateway_key():
    from services.cli_agent import mcp_server

    tools = {}

    class FakeMCP:
        def tool(self, name=None, **_):
            def register(fn):
                tools[name] = fn
                return fn

            return register

    mcp_server._build_tools(FakeMCP())
    token = mcp_server._current_batch.set(SimpleNamespace(allowed_credentials={"opencompany_gateway"}))
    try:
        refused = await tools["getCredential"]("opencompany_gateway")
    finally:
        mcp_server._current_batch.reset(token)
    assert refused["status"] == 403
    assert "value" not in refused

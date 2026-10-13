"""The browser task's claim token reaches the Browser node on both tool paths.

An agent claims its browser profile with a token (its workflow id) and sends
it with every browser call. ``BaseNode.as_activity`` and the in-process
``_dispatch_tool`` hand a node the keys it declares in ``context_keys``;
before they did, the node compared the claim with the run's execution id and
refused its own task (BrowserBusy).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

import nodes  # noqa: F401 -- populate plugin registry
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

TOKEN = "AI_Employee_1-Task_Completed-task_1:terminal-agent-2:ai_employee:1-delegate-2:browser_agent:1-1-1-task_1"
SCOPE = {
    "_browser_task_id": TOKEN,
    "_browser_owner": {"profile_id": "profile", "owner_id": "local", "profile_task_id": TOKEN},
    "parameter_snapshot": {"wf:browser:1": {"interaction": "full"}},
    "native_workspace_version": 1,
    "browser_runtime_version": 1,
    "browser_routing_version": 1,
}
SNAPSHOT = {"operation": "snapshot"}


@pytest.fixture
def service(monkeypatch):
    import core.container as container_module
    import services.status_broadcaster as status_broadcaster
    from services.approvals import tool_calls

    workflow_service = MagicMock()
    workflow_service.execute_node = AsyncMock(return_value={"success": True, "result": {}})
    monkeypatch.setattr(container_module, "container", SimpleNamespace(workflow_service=lambda: workflow_service))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: AsyncMock())
    # Ask first is covered elsewhere (tests/services/approvals): every call here runs as sent.
    monkeypatch.setattr(tool_calls, "check",
                        AsyncMock(return_value=SimpleNamespace(result=None, node_data=None, tool_args=None, record=None)))
    return workflow_service


def call(node_id: str, args: dict, **scope) -> dict:
    return {
        "node_id": node_id,
        "workflow_id": "wf",
        "execution_id": "wf:execution:3",
        "user_id": "owner",
        "parent_node_id": "wf:browser_agent:1",
        "tool_call_id": "call_1",
        "node_data": dict(args),
        "tool_args": dict(args),
        **scope,
    }


async def test_the_activity_hands_the_browser_its_task_scope(service):
    await ActivityEnvironment().run(get_node_class("browser").as_activity(), call("wf:browser:1", SNAPSHOT, **SCOPE))
    extras = service.execute_node.await_args.kwargs["extras"]
    assert {key: extras.get(key) for key in SCOPE} == SCOPE


async def test_a_node_that_does_not_read_the_scope_is_not_handed_it(service):
    add = {"operation": "add", "a": 1, "b": 2}
    await ActivityEnvironment().run(get_node_class("calculatorTool").as_activity(), call("wf:calculatorTool:1", add, **SCOPE))
    assert not set(SCOPE) & set(service.execute_node.await_args.kwargs["extras"])


async def test_in_process_dispatch_hands_the_browser_its_task_scope(monkeypatch):
    from services.approvals import tool_calls
    from services.handlers import tools

    seen: dict = {}

    async def execute_as_tool(self, tool_args, node_params, ctx):
        seen.update(ctx.raw)
        return {"success": True}

    monkeypatch.setattr(get_node_class("browser"), "execute_as_tool", execute_as_tool)
    monkeypatch.setattr(tool_calls, "check_in_process",
                        AsyncMock(return_value=SimpleNamespace(result=None, node_data=None, tool_args=None)))
    config = {"node_type": "browser", "node_id": "wf:browser:1", "parameters": {}, "workflow_id": "wf",
              "execution_id": "wf:execution:3", "user_id": "owner", **SCOPE}
    await tools._dispatch_tool("browser", dict(SNAPSHOT), config)
    assert {key: seen.get(key) for key in SCOPE} == SCOPE

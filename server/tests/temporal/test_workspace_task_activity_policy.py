"""Actual native-agent leaf scheduling, without a Temporal server."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from services.plugin.scaling import RetryPolicy
from services.temporal import agent_workflow as module
from temporalio.workflow import ActivityCancellationType


@pytest.mark.parametrize("pool", [False, True])
async def test_workspace_task_honors_plugin_policy_and_authenticated_principal(monkeypatch, pool):
    plugin = SimpleNamespace(
        workspace_task=True,
        start_to_close_timeout=timedelta(hours=3),
        heartbeat_timeout=timedelta(seconds=75),
        retry_policy=RetryPolicy(maximum_attempts=1),
        task_queue="device-pool",
    )
    monkeypatch.setattr(module, "get_node_class", lambda _: plugin)
    patched = Mock(return_value=True)
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module.workflow, "patched", patched)
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    tool_payload = {"node_type": "embedded_task", "node_id": "child", "user_id": "untrusted", "nodes": [{"id": "model"}]}
    result = await module._execute_plugin_tool_activity(
        "node.embedded_task.v1", "call-1", tool_payload, {"user_id": "authenticated-owner", "temporal_worker_pool_enabled": pool}
    )
    patched.assert_called_once_with("workspace-task-activity-policy-v1")
    assert result == {"success": True}
    kwargs = execute.await_args.kwargs
    assert kwargs["start_to_close_timeout"] == timedelta(hours=3)
    assert kwargs["heartbeat_timeout"] == timedelta(seconds=75)
    assert kwargs["retry_policy"].maximum_attempts == 1
    assert kwargs["cancellation_type"] == ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    assert kwargs.get("task_queue") == ("device-pool" if pool else None)
    assert kwargs["args"][0]["user_id"] == "authenticated-owner"
    assert kwargs["args"][0]["nodes"] == [{"id": "model"}]
    assert tool_payload["user_id"] == "untrusted"  # caller input is not mutated


async def test_prepatch_workspace_history_emits_identical_legacy_command(monkeypatch):
    monkeypatch.setattr(module, "get_node_class", lambda _: SimpleNamespace(workspace_task=True))
    monkeypatch.setattr(module.workflow, "patched", Mock(return_value=False))
    execute = AsyncMock()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    payload = {"node_type": "embedded_task", "node_id": "child"}
    await module._execute_plugin_tool_activity(
        "node.embedded_task.v1", "old-id", payload, {"user_id": "42", "temporal_worker_pool_enabled": True}
    )
    execute.assert_awaited_once_with(
        "node.embedded_task.v1",
        args=[payload],
        activity_id="old-id",
        start_to_close_timeout=module.TOOL_STEP_TIMEOUT,
        heartbeat_timeout=module.TOOL_HEARTBEAT_TIMEOUT,
    )
    assert "user_id" not in payload


async def test_ordinary_tools_do_not_add_patch_markers_or_change_policy(monkeypatch):
    monkeypatch.setattr(module, "get_node_class", lambda _: SimpleNamespace(workspace_task=False))
    patched = Mock(side_effect=AssertionError("Ordinary tool must not change history"))
    monkeypatch.setattr(module.workflow, "patched", patched)
    execute = AsyncMock()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    payload = {"node_type": "ordinary_tool"}
    await module._execute_plugin_tool_activity("node.ordinary_tool.v1", "ordinary-id", payload, {})
    patched.assert_not_called()
    execute.assert_awaited_once_with(
        "node.ordinary_tool.v1",
        args=[payload],
        activity_id="ordinary-id",
        start_to_close_timeout=module.TOOL_STEP_TIMEOUT,
        heartbeat_timeout=module.TOOL_HEARTBEAT_TIMEOUT,
    )

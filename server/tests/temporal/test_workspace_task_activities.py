"""Cleanup activity scope and failure behavior; no device or Temporal server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from services.temporal.workspace_task_activities import reset_workspace_task_runtime


@pytest.mark.parametrize("status,generation", [(None, 0), ("resetting", 5), ("running", 0), ("reset", 0)])
async def test_cleanup_waits_for_legacy_roots_and_passes_retired_scope(monkeypatch, status, generation):
    from services import node_invocations, node_registry
    from services.plugin import deps

    order = []
    async def cancel(_):
        order.append("legacy_closed")
        return 2
    async def clear(**kwargs):
        assert order == ["legacy_closed"]
        assert kwargs["generation"] == generation
        assert kwargs["workflow_id"] == "wf"
        order.append("runtime_cleared")
        return {"reset": True}
    control = SimpleNamespace(status=status, generation=5, execution_id="exec") if status else None
    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=control))
    monkeypatch.setattr(deps, "get_database", lambda: database)
    monkeypatch.setattr(node_invocations, "cancel_legacy_invocations", cancel)
    monkeypatch.setattr(node_invocations, "workspace_task_nodes", AsyncMock(return_value=({}, [{"id": "phone", "type": "device"}])))
    monkeypatch.setattr(node_registry, "get_node_class", lambda _: SimpleNamespace(reset_execution_state=clear))
    result = await ActivityEnvironment().run(reset_workspace_task_runtime, {"workflow_id": "wf"})
    assert result == {"reset_nodes": ["phone"], "legacy_cancelled": 2}
    assert order == ["legacy_closed", "runtime_cleared"]


async def test_legacy_cleanup_failure_does_not_acknowledge_runtime_reset(monkeypatch):
    from services import node_invocations

    monkeypatch.setattr(node_invocations, "cancel_legacy_invocations", AsyncMock(side_effect=RuntimeError("Temporal unavailable")))
    nodes = AsyncMock()
    monkeypatch.setattr(node_invocations, "workspace_task_nodes", nodes)
    with pytest.raises(RuntimeError, match="unavailable"):
        await ActivityEnvironment().run(reset_workspace_task_runtime, {"workflow_id": "wf"})
    nodes.assert_not_awaited()

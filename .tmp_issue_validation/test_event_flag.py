"""Issue #143 diagnostic: flag-off drops teammate completion delivery.

Run from server with its Python and ``-p tests.conftest``. Only external
delivery functions are mocked; the task-event producer and dispatch are real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodes.agent._events import broadcast_agent_task_completed
from services.events import dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_task_completion_dispatch_depends_on_framework_flag(monkeypatch, enabled):
    monkeypatch.setattr(
        dispatch, "Settings", lambda: SimpleNamespace(event_framework_enabled=enabled)
    )
    signal = AsyncMock()
    broadcast = AsyncMock()
    monkeypatch.setattr(dispatch, "_signal_running_consumers", signal)
    monkeypatch.setattr(dispatch, "_broadcast_in_process", broadcast)

    await broadcast_agent_task_completed(
        task_id="diagnostic-task",
        agent_name="Worker",
        agent_node_id="worker",
        parent_node_id="lead",
        workflow_id="diagnostic-workflow",
        result="Reviewed email draft",
    )

    assert signal.await_count == int(enabled)
    assert broadcast.await_count == int(enabled)
    if enabled:
        event = signal.await_args.args[0]
        assert event.data["task_id"] == "diagnostic-task"
        assert event.data["result"] == "Reviewed email draft"

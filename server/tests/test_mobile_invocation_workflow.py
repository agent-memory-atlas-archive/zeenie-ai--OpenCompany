"""Durable admission orchestration without a live Temporal server."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.temporal.node_invocation import NodeInvocationWorkflow


@pytest.fixture
def payload():
    return {
        "principal": "owner",
        "workflow_id": "saved",
        "node_id": "mobile",
        "fingerprint": "hash",
        "activity": "node.mobile_use_agent.v1",
        "task_queue": "android",
        "timeout_s": 900,
        "context": {"generation": 2, "nodes": [{"id": "mobile"}], "edges": []},
    }


async def test_result_and_retry_policy_survive_summary_failure(monkeypatch, payload):
    from services.temporal.node_invocation import workflow

    execute = AsyncMock(side_effect=[{"success": True, "result": "done"}, RuntimeError("summary unavailable")])
    monkeypatch.setattr(workflow, "execute_activity", execute)
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="invocation", run_id="attempt"))
    monkeypatch.setattr(workflow, "logger", SimpleNamespace(warning=lambda *_args: None))
    run = NodeInvocationWorkflow()
    assert await run.run(payload) == {"success": True, "result": "done"}
    assert run.describe()["status"] == "completed"
    first, record = execute.await_args_list
    assert first.args == (payload["activity"], payload["context"])
    assert first.kwargs["retry_policy"].maximum_attempts == 1
    assert first.kwargs["cancellation_type"] == workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    assert record.args[1]["status"] == "success"


async def test_cancellation_records_terminal_status_and_propagates(monkeypatch, payload):
    from services.temporal.node_invocation import workflow

    entered = asyncio.Event()
    records = []

    async def execute(name, data, **kwargs):
        if name == payload["activity"]:
            entered.set()
            await asyncio.Event().wait()
        else:
            records.append(data)

    monkeypatch.setattr(workflow, "execute_activity", execute)
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="invocation", run_id="attempt"))
    run = NodeInvocationWorkflow()
    task = asyncio.create_task(run.run(payload))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert run.describe()["status"] == "cancelled"
    assert records[0]["run_id"] == "invocation:attempt"

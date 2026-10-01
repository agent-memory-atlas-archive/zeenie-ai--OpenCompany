"""A canvas Run on the Temporal path reports why it failed and what it ran.

MachinaWorkflow returns per-node failures as ``errors`` (a list of
``{node_id, error, ...}``), and only an empty graph returns a top-level
``error``. It also counts the nodes in its executable graph as
``total_nodes``. The editor's Run dialog shows ``error`` when a run fails and
``completed_nodes`` / ``total_nodes`` when it succeeds, so all of them must
survive TemporalExecutor and WorkflowService._execute_temporal.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import nodes  # noqa: F401 -- populate the node registry


async def _run(client_result=None, client_error=None):
    from services.temporal.executor import TemporalExecutor

    client = MagicMock()
    client.execute_workflow = AsyncMock(return_value=client_result, side_effect=client_error)
    return await TemporalExecutor(client).execute_workflow(workflow_id="wf-1", nodes=[], edges=[])


@pytest.mark.asyncio
async def test_a_node_failure_reaches_the_run_result():
    failure = {"node_id": "wf-1:aiAgent:1", "error": "No API key for openai", "hint": "Add one in Credentials"}

    result = await _run({"success": False, "outputs": {}, "execution_trace": [], "errors": [failure]})

    assert result["success"] is False
    assert result["errors"] == [failure]
    assert result["error"] == "No API key for openai"


@pytest.mark.asyncio
async def test_a_workflow_level_error_is_reported_like_a_node_failure():
    result = await _run({"success": False, "error": "No nodes provided", "outputs": {}, "execution_trace": []})

    assert result["errors"] == [{"error": "No nodes provided"}]
    assert result["error"] == "No nodes provided"


@pytest.mark.asyncio
async def test_a_client_failure_is_reported_like_a_node_failure():
    result = await _run(client_error=RuntimeError("worker unavailable"))

    assert result["success"] is False
    assert result["errors"] == [{"error": "RuntimeError: worker unavailable"}]
    assert result["error"] == "RuntimeError: worker unavailable"


@pytest.mark.asyncio
async def test_a_successful_run_reports_no_error():
    result = await _run({"success": True, "outputs": {}, "execution_trace": ["n1"], "errors": None})

    assert result["errors"] == []
    assert result["error"] is None


@pytest.mark.asyncio
async def test_the_run_counts_its_nodes():
    result = await _run({"success": True, "outputs": {}, "execution_trace": ["start-1", "py-1"], "total_nodes": 2})

    assert (result["completed_nodes"], result["total_nodes"]) == (2, 2)


@pytest.mark.asyncio
async def test_the_workflow_service_passes_the_message_and_counts_on():
    from services.workflow import WorkflowService

    service = WorkflowService.__new__(WorkflowService)
    service._resolve_workflow_slug = AsyncMock(return_value="workflow")
    failure = {"node_id": "n1", "error": "boom"}
    service._temporal_executor = SimpleNamespace(
        execute_workflow=AsyncMock(
            return_value={
                "success": False,
                "nodes_executed": ["start-1"],
                "outputs": {},
                "errors": [failure],
                "error": "boom",
                "total_nodes": 3,
                "completed_nodes": 1,
            }
        )
    )

    result = await service._execute_temporal([], [], "session", None, time.time(), "wf-1")

    assert result["errors"] == [failure]
    assert result["error"] == "boom"
    assert (result["completed_nodes"], result["total_nodes"]) == (1, 3)


async def _run_machina_workflow(monkeypatch, failing=()):
    """Run the real MachinaWorkflow body on start -> py-1 -> py-2 without a
    Temporal server (the pattern of test_machina_workflow_loop.py)."""
    from temporalio import workflow as temporal_workflow

    from services.temporal.workflow import MachinaWorkflow

    def start_activity(name, **kwargs):
        node_id = kwargs["args"][0]["node_id"]
        future = asyncio.get_event_loop().create_future()
        future.set_result({"success": False, "error": f"{node_id} broke"} if node_id in failing else {"success": True, "result": {}})
        return future

    async def execute_activity(*args, **kwargs):
        return None

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(temporal_workflow, "start_activity", start_activity)
    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        MachinaWorkflow,
        "_resolve_dispatch",
        lambda self, node_type, **_kwargs: {"kind": "activity", "name": f"node.{node_type}.v1", "queue": None},
    )
    ids = ["start-1", "py-1", "py-2"]
    graph_nodes = [{"id": node_id, "type": "start" if node_id == "start-1" else "pythonExecutor", "data": {}} for node_id in ids]
    edges = [
        {"id": f"e{index}", "source": source, "target": target, "sourceHandle": "output-main", "targetHandle": "input-main"}
        for index, (source, target) in enumerate(zip(ids, ids[1:]))
    ]
    return await MachinaWorkflow().run(
        {"nodes": graph_nodes, "edges": edges, "session_id": "test", "workflow_id": "wf-1", "execution_id": "wf-1-run"}
    )


@pytest.mark.asyncio
async def test_the_workflow_counts_the_nodes_of_its_run(monkeypatch):
    finished = await _run_machina_workflow(monkeypatch)
    failed = await _run_machina_workflow(monkeypatch, failing={"py-1"})

    assert (finished["success"], finished["execution_trace"], finished["total_nodes"]) == (True, ["start-1", "py-1", "py-2"], 3)
    assert (failed["success"], failed["execution_trace"], failed["total_nodes"]) == (False, ["start-1"], 3)
    assert failed["errors"] == [{"node_id": "py-1", "error": "py-1 broke"}]

"""A failed canvas Run on the Temporal path reports why it failed.

MachinaWorkflow returns per-node failures as ``errors`` (a list of
``{node_id, error, ...}``), and only an empty graph returns a top-level
``error``. The Run response the editor shows reads ``error`` for its message
and ``errors`` for detail, so both must survive TemporalExecutor and
WorkflowService._execute_temporal.
"""

from __future__ import annotations

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
async def test_the_workflow_service_passes_the_message_on():
    from services.workflow import WorkflowService

    service = WorkflowService.__new__(WorkflowService)
    service._resolve_workflow_slug = AsyncMock(return_value="workflow")
    failure = {"node_id": "n1", "error": "boom"}
    service._temporal_executor = SimpleNamespace(
        execute_workflow=AsyncMock(
            return_value={"success": False, "nodes_executed": [], "outputs": {}, "errors": [failure], "error": "boom"}
        )
    )

    result = await service._execute_temporal([], [], "session", None, time.time(), "wf-1")

    assert result["errors"] == [failure]
    assert result["error"] == "boom"

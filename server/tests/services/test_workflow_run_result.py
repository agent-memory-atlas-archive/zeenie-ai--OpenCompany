"""Every Run path reports a failure and its node count the same way.

The editor's Run dialog shows ``error`` when a run fails and
``completed_nodes`` / ``total_nodes`` when it succeeds, whichever path ran
it: Temporal (tests/temporal/test_run_errors.py), the Redis-only parallel
engine or the sequential fallback (here).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.workflow import WorkflowService


def _chain():
    graph_nodes = [
        {"id": "start-1", "type": "start", "data": {}},
        {"id": "a", "type": "console", "data": {}},
        {"id": "b", "type": "console", "data": {}},
    ]
    edges = [{"id": "e1", "source": "start-1", "target": "a"}, {"id": "e2", "source": "a", "target": "b"}]
    return graph_nodes, edges


def _sequential_service(results):
    service = WorkflowService.__new__(WorkflowService)
    service._settings = {}

    async def execute_node(*, node_id, **_kwargs):
        return results.get(node_id, {"success": True, "result": {}})

    service.execute_node = execute_node
    return service


def _parallel_service(executor_result):
    service = WorkflowService.__new__(WorkflowService)
    executor = SimpleNamespace(execute_workflow=AsyncMock(return_value=executor_result))
    service._get_workflow_executor = lambda _status_callback: executor
    return service


@pytest.mark.asyncio
async def test_the_sequential_path_reports_a_failed_node():
    service = _sequential_service({"a": {"success": False, "error": "a broke", "hint": "Check a"}})

    result = await service._execute_sequential(*_chain(), "session", None, time.time())

    assert result["success"] is False
    assert result["errors"] == [{"node_id": "a", "error": "a broke", "hint": "Check a"}]
    assert result["error"] == "a broke"
    # stop_on_error is off, so b still ran.
    assert (result["completed_nodes"], result["total_nodes"]) == (2, 3)


@pytest.mark.asyncio
async def test_the_sequential_path_counts_a_clean_run():
    result = await _sequential_service({})._execute_sequential(*_chain(), "session", None, time.time())

    assert result["success"] is True
    assert (result["errors"], result["error"]) == ([], None)
    assert (result["completed_nodes"], result["total_nodes"]) == (3, 3)


@pytest.mark.asyncio
async def test_the_parallel_path_passes_a_failed_node_on():
    failure = {"node_id": "a", "error": "a broke", "timestamp": 1.0}
    service = _parallel_service({"success": False, "nodes_executed": ["start-1"], "errors": [failure], "total_nodes": 3})

    result = await service._execute_parallel(*_chain(), "session", None, time.time())

    assert result["errors"] == [failure]
    assert result["error"] == "a broke"
    assert (result["completed_nodes"], result["total_nodes"]) == (1, 3)


@pytest.mark.asyncio
async def test_the_parallel_path_keeps_a_run_level_error():
    service = _parallel_service({"success": False, "status": "cancelled", "error": "Cancelled by user"})

    result = await service._execute_parallel(*_chain(), "session", None, time.time())

    assert result["errors"] == [{"error": "Cancelled by user"}]
    assert result["error"] == "Cancelled by user"

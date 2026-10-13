"""Saved workflows must report unavailable plugins without producing output."""

import pytest

from services.node_registry import get_node_class
from services.workflow_validator import validate_workflow


RETIRED_NODE_TYPES = ("vertex_managed_agent", "vertex_agent_admin", "vertexCloudTool")


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type", (*RETIRED_NODE_TYPES, "missingPlugin"))
async def test_unavailable_node_fails_without_storing_output(harness, node_type):
    assert get_node_class(node_type) is None

    result = await harness.execute(node_type, node_id="saved-node")

    harness.assert_envelope(result, success=False)
    assert result["node_id"] == "saved-node"
    assert node_type in result["error"]
    assert "Remove or replace" in result["error"]
    assert result["error_type"] == "NodeUserError"
    assert result["requires_user_action"] is True
    assert result["retryable"] is False
    assert harness.captured.output_store_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type", RETIRED_NODE_TYPES)
async def test_saved_workflow_with_retired_node_fails_validation(node_type):
    node = {"id": "saved-node", "type": node_type, "data": {"label": "Saved node"}}

    report = await validate_workflow(nodes=[node], edges=[])

    assert any(issue["code"] == "UNKNOWN_NODE_TYPE" and issue["node_id"] == "saved-node" for issue in report["errors"])
    assert node["type"] == node_type

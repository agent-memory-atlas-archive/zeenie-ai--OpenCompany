"""Workspace task identities are stable, scoped, and server-authorized."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.node_invocations import invocation_id
from services.authz.workflow_node import resolve_workflow_node
from services.plugin import NodeUserError


SUBMISSION = "63cb635f-c758-42bc-a9b6-673655a72fa0"


def test_invocation_identity_is_stable_and_scoped():
    identity = invocation_id("owner", "workflow", "node", SUBMISSION)
    assert identity == invocation_id("owner", "workflow", "node", SUBMISSION)
    assert (
        len(
            {
                identity,
                invocation_id("other", "workflow", "node", SUBMISSION),
                invocation_id("owner", "other", "node", SUBMISSION),
                invocation_id("owner", "workflow", "other", SUBMISSION),
            }
        )
        == 4
    )


def test_invocation_requires_a_uuid():
    with pytest.raises(ValueError):
        invocation_id("owner", "workflow", "node", "arbitrary-client-id")


@pytest.fixture
def saved_graph(monkeypatch):
    import services.plugin.deps as deps

    graph = {"owner_id": "owner", "nodes": [{"id": "node", "type": "mobile_use_agent"}], "edges": []}
    database = SimpleNamespace(get_workflow=AsyncMock(return_value=SimpleNamespace(data=graph)))
    monkeypatch.setattr(deps, "get_database", lambda: database)
    return graph


async def test_workflow_resolution_rejects_other_owner(saved_graph):
    with pytest.raises(NodeUserError, match="Workflow access denied"):
        await resolve_workflow_node("other", "workflow", "node")


async def test_workflow_resolution_rejects_absent_and_ambiguous_nodes(saved_graph):
    with pytest.raises(NodeUserError, match="Node does not belong"):
        await resolve_workflow_node("owner", "workflow", "missing")
    saved_graph["nodes"].append(dict(saved_graph["nodes"][0]))
    with pytest.raises(NodeUserError, match="Node does not belong"):
        await resolve_workflow_node("owner", "workflow", "node")


async def test_workflow_resolution_returns_saved_graph(saved_graph):
    _, graph, node = await resolve_workflow_node("owner", "workflow", "node")
    assert graph is saved_graph
    assert node is saved_graph["nodes"][0]

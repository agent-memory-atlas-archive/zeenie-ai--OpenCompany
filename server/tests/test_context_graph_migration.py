from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from services.workflow_migrations import normalize_workflow_graph


def _node(node_id: str, node_type: str, x: int = 0):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": x, "y": 20},
        "data": {"label": node_id},
    }


def test_legacy_memory_becomes_isolated_context_and_shared_tool():
    nodes = [
        _node("memory", "simpleMemory"),
        _node("first", "agent", 400),
        _node("second", "agent", 800),
    ]
    edges = [
        {
            "source": "memory",
            "target": "first",
            "sourceHandle": "output-memory",
            "targetHandle": "input-memory",
        },
        {
            "source": "memory",
            "target": "second",
            "source_handle": "output-memory",
            "target_handle": "input-memory",
        },
    ]
    result = normalize_workflow_graph(
        "42",
        nodes,
        edges,
        {"memory": {"memory_content": "## Human\nhello"}},
    )

    contexts = [node for node in result.nodes if node["type"] == "context"]
    assert [node["id"] for node in contexts] == ["42:context:1", "42:context:2"]
    context_edges = [edge for edge in result.edges if edge.get("targetHandle") == "input-context"]
    assert len(context_edges) == 2
    assert len({edge["source"] for edge in context_edges}) == 2
    tool_edges = [edge for edge in result.edges if edge.get("targetHandle") == "input-tools"]
    assert len(tool_edges) == 2
    assert {edge["source"] for edge in tool_edges} == {"42:simpleMemory:1"}
    assert len(result.state_imports) == 2
    assert all(item["event_type"] == "legacy_partial" for item in result.state_imports)
    assert all("memory_content" not in node.get("data", {}) for node in contexts)


def test_context_graph_normalization_is_idempotent():
    first = normalize_workflow_graph(
        "7",
        [_node("agent", "agent")],
        [],
    )
    second = normalize_workflow_graph(
        "7",
        first.nodes,
        first.edges,
        first.node_parameters,
    )
    assert second.nodes == first.nodes
    assert second.edges == first.edges
    assert second.aliases == {}


def test_normalization_never_recreates_a_deleted_context():
    # The user deleted the agent's Context on the canvas; saving must not
    # bring it back. Stale companion metadata on the remaining nodes changes
    # nothing either.
    result = normalize_workflow_graph(
        "3",
        [
            {
                **_node("agent", "agent"),
                "data": {"label": "Agent"},
            },
        ],
        [],
    )

    assert not [node for node in result.nodes if node["type"] == "context"]
    assert not [edge for edge in result.edges if edge.get("targetHandle") == "input-context"]
    assert result.warnings == []


def test_normalization_leaves_user_context_topology_alone():
    # A Context left without its agent, and a Context whose edge the user
    # removed, stay exactly as placed: no cascade delete, no edge repair.
    nodes = [
        _node("agent", "agent"),
        {
            **_node("orphan", "context"),
            "data": {
                "label": "orphan",
                "systemManaged": True,
                "agentNodeId": "deleted-agent",
            },
        },
        {
            **_node("unwired", "context"),
            "data": {
                "label": "unwired",
                "systemManaged": True,
                "agentNodeId": "agent",
            },
        },
    ]

    result = normalize_workflow_graph("5", nodes, [])

    contexts = [node for node in result.nodes if node["type"] == "context"]
    assert [node["data"]["label"] for node in contexts] == ["orphan", "unwired"]
    assert result.edges == []
    assert result.warnings == []


class _Params(BaseModel):
    pass


class _ContextCapable:
    Params = _Params
    credentials = ()
    requires_context = True
    ui_hints = {}


class _ContextNode:
    Params = _Params
    credentials = ()
    requires_context = False
    ui_hints = {}


@pytest.mark.asyncio
async def test_validator_rejects_multiple_and_shared_contexts_not_missing(monkeypatch):
    from services.workflow_validator import validate_workflow

    monkeypatch.setattr(
        "services.workflow_validator.get_node_class",
        lambda node_type: {
            "agent": _ContextCapable,
            "context": _ContextNode,
        }.get(node_type),
    )
    nodes = [
        _node("a", "agent"),
        _node("b", "agent"),
        _node("c1", "context"),
        _node("c2", "context"),
    ]
    edges = [
        {
            "source": "c1",
            "target": "a",
            "sourceHandle": "output-context",
            "targetHandle": "input-context",
        },
        {
            "source": "c2",
            "target": "a",
            "sourceHandle": "output-context",
            "targetHandle": "input-context",
        },
        {
            "source": "c1",
            "target": "b",
            "sourceHandle": "output-context",
            "targetHandle": "input-context",
        },
    ]
    report = await validate_workflow(nodes, edges)
    codes = {issue["code"] for issue in report["errors"]}
    assert "MULTIPLE_CONTEXTS" in codes
    assert "SHARED_CONTEXT" in codes

    # A Context is optional: an agent without one is a valid graph.
    missing = await validate_workflow([_node("a", "agent")], [])
    assert missing["errors"] == []


@pytest.mark.asyncio
async def test_save_rejects_ambiguous_shared_context_before_persistence(
    monkeypatch,
):
    from services.workflow_storage import handlers

    monkeypatch.setattr(
        "services.workflow_validator.get_node_class",
        lambda node_type: {
            "agent": _ContextCapable,
            "context": _ContextNode,
        }.get(node_type),
    )
    database = type("Database", (), {})()
    database.allocate_workflow_id = AsyncMock(return_value="1")
    database.get_workflow = AsyncMock(return_value=None)
    database.list_workflow_slugs = AsyncMock(return_value=[])
    database.get_node_parameters = AsyncMock(return_value={})
    database.save_workflow = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers.container, "database", lambda: database)

    result = await handlers.handle_save_workflow(
        {
            "workflow_id": "new",
            "name": "Invalid Context",
            "data": {
                "nodes": [
                    _node("a", "agent"),
                    _node("b", "agent"),
                    {
                        **_node("ctx", "context"),
                        "data": {"label": "Context", "systemManaged": True},
                    },
                ],
                "edges": [
                    {
                        "source": "ctx",
                        "target": target,
                        "sourceHandle": "output-context",
                        "targetHandle": "input-context",
                    }
                    for target in ("a", "b")
                ],
            },
        },
        websocket=None,
    )

    assert result["success"] is False
    assert result["error"] == "invalid_context_topology"
    assert "SHARED_CONTEXT" in {issue["code"] for issue in result["validation_errors"]}
    database.save_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_uses_authenticated_owner_and_ignores_client_owner(
    monkeypatch,
):
    from services.workflow_storage import handlers

    database = type("Database", (), {})()
    database.allocate_workflow_id = AsyncMock(return_value="1")
    database.get_workflow = AsyncMock(return_value=None)
    database.list_workflow_slugs = AsyncMock(return_value=[])
    database.get_node_parameters = AsyncMock(return_value={})
    database.save_workflow = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers.container, "database", lambda: database)

    result = await handlers.handle_save_workflow(
        {
            "workflow_id": "new",
            "name": "Owned",
            "data": {
                "nodes": [],
                "edges": [],
                "owner_id": "client-forgery",
            },
        },
        websocket=SimpleNamespace(state=SimpleNamespace(user_id="authenticated-user")),
    )

    assert result["success"] is True
    assert result["data"]["owner_id"] == "authenticated-user"
    assert database.save_workflow.await_args.kwargs["data"]["owner_id"] == ("authenticated-user")


@pytest.mark.asyncio
async def test_save_keeps_a_deleted_context_deleted(
    monkeypatch,
):
    from services.workflow_storage import handlers

    monkeypatch.setattr(
        "services.workflow_validator.get_node_class",
        lambda node_type: {
            "agent": _ContextCapable,
            "context": _ContextNode,
        }.get(node_type),
    )
    existing = SimpleNamespace(
        id="1",
        name="Owned",
        slug="owned",
        description=None,
        data={
            "graphVersion": 2,
            "owner_id": "owner",
            "nodes": [
                _node("1:agent:1", "agent"),
                {
                    **_node("1:context:1", "context"),
                    "data": {
                        "label": "Context",
                        "systemManaged": True,
                        "agentNodeId": "1:agent:1",
                    },
                },
            ],
            "edges": [
                {
                    "source": "1:context:1",
                    "target": "1:agent:1",
                    "sourceHandle": "output-context",
                    "targetHandle": "input-context",
                }
            ],
        },
    )
    database = type("Database", (), {})()
    database.get_workflow = AsyncMock(return_value=existing)
    database.get_node_parameters = AsyncMock(return_value={})
    database.save_workflow = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers.container, "database", lambda: database)

    # The user deleted the Context node (and with it, its edge).
    result = await handlers.handle_save_workflow(
        {
            "workflow_id": "1",
            "name": "Owned",
            "data": {
                "nodes": [_node("1:agent:1", "agent")],
                "edges": [],
            },
        },
        websocket=None,
    )

    assert result["success"] is True
    assert [node["id"] for node in result["data"]["nodes"]] == ["1:agent:1"]
    assert result["data"]["edges"] == []
    assert result["migration_warnings"] == []
    saved = database.save_workflow.await_args.kwargs["data"]
    assert [node["id"] for node in saved["nodes"]] == ["1:agent:1"]


def test_save_owner_resolution_preserves_existing_backend_owner():
    from services.workflow_storage.handlers import _trusted_owner_id

    assert (
        _trusted_owner_id(
            websocket=None,
            existing=SimpleNamespace(data={"owner_id": "stored-owner"}),
        )
        == "stored-owner"
    )


@pytest.mark.asyncio
async def test_workflow_delete_archives_context_before_graph(
    monkeypatch,
):
    from services.workflow_storage import deletion, handlers

    monkeypatch.setattr(deletion, "stop_workflow_for_deletion", AsyncMock(return_value=None))

    database = type("Database", (), {})()
    database.get_workflow = AsyncMock(
        return_value=SimpleNamespace(
            data={
                "nodes": [
                    _node("agent", "agent"),
                    _node("ctx", "context"),
                ]
            }
        )
    )
    database.delete_workflow = AsyncMock(return_value=True)
    import services.agent_context as agent_context

    clear = AsyncMock(return_value=0)
    monkeypatch.setattr(handlers.container, "database", lambda: database)
    monkeypatch.setattr(agent_context, "clear_conversation", clear)

    result = await handlers.handle_delete_workflow(
        {"workflow_id": "12"},
        websocket=None,
    )

    assert result == {
        "success": True,
        "workflow_id": "12",
        "contexts_archived": 1,
        "context_archives_pending": 0,
    }
    clear.assert_awaited_once_with(database, workflow_id="12")
    database.delete_workflow.assert_awaited_once_with("12")


@pytest.mark.asyncio
async def test_rest_delete_uses_shared_context_lifecycle_path(
    monkeypatch,
):
    from routers import database as database_router

    shared_delete = AsyncMock(
        return_value={
            "success": True,
            "workflow_id": "12",
            "contexts_archived": 1,
            "context_archives_pending": 0,
        }
    )
    monkeypatch.setattr(
        database_router,
        "delete_workflow_with_context_archival",
        shared_delete,
    )
    database = object()

    result = await database_router.delete_workflow(
        "12",
        database=database,
    )

    assert result["success"] is True
    shared_delete.assert_awaited_once_with(database, "12")

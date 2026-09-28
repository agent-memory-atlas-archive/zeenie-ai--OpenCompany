"""The core ``workflow_ops_apply`` push (services/workflow_ops.py): a typed
CloudEvents factory whose data is the flat frame the editor's listener
reads, ``persisted`` for batches the server already saved, and the op
fields those batches carry (server node and edge ids, node data,
conditions). Core never reaches into the Agent Builder plugin for it."""

from __future__ import annotations

import inspect

import services.status_broadcaster as status_broadcaster
from services import workflow_ops


def test_saved_batches_carry_the_server_ids():
    node = workflow_ops.add_node(
        "ctx", "context", {}, label="Context", position={"x": 1, "y": 2}, minted_id="7:context:1", data={"agentNodeId": "7:aiAgent:1"}
    )
    assert node["minted_id"] == "7:context:1" and node["data"] == {"agentNodeId": "7:aiAgent:1"}
    assert "data" not in workflow_ops.add_node("r", "canvas", data={})
    edge = workflow_ops.add_edge("a", "b", edge_id="e-a-b", condition={"field": "result.approved", "operator": "is_true"})
    assert (edge["edge_id"], edge["condition"]) == ("e-a-b", {"field": "result.approved", "operator": "is_true"})
    assert set(workflow_ops.add_edge("a", "b")) == {"type", "source", "target"}


def test_the_event_is_typed_and_its_data_is_the_flat_frame():
    ops = [workflow_ops.add_node("n", "canvas", minted_id="7:canvas:1")]
    event = workflow_ops.workflow_ops_applied(workflow_id="7", caller_node_id="7:aiAgent:1", operations=ops, persisted=True)
    assert (event.type, event.source, event.subject) == ("com.opencompany.workflow.ops.applied", "opencompany://services/workflow_ops", "7")
    assert event.data == {"workflow_id": "7", "caller_node_id": "7:aiAgent:1", "operations": ops, "persisted": True}
    unsaved = workflow_ops.workflow_ops_applied(workflow_id="7", caller_node_id=None, operations=ops)
    assert "persisted" not in unsaved.data


async def test_the_broadcast_sends_the_flat_frame_and_never_raises(monkeypatch):
    frames: list = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    ops = [workflow_ops.set_node_parameters("7:masterSkill:1", {"skills_config": {}})]
    await workflow_ops.broadcast_workflow_ops(workflow_id="7", caller_node_id=None, operations=ops, persisted=True)
    await workflow_ops.broadcast_workflow_ops(workflow_id="7", caller_node_id=None, operations=[], persisted=True)
    assert frames == [
        {"type": "workflow_ops_apply", "data": {"workflow_id": "7", "caller_node_id": None, "operations": ops, "persisted": True}}
    ]

    class Broken:
        async def broadcast(self, message):
            raise RuntimeError("socket gone")

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broken())
    await workflow_ops.broadcast_workflow_ops(workflow_id="7", caller_node_id=None, operations=ops)


def test_core_does_not_import_the_plugin():
    source = inspect.getsource(workflow_ops)
    assert "from nodes" not in source and "import nodes" not in source

"""services/workflow_storage/mutate.py: additions to a saved workflow land in
one transaction (graph, fresh rows, merged rows), a retry replays the
ledger, and open editors get the batch with the server's ids."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.status_broadcaster as status_broadcaster
from services.graph_build import GraphAdditions, NewNode, ParamMerge, context_edge, main_edge, skill_edge, tool_edge
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_storage import mutate

SKILLS = {
    "skill_folder": "assistant",
    "skills_config": {
        "skill": {"enabled": True, "instructions": "", "isCustomized": False, "required": True},
        "my-tone": {"enabled": True, "instructions": "Write warmly.", "isCustomized": False, "description": "How I write."},
    },
}
BOOK = {"enabled": True, "instructions": "Offer real times.", "isCustomized": False, "description": "Offers free times."}


@pytest.fixture
async def database(tmp_path: Path):
    """The real Database on a throwaway SQLite file (the root conftest stubs
    ``core.database``, so the module is loaded privately)."""
    module_name = f"tests._graph_additions_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "core" / "database.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"graph-additions-{uuid.uuid4().hex}.db"
    db = module.Database(
        SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}", database_echo=False, database_pool_size=5, database_max_overflow=5)
    )
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()
        sys.modules.pop(module_name, None)


@pytest.fixture
def announced(monkeypatch):
    """The frames broadcast and the workflows announced as changed."""
    frames: list = []
    changed: list = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    monkeypatch.setattr(mutate, "notify_graph_changed", changed.append)
    return SimpleNamespace(frames=frames, changed=changed)


def _node(node_id: str, node_type: str, label: str) -> dict:
    return {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0}, "data": {"label": label}}


async def _saved_employee(database) -> None:
    """Maya: a Chat trigger, her agent, a Skills node, and a calendar tool an
    older writer added under a non-canonical id."""
    graph = {
        "graphVersion": 2,
        "owner_id": "owner",
        "nodes": [
            _node("7:chatTrigger:1", "chatTrigger", "Chat"),
            _node("7:aiAgent:1", "aiAgent", "Maya"),
            _node("7:masterSkill:1", "masterSkill", "Skills"),
            _node("googleCalendar-1727-abc", "googleCalendar", "Calendar"),
        ],
        "edges": [
            main_edge("7:chatTrigger:1", "7:aiAgent:1").to_dict(),
            skill_edge("7:masterSkill:1", "7:aiAgent:1").to_dict(),
            tool_edge("googleCalendar-1727-abc", "7:aiAgent:1").to_dict(),
        ],
    }
    assert await database.save_workflow(workflow_id="7", name="Maya", slug="Maya_1", data=graph)
    assert await database.save_node_parameters("7:masterSkill:1", SKILLS)
    # Left behind by a node deleted earlier under the id the new tool gets.
    assert await database.save_node_parameters("7:googleCalendar:2", {"operation": "stale", "calendar_id": "old"})


ADDITIONS = GraphAdditions(
    nodes=(
        NewNode("calendar", "googleCalendar", "Calendar", {"operation": "list"}, position=(500, 440)),
        NewNode("talk", "aiAgent", "Talk with Maya", {"prompt": "{{chat.message}}"}, position=(360, 600)),
        NewNode("context", "context", "Context", position=(360, 420), context_of="talk"),
    ),
    edges=(
        tool_edge("calendar", "7:aiAgent:1"),
        tool_edge("calendar", "talk"),
        skill_edge("7:masterSkill:1", "talk"),
        main_edge("7:chatTrigger:1", "talk", {"field": "result.response", "operator": "neq", "value": "NO_REPLY"}),
    ),
    merges=(ParamMerge("7:masterSkill:1", {"skills_config": {"book-appointments": BOOK}}),),
)


async def test_additions_land_in_one_transaction(database, announced):
    await _saved_employee(database)
    result = await mutate.apply_graph_additions(database, "7", ADDITIONS, mutation_id="test:1", caller_node_id="7:aiAgent:1")

    assert result is not None and result.applied is True
    # Canonical ids, numbered past what canonicalizing the graph gives the
    # older calendar id (it becomes 7:googleCalendar:1).
    assert result.node_ids == {"calendar": "7:googleCalendar:2", "talk": "7:aiAgent:2", "context": "7:context:1"}
    assert result.labels == {"calendar": "Calendar 2", "talk": "Talk with Maya", "context": "Context"}
    assert result.label_keys == {"calendar": "calendar2", "talk": "talkwithmaya", "context": "context"}

    saved = (await database.get_workflow("7")).data
    assert [node["id"] for node in saved["nodes"]][-3:] == ["7:googleCalendar:2", "7:aiAgent:2", "7:context:1"]
    assert saved["nodes"][-1]["data"] == {"label": "Context", "systemManaged": True, "agentNodeId": "7:aiAgent:2"}
    assert len(saved["edges"]) == 3 + 5 and saved["owner_id"] == "owner"
    normalization = normalize_workflow_graph("7", saved["nodes"], saved["edges"])
    assert not set(result.node_ids.values()) & set(normalization.aliases)

    # A fresh row for each new node (the stale one is gone), a merge into the existing Skills row.
    assert await database.get_node_parameters("7:googleCalendar:2") == {"operation": "list"}
    assert await database.get_node_parameters("7:aiAgent:2") == {"prompt": "{{chat.message}}"}
    skills = await database.get_node_parameters("7:masterSkill:1")
    assert list(skills["skills_config"]) == ["skill", "my-tone", "book-appointments"]

    [frame] = announced.frames
    assert frame["type"] == "workflow_ops_apply"
    data = frame["data"]
    assert (data["workflow_id"], data["caller_node_id"], data["persisted"]) == ("7", "7:aiAgent:1", True)
    assert data["operations"] == list(result.operations)
    [calendar, talk, context] = [op for op in data["operations"] if op["type"] == "add_node"]
    assert calendar == {
        "type": "add_node",
        "client_ref": "calendar",
        "node_type": "googleCalendar",
        "parameters": {"operation": "list"},
        "label": "Calendar 2",
        "position": {"x": 500, "y": 440},
        "minted_id": "7:googleCalendar:2",
    }
    assert context["data"] == {"systemManaged": True, "agentNodeId": "7:aiAgent:2"}
    edges = [op for op in data["operations"] if op["type"] == "add_edge"]
    assert [op["edge_id"] for op in edges] == [edge["id"] for edge in saved["edges"][3:]]
    assert edges[-1]["condition"] == {"field": "result.response", "operator": "neq", "value": "NO_REPLY"}
    [merged] = [op for op in data["operations"] if op["type"] == "set_node_parameters"]
    assert merged == {"type": "set_node_parameters", "node_id": "7:masterSkill:1", "parameters": skills}
    assert announced.changed == ["7"]


async def test_a_retry_replays_the_ledger(database, announced):
    await _saved_employee(database)
    first = await mutate.apply_graph_additions(database, "7", ADDITIONS, mutation_id="test:retry")
    graph = (await database.get_workflow("7")).data
    skills = await database.get_node_parameters("7:masterSkill:1")

    again = await mutate.apply_graph_additions(database, "7", ADDITIONS, mutation_id="test:retry")

    assert again.applied is False
    assert (again.node_ids, again.labels, list(again.operations)) == (first.node_ids, first.labels, list(first.operations))
    assert (await database.get_workflow("7")).data == graph
    assert await database.get_node_parameters("7:masterSkill:1") == skills
    # Announced again: the first announcement may never have gone out.
    assert len(announced.frames) == 2 and announced.frames[0] == announced.frames[1]
    assert announced.changed == ["7", "7"]


async def test_nothing_changes_for_a_batch_that_does_not_fit(database, announced):
    await _saved_employee(database)
    graph = (await database.get_workflow("7")).data
    broken = GraphAdditions(
        nodes=(NewNode("calendar", "googleCalendar", "Calendar", {"operation": "list"}),),
        edges=(context_edge("calendar", "7:aiAgent:9"),),
    )
    with pytest.raises(ValueError):
        await mutate.apply_graph_additions(database, "7", broken, mutation_id="test:broken")
    assert (await database.get_workflow("7")).data == graph
    assert await database.get_node_parameters("7:googleCalendar:2") == {"operation": "stale", "calendar_id": "old"}
    assert announced.frames == [] and announced.changed == []


async def test_a_missing_workflow_gets_nothing(database, announced):
    assert await mutate.apply_graph_additions(database, "404", ADDITIONS, mutation_id="test:missing") is None
    assert announced.frames == [] and announced.changed == []


async def test_a_batch_that_adds_nothing_announces_nothing(database, announced):
    await _saved_employee(database)
    result = await mutate.apply_graph_additions(
        database,
        "7",
        GraphAdditions(edges=(skill_edge("7:masterSkill:1", "7:aiAgent:1"),), merges=(ParamMerge("7:masterSkill:1", SKILLS),)),
        mutation_id="test:noop",
    )
    assert result.operations == [] and result.node_ids == {}
    assert announced.frames == [] and announced.changed == []

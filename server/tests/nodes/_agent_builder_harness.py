"""Shared fixtures for the Agent Builder tests: a real database, the frames
it announces, and helpers to save a graph and call an operation the way
the agent loop does.

Test modules import the fixture functions (``database_fixture``,
``builder_fixture``); pytest finds them in a module's globals and
registers them as ``database`` and ``builder``.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import services.status_broadcaster as status_broadcaster
from nodes.tool import agent_builder as ab
from services.graph_build import tool_edge
from services.plugin import NodeContext
from services.workflow_storage import mutate

WORKFLOW_ID = "7"

#: The real ``core.database`` module and a migrated database file, made once
#: per session: creating the schema takes most of a second, copying it none.
_session: Dict[str, Any] = {}


def _settings(path: Path) -> SimpleNamespace:
    return SimpleNamespace(database_url=f"sqlite+aiosqlite:///{path.as_posix()}", database_echo=False, database_pool_size=5, database_max_overflow=5)


def _database_module() -> ModuleType:
    """The real module (the root conftest stubs ``core.database``), loaded
    privately."""
    if "module" not in _session:
        name = "tests._agent_builder_database"
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / "core" / "database.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _session["module"] = module
    return _session["module"]


@pytest.fixture(name="database")
async def database_fixture(tmp_path: Path, tmp_path_factory):
    """The real Database on a throwaway SQLite file."""
    module = _database_module()
    if "template" not in _session:
        template = tmp_path_factory.mktemp("agent-builder") / "template.db"
        db = module.Database(_settings(template))
        await db.startup()
        await db.shutdown()
        _session["template"] = template
    path = tmp_path / "agent-builder.db"
    shutil.copyfile(_session["template"], path)
    db = module.Database(_settings(path))
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()


@pytest.fixture(name="builder")
def builder_fixture(database, monkeypatch):
    """Wires the plugin to ``database`` and records what it announces:
    ``frames`` (``workflow_ops_apply`` broadcasts) and ``changed`` (graph
    listeners). ``connected`` is the connected app ids policy sees."""
    state = SimpleNamespace(frames=[], changed=[], connected=[])

    class Broadcaster:
        async def broadcast(self, message):
            state.frames.append(message)

    async def connected_apps():
        return list(state.connected)

    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database, auth_service=lambda: SimpleNamespace()))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    monkeypatch.setattr(mutate, "notify_graph_changed", state.changed.append)
    monkeypatch.setattr(ab, "_connected_apps", connected_apps)
    return state


def node(node_id: str, node_type: str, label: str, x: float = 0, y: float = 0) -> Dict[str, Any]:
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "data": {"label": label}}


def agents_graph() -> Dict[str, Any]:
    """Two agents sharing one Agent Builder: 7:aiAgent:1 ("Maya") and
    7:aiAgent:2 ("Talk with Maya")."""
    return {
        "nodes": [
            node("7:chatTrigger:1", "chatTrigger", "Chat"),
            node("7:aiAgent:1", "aiAgent", "Maya", 360, 200),
            node("7:aiAgent:2", "aiAgent", "Talk with Maya", 360, 600),
            node("7:agentBuilder:1", "agentBuilder", "Agent Builder"),
        ],
        "edges": [
            tool_edge("7:agentBuilder:1", "7:aiAgent:1").to_dict(),
            tool_edge("7:agentBuilder:1", "7:aiAgent:2").to_dict(),
        ],
    }


async def save_graph(database, graph: Dict[str, Any], parameters: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    assert await database.save_workflow(workflow_id=WORKFLOW_ID, name="Maya", slug="Maya_1", data=graph)
    for node_id, params in (parameters or {}).items():
        assert await database.save_node_parameters(node_id, params)


async def saved(database) -> Dict[str, Any]:
    return (await database.get_workflow(WORKFLOW_ID)).data


def ctx(
    caller: Optional[str] = "7:aiAgent:1",
    *,
    run: Optional[Dict[str, Any]] = None,
    call: str = "call-1",
    execution: str = "run-1",
    invoking: bool = False,
    **raw: Any,
) -> NodeContext:
    """The context an agent's tool call builds: the caller as the runtime
    names it (Temporal's ``invoking_agent_node_id`` when ``invoking``, else
    ``parent_node_id``), the provider call id, and the run's own canvas."""
    run = run or {"nodes": [], "edges": []}
    fields: Dict[str, Any] = {"workflow_id": WORKFLOW_ID, "tool_call_id": call, "execution_id": execution, **raw}
    if caller:
        fields["invoking_agent_node_id" if invoking else "parent_node_id"] = caller
    return NodeContext(
        node_id="7:agentBuilder:1",
        node_type="agentBuilder",
        workflow_id=WORKFLOW_ID,
        execution_id=execution,
        nodes=list(run["nodes"]),
        edges=list(run["edges"]),
        raw=fields,
    )


async def call(operation: str, context: NodeContext, **fields: Any) -> ab.AgentBuilderOutput:
    node = ab.AgentBuilderNode()
    params = ab.AgentBuilderParams(operation=operation, **fields)
    return await getattr(node, operation)(context, params)


def edges_into(graph: Dict[str, Any], target: str, handle: str) -> List[Dict[str, Any]]:
    return [edge for edge in graph["edges"] if edge["target"] == target and edge["targetHandle"] == handle]

"""The graph-changed hook: an editor save and a server-side addition tell
the registered listeners, a failing listener never fails the save, and
Normal mode's employees refresh through the coalesced path."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.workflow_storage import handlers, listeners


@pytest.fixture
def isolated_listeners(monkeypatch):
    monkeypatch.setattr(listeners, "_LISTENERS", [])
    return listeners


def test_listeners_hear_each_change_once_and_a_failure_is_contained(isolated_listeners):
    heard: list = []

    def broken(_workflow_id):
        raise RuntimeError("listener bug")

    isolated_listeners.register_graph_listener(broken)
    isolated_listeners.register_graph_listener(heard.append)
    isolated_listeners.register_graph_listener(heard.append)
    isolated_listeners.notify_graph_changed("7")
    assert heard == ["7"]


def test_employees_refresh_through_the_coalesced_path():
    import services.employees  # noqa: F401 - registers the listener
    from services.employees import events

    assert events.employee_changed in listeners._LISTENERS
    assert events.employee_changed_now not in listeners._LISTENERS


@pytest.fixture
async def database(tmp_path: Path):
    module_name = f"tests._graph_listener_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "core" / "database.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"graph-listeners-{uuid.uuid4().hex}.db"
    db = module.Database(
        SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}", database_echo=False, database_pool_size=5, database_max_overflow=5)
    )
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()
        sys.modules.pop(module_name, None)


def _agent_with_contexts(count: int) -> dict:
    nodes = [{"id": "agent", "type": "aiAgent", "position": {"x": 0, "y": 0}, "data": {"label": "Maya"}}]
    edges = []
    for n in range(count):
        nodes.append({"id": f"context-{n}", "type": "context", "position": {"x": 0, "y": 0}, "data": {"label": f"Context {n}"}})
        edges.append({"id": f"e-{n}", "source": f"context-{n}", "sourceHandle": "output-context", "target": "agent", "targetHandle": "input-context"})
    return {"nodes": nodes, "edges": edges}


async def test_an_editor_save_tells_the_listeners(monkeypatch, database):
    changed: list = []
    monkeypatch.setattr(handlers, "container", SimpleNamespace(database=lambda: database))
    monkeypatch.setattr(handlers, "notify_graph_changed", changed.append)

    saved = await handlers.handle_save_workflow({"workflow_id": "new", "name": "Maya", "data": _agent_with_contexts(1)}, None)
    assert saved["success"] is True
    assert changed == [saved["workflow_id"]]

    # A save the Context rules refuse writes nothing, so it changes nothing.
    refused = await handlers.handle_save_workflow(
        {"workflow_id": saved["workflow_id"], "name": "Maya", "data": _agent_with_contexts(2)}, None
    )
    assert refused["error"] == "invalid_context_topology"
    assert changed == [saved["workflow_id"]]

"""services/deployment/restart.py: a restart on the saved graph ends in the
right state for each control state, and ``pending_changes`` sees what
would run differently but not where nodes sit."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.container as container_module
from services.deployment import restart
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_runtime_payload, sanitize_workflow_graph


def _control(status: str, revision: int = 4, **fields):
    values = dict(
        id="workflow-control:7:2",
        workflow_id="7",
        generation=2,
        execution_id="e2",
        root_execution_id="e2",
        data_scope_id="e2",
        controller_workflow_id="workflow-control-7-g2",
        controller_run_id="run",
        status=status,
        revision=revision,
        created_at=None,
        updated_at=None,
        terminal_reason=None,
        graph_snapshot={},
    )
    values.update(fields)
    return SimpleNamespace(**values)


@pytest.fixture
def plane(monkeypatch):
    """The control plane restart drives: the latest control, the controls
    started under a key, and the Reset / Start it calls."""
    state = SimpleNamespace(latest=None, by_key={}, reset=AsyncMock(return_value={"success": True}), start=AsyncMock(return_value={"success": True}))

    async def latest(_workflow_id):
        return state.latest

    async def by_key(_workflow_id, key):
        return state.by_key.get(key)

    database = SimpleNamespace(get_latest_workflow_control=latest, get_workflow_control_by_idempotency_key=by_key)
    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: database))
    monkeypatch.setattr(restart, "handle_reset_workflow", state.reset)
    monkeypatch.setattr(restart, "start_saved_workflow", state.start)
    return state


async def test_a_running_workflow_restarts_on_the_saved_graph(plane):
    plane.latest = _control("running", revision=4)
    result = await restart.restart_with_latest_graph("7", owner_id="owner", key="k1")
    assert (result.outcome, result.error) == ("restarted", None)
    plane.reset.assert_awaited_once_with({"workflow_id": "7", "expected_revision": 4}, None)
    plane.start.assert_awaited_once_with("7", owner_id="owner", idempotency_key="restart:k1")


@pytest.mark.parametrize("status", ["paused", "failed"])
async def test_a_paused_or_failed_workflow_is_reset_and_ends_ready(plane, status):
    plane.latest = _control(status)
    result = await restart.restart_with_latest_graph("7", owner_id="owner", key="k1")
    assert (result.outcome, result.error) == ("reset", None)
    plane.reset.assert_awaited_once()
    plane.start.assert_not_awaited()


@pytest.mark.parametrize("latest", [None, _control("reset")])
async def test_nothing_restarts_when_nothing_was_started(plane, latest):
    plane.latest = latest
    result = await restart.restart_with_latest_graph("7", owner_id="owner", key="k1")
    assert (result.outcome, result.error) == ("unchanged", None)
    plane.reset.assert_not_awaited()
    plane.start.assert_not_awaited()


@pytest.mark.parametrize("status", ["starting", "pausing", "resuming", "resetting"])
async def test_a_transition_under_way_is_a_conflict(plane, status):
    plane.latest = _control(status)
    result = await restart.restart_with_latest_graph("7", owner_id="owner", key="k1")
    assert (result.error, result.detail) == ("conflict", status)
    plane.reset.assert_not_awaited()


@pytest.mark.parametrize(
    "reset, start, error",
    [
        ({"success": False, "error": "control_revision_conflict"}, None, "conflict"),
        ({"success": False, "error": "workflow_local_cleanup_failed:boom"}, None, "restart_failed"),
        ({"success": True}, {"success": False, "error": "validation_failed"}, "restart_failed"),
        ({"success": True}, {"success": False, "error": "workflow_already_started"}, "conflict"),
    ],
)
async def test_failures_map_to_conflict_or_restart_failed(plane, reset, start, error):
    plane.latest = _control("running")
    plane.reset.return_value = reset
    if start is not None:
        plane.start.return_value = start
    result = await restart.restart_with_latest_graph("7", owner_id="owner", key="k1")
    assert result.error == error and result.detail == (start or reset)["error"]


async def test_the_same_key_again_does_not_restart_twice(plane):
    # The first attempt reset and started generation 3 under restart:k1.
    plane.latest = _control("running", revision=9, generation=3)
    plane.by_key["restart:k1"] = plane.latest
    result = await restart.restart_with_latest_graph("7", owner_id="owner", key="k1")
    assert result.outcome == "restarted"
    plane.reset.assert_not_awaited()
    plane.start.assert_awaited_once_with("7", owner_id="owner", idempotency_key="restart:k1")


# ----- pending changes -----


def _saved() -> dict:
    condition = {"field": "result.response", "operator": "neq", "value": "NO_REPLY"}
    return {
        "graphVersion": 2,
        "owner_id": "owner",
        "nodes": [
            {"id": "7:chatTrigger:1", "type": "chatTrigger", "position": {"x": 0, "y": 200}, "data": {"label": "Chat"}},
            {"id": "7:aiAgent:1", "type": "aiAgent", "position": {"x": 360, "y": 200}, "data": {"label": "Maya"}},
            {"id": "7:console:1", "type": "console", "position": {"x": 720, "y": 420}, "data": {"label": "Activity log"}},
        ],
        "edges": [
            {"id": "e-1", "source": "7:chatTrigger:1", "sourceHandle": "output-main", "target": "7:aiAgent:1", "targetHandle": "input-main"},
            {
                "id": "e-2",
                "source": "7:aiAgent:1",
                "sourceHandle": "output-main",
                "target": "7:console:1",
                "targetHandle": "input-main",
                "data": {"condition": condition},
            },
        ],
    }


def _started_on(graph: dict, status: str = "running"):
    """The control Start admits for ``graph`` (handle_start_workflow's
    normalize + sanitize, then begin_generation's snapshot)."""
    normalization = normalize_workflow_graph("7", deepcopy(graph["nodes"]), deepcopy(graph["edges"]))
    safe = sanitize_workflow_graph(normalization.graph_data())
    snapshot = sanitize_runtime_payload({"graphVersion": 2, "owner_id": "owner", "nodes": safe["nodes"], "edges": safe["edges"]})
    return _control(status, graph_snapshot=snapshot)


def _workflow(graph: dict):
    return SimpleNamespace(id="7", data=graph)


@pytest.mark.parametrize("status", sorted(restart.LIVE_STATES))
def test_the_graph_the_generation_runs_has_nothing_pending(status):
    assert restart.pending_changes(_workflow(_saved()), _started_on(_saved(), status)) is False


def test_nothing_is_pending_without_a_live_generation():
    changed = _saved()
    changed["nodes"][1]["data"]["label"] = "Nora"
    assert restart.pending_changes(_workflow(changed), None) is False
    for status in ("reset", "failed", "resetting"):
        assert restart.pending_changes(_workflow(changed), _started_on(_saved(), status)) is False


def test_moving_nodes_or_reordering_the_graph_is_not_a_change():
    control = _started_on(_saved())
    moved = _saved()
    moved["nodes"][1]["position"] = {"x": 999, "y": -40}
    moved["nodes"].reverse()
    moved["edges"].reverse()
    moved["edges"][0]["id"] = "reactflow__edge-something-else"
    moved["nodes"][0]["data"]["label"] = "Activity  Log"  # same template key
    assert restart.pending_changes(_workflow(moved), control) is False


def test_an_id_start_canonicalizes_is_not_a_change():
    legacy = _saved()
    legacy["nodes"].append({"id": "canvas-1727-abc", "type": "canvas", "position": {"x": 0, "y": 0}, "data": {"label": "Canvas"}})
    assert restart.pending_changes(_workflow(legacy), _started_on(legacy)) is False


def _edit(change):
    graph = _saved()
    change(graph)
    return graph


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda g: g["edges"].append({**g["edges"][0], "id": "e-3", "target": "7:console:1"}), id="an added edge"),
        pytest.param(lambda g: g["nodes"][1]["data"].update(label="Nora"), id="a renamed label"),
        pytest.param(lambda g: g["nodes"][2]["data"].update(disabled=True), id="a disabled node"),
        pytest.param(lambda g: g["edges"][1]["data"]["condition"].update(value="SKIP"), id="a changed condition"),
        pytest.param(lambda g: g["edges"][1]["data"].pop("condition"), id="a dropped condition"),
        pytest.param(lambda g: g["edges"][0].update(targetHandle="input-tools"), id="a changed handle"),
        pytest.param(
            lambda g: g["nodes"].append({"id": "7:canvas:1", "type": "canvas", "position": {"x": 0, "y": 0}, "data": {"label": "Canvas"}}),
            id="an added node",
        ),
        pytest.param(lambda g: g["nodes"].pop(), id="a removed node"),
    ],
)
def test_a_change_to_what_runs_is_pending(change):
    assert restart.pending_changes(_workflow(_edit(change)), _started_on(_saved())) is True

"""Restarting a deployment on its latest saved graph, and telling whether
that would change anything.

A controlled generation runs the graph admitted at Start
(``control.graph_snapshot``): node parameters are read live, but which
nodes run and how they connect is fixed. ``restart_with_latest_graph`` puts
a saved change into effect, by the latest control's state:

- running: Reset, then Start again from the saved graph; ends running;
- paused or failed: Reset; ends ready, and the next Start takes the saved
  graph;
- ready or never started: nothing to restart;
- starting, pausing, resuming or resetting: a conflict.

Reset clears the workflow's Context conversations and drops events queued
while paused, as it does from the editor.

``pending_changes`` tells whether the saved graph differs from the live
generation's snapshot in anything a run takes from it: its nodes (id, type,
label key, disabled) and edges (ends, handles, condition). Positions, edge
ids and list order are not part of that, so moving a node in the editor is
not a pending change; ``control.graph_hash`` covers them, which is why it
cannot answer this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from core.logging import get_logger
from services.deployment.control import serialize_control
from services.deployment.handlers import handle_reset_workflow, start_saved_workflow
from services.graph_build import template_key
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_workflow_graph

logger = get_logger(__name__)

#: Control states whose snapshot is what runs now, or again on Resume.
LIVE_STATES = frozenset({"starting", "running", "pausing", "paused", "resuming"})
#: States a restart must not interrupt.
_TRANSITIONAL_STATES = frozenset({"starting", "pausing", "resuming", "resetting"})
#: Control errors that mean someone else changed the workflow first.
_CONFLICT_ERRORS = frozenset(
    {"control_revision_conflict", "workflow_already_started", "workflow_control_transition_pending", "workflow_start_pending"}
)


@dataclass(frozen=True)
class RestartResult:
    #: "restarted" (running again, on the saved graph), "reset" (ready; the
    #: next Start takes the saved graph) or "unchanged".
    outcome: str
    #: None when it worked; "conflict" (a start, pause, resume or reset is
    #: under way, or won the race) or "restart_failed".
    error: Optional[str] = None
    #: The control plane's own error, for the log.
    detail: Optional[str] = None


def _failed(outcome: str, response: Mapping[str, Any]) -> RestartResult:
    detail = str(response.get("error") or "unknown")
    return RestartResult(outcome, "conflict" if detail in _CONFLICT_ERRORS else "restart_failed", detail)


def _started(response: Mapping[str, Any]) -> RestartResult:
    return RestartResult("restarted") if response.get("success") else _failed("reset", response)


async def restart_with_latest_graph(workflow_id: str, *, owner_id: str, key: str) -> RestartResult:
    """Put the saved graph into effect (see the module docstring). ``key``
    is the caller's idempotency key: the same key again reports the restart
    it already made rather than restarting a second time."""
    from core.container import container

    database = container.database()
    start_key = f"restart:{key}"
    if await database.get_workflow_control_by_idempotency_key(workflow_id, start_key) is not None:
        # This key's restart already reached its Start; that Start's own
        # idempotency reports how it went.
        return _started(await start_saved_workflow(workflow_id, owner_id=owner_id, idempotency_key=start_key))

    control = await database.get_latest_workflow_control(workflow_id)
    state = serialize_control(control)["state"]
    if state in _TRANSITIONAL_STATES:
        return RestartResult("unchanged", "conflict", state)
    if control is None or state == "ready":
        return RestartResult("unchanged")

    reset = await handle_reset_workflow({"workflow_id": workflow_id, "expected_revision": control.revision}, None)
    if not reset.get("success"):
        result = _failed("unchanged", reset)
    elif state != "running":
        result = RestartResult("reset")
    else:
        result = _started(await start_saved_workflow(workflow_id, owner_id=owner_id, idempotency_key=start_key))
    if result.error:
        logger.warning("Restart on the saved graph did not finish", workflow_id=workflow_id, error=result.error, detail=result.detail)
    return result


def _condition(edge: Mapping[str, Any]) -> str:
    data = edge.get("data")
    condition = data.get("condition") if isinstance(data, Mapping) else None
    return json.dumps(condition, sort_keys=True, default=str) if condition else ""


def _signature(nodes: Iterable[Any], edges: Iterable[Any]) -> Tuple[Tuple[Any, ...], Tuple[Any, ...]]:
    node_part = sorted(
        (str(node.get("id") or ""), str(node.get("type") or ""), template_key(node), bool((node.get("data") or {}).get("disabled")))
        for node in nodes
        if isinstance(node, Mapping)
    )
    edge_part = sorted(
        (
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            str(edge.get("sourceHandle") or edge.get("source_handle") or ""),
            str(edge.get("targetHandle") or edge.get("target_handle") or ""),
            _condition(edge),
        )
        for edge in edges
        if isinstance(edge, Mapping)
    )
    return tuple(node_part), tuple(edge_part)


def pending_changes(workflow: Any, control: Any) -> bool:
    """Whether the saved graph would run differently from the live
    generation. The saved graph goes through Start's own normalization
    first (canonical ids included); its parameters are not needed for that,
    so this reads nothing but the two graphs."""
    if control is None or control.status not in LIVE_STATES:
        return False
    data: Dict[str, Any] = getattr(workflow, "data", None) or {}
    normalization = normalize_workflow_graph(
        str(workflow.id),
        [node for node in data.get("nodes") or [] if isinstance(node, dict)],
        [edge for edge in data.get("edges") or [] if isinstance(edge, dict)],
    )
    saved = sanitize_workflow_graph(normalization.graph_data())
    snapshot = control.graph_snapshot or {}
    return _signature(saved["nodes"], saved["edges"]) != _signature(snapshot.get("nodes") or [], snapshot.get("edges") or [])


__all__ = ["LIVE_STATES", "RestartResult", "pending_changes", "restart_with_latest_graph"]

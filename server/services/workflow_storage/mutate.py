"""Adding to a saved workflow from the server, in one transaction.

``apply_graph_additions`` is how the server grows a workflow that someone
may have open in the editor (Turn on Talk, the Agent Builder). One write
transaction (``database.run_runtime_mutation``) reads ``workflow.data``,
places the batch against it (``services.graph_build.add_to_graph``: ids and
labels allocated against the graph as it is at that moment), appends the
nodes and edges, writes a fresh parameter row for each new node and merges
into existing rows where asked (a Skills node's ``skills_config``). The
mutation id keys a ledger row committed in the same transaction, so a retry
returns the first result instead of adding twice.

After the commit the batch goes to every editor as ``workflow_ops_apply``
with ``persisted: true``: the ops carry the server's ids, positions and
parameters, and an editor adopts them without saving anything. Then the
graph-changed listeners run. A retry announces the batch again, since the
first announcement may never have gone out.

Unlike ``save_workflow`` this never replaces the graph, so it cannot drop
what an editor saved meanwhile.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlmodel import select

from core.logging import get_logger
from models.database import NodeParameter, Workflow
from services import workflow_ops
from services.graph_build import GraphAdditions, add_to_graph, label_key, merge_params
from services.workflow_storage.listeners import notify_graph_changed

logger = get_logger(__name__)


@dataclass(frozen=True)
class GraphAdditionsResult:
    #: ref -> node id, label and label key it got.
    node_ids: Mapping[str, str]
    labels: Mapping[str, str]
    label_keys: Mapping[str, str]
    #: The batch as ``workflow_ops`` operations (what was announced).
    operations: Sequence[Mapping[str, Any]]
    #: False when the ledger already had this mutation (a retry).
    applied: bool


class _WorkflowMissing(Exception):
    pass


async def _row(session: Any, node_id: str) -> Optional[NodeParameter]:
    return (await session.execute(select(NodeParameter).where(NodeParameter.node_id == node_id))).scalar_one_or_none()


def _write(session: Any, row: Optional[NodeParameter], node_id: str, parameters: Dict[str, Any]) -> None:
    if row is None:
        session.add(NodeParameter(node_id=node_id, parameters=deepcopy(parameters)))
    else:
        row.parameters = deepcopy(parameters)
        row.updated_at = datetime.now(timezone.utc)


async def apply_graph_additions(
    database: Any,
    workflow_id: str,
    additions: GraphAdditions,
    *,
    mutation_id: str,
    caller_node_id: Optional[str] = None,
) -> Optional[GraphAdditionsResult]:
    """Add ``additions`` to the saved workflow in one transaction, then
    announce them. None when the workflow does not exist (nothing is
    written). ``caller_node_id`` names the agent that asked, if one did.
    Raises ValueError, with nothing written, for a batch that does not fit
    the graph (see ``add_to_graph``)."""

    async def mutate(session: Any) -> Dict[str, Any]:
        workflow = (await session.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
        if workflow is None:
            raise _WorkflowMissing()
        placed = add_to_graph(workflow_id, workflow.data or {}, additions)
        operations: List[Dict[str, Any]] = []
        refs = {node_id: ref for ref, node_id in placed.node_ids.items()}
        for node in placed.nodes:
            data = {key: value for key, value in node["data"].items() if key != "label"}
            operations.append(
                workflow_ops.add_node(
                    refs[node["id"]],
                    node["type"],
                    placed.parameters[node["id"]],
                    label=node["data"]["label"],
                    position=node["position"],
                    minted_id=node["id"],
                    data=data,
                )
            )
        for edge in placed.edges:
            operations.append(
                workflow_ops.add_edge(
                    edge["source"],
                    edge["target"],
                    source_handle=edge["sourceHandle"],
                    target_handle=edge["targetHandle"],
                    edge_id=edge["id"],
                    condition=(edge.get("data") or {}).get("condition"),
                )
            )
        if placed.nodes or placed.edges:
            workflow.data = deepcopy(placed.graph)
            workflow.updated_at = datetime.now(timezone.utc)
        for node_id, parameters in placed.parameters.items():
            # A fresh row: one left behind under a reused id is replaced.
            _write(session, await _row(session, node_id), node_id, parameters)
        for node_id, patch in placed.merges.items():
            row = await _row(session, node_id)
            current = dict(row.parameters or {}) if row is not None else {}
            merged = merge_params(current, patch)
            if merged != current:
                _write(session, row, node_id, merged)
                operations.append(workflow_ops.set_node_parameters(node_id, merged))
        return {
            "node_ids": placed.node_ids,
            "labels": placed.labels,
            "label_keys": {ref: label_key(label) for ref, label in placed.labels.items()},
            "operations": operations,
        }

    try:
        stored, applied = await database.run_runtime_mutation(
            resource_type="workflow",
            resource_id=workflow_id,
            operation="graph_additions",
            mutation_id=mutation_id,
            mutate=mutate,
        )
    except _WorkflowMissing:
        return None
    result = GraphAdditionsResult(
        node_ids=stored["node_ids"],
        labels=stored["labels"],
        label_keys=stored["label_keys"],
        operations=stored["operations"],
        applied=applied,
    )
    if result.operations:
        await workflow_ops.broadcast_workflow_ops(
            workflow_id=workflow_id, caller_node_id=caller_node_id, operations=result.operations, persisted=True
        )
        notify_graph_changed(workflow_id)
    logger.info(
        "Graph additions saved",
        workflow_id=workflow_id,
        nodes=len(result.node_ids),
        operations=len(result.operations),
        applied=applied,
    )
    return result


__all__ = ["GraphAdditionsResult", "apply_graph_additions"]

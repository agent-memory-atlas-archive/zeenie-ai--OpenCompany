"""Server-owned workflow and capability resolution for Workspace plugins."""

from typing import Any


async def resolve_workflow_node(
    principal: str, workflow_id: str, node_id: str, *, workspace_kind: str | None = None
) -> tuple[Any, dict, dict]:
    from services.plugin import NodeUserError
    from services.plugin.deps import get_database
    from services.workspace_capabilities import workspace_nodes

    if not principal or not workflow_id or not node_id:
        raise NodeUserError("Authenticated workflow and node identity required")
    saved = await get_database().get_workflow(workflow_id)
    if saved is None:
        raise NodeUserError("Workflow not found")
    graph = saved.data if hasattr(saved, "data") else saved.get("data", saved)
    owner = str(graph.get("owner_id") or "")
    if owner and owner != principal:
        raise NodeUserError("Workflow access denied")
    matches = [n for n in graph.get("nodes", []) if n.get("id") == node_id]
    if len(matches) != 1:
        raise NodeUserError("Node does not belong to workflow")
    if workspace_kind and not any(n["node_id"] == node_id and n["kind"] == workspace_kind for n in workspace_nodes(graph)):
        raise NodeUserError("Node does not provide this Workspace capability")
    return saved, graph, matches[0]

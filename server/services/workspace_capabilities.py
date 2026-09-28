"""Generic, declarative Workspace discovery. Plugins own runtime and renderers."""

from typing import Any


def workspace_nodes(graph: dict[str, Any]) -> list[dict[str, str]]:
    from services.node_registry import get_node_class

    found = []
    for node in graph.get("nodes", []):
        cls = get_node_class(node.get("type", ""))
        hints = getattr(cls, "ui_hints", {}) or {}
        descriptor = hints.get("workspace") or ({"kind": "browser"} if hints.get("isBrowserPanel") else {})
        kind = descriptor.get("kind")
        if kind and node.get("id"):
            found.append(
                {"kind": kind, "node_id": node["id"], "label": (node.get("data") or {}).get("label") or getattr(cls, "display_name", kind)}
            )
    return found


def is_registered_agent(node_type: str) -> bool:
    from constants import AI_AGENT_TYPES
    from services.node_registry import get_node_class

    cls = get_node_class(node_type)
    # component_kind is a renderer choice (socialSend/socialReceive also use
    # the agent card); delegation must be an explicit execution capability.
    return node_type in AI_AGENT_TYPES or bool(getattr(cls, "supports_delegation", False))

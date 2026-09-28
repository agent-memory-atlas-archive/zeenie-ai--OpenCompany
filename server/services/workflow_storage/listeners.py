"""Graph-changed listeners: who hears that a saved workflow's graph
changed, without the storage layer knowing them.

``notify_graph_changed(workflow_id)`` runs after an editor save
(``save_workflow``) and after an addition made on the server
(``apply_graph_additions``). Normal mode's employees register their summary
refresh, so Home sees a new tool, or a change waiting for a restart, after
any save.

The same shape as the control listeners (services/deployment/control.py):
listeners are synchronous and must not block, so they schedule any I/O. A
failing listener is logged and never fails the save.
"""

from __future__ import annotations

from typing import Callable, List

from core.logging import get_logger

logger = get_logger(__name__)

GraphListener = Callable[[str], None]
_LISTENERS: List[GraphListener] = []


def register_graph_listener(listener: GraphListener) -> None:
    """Call ``listener(workflow_id)`` after every saved graph change.
    Registering the same listener twice is a no-op."""
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


def notify_graph_changed(workflow_id: str) -> None:
    for listener in list(_LISTENERS):
        try:
            listener(workflow_id)
        except Exception:
            logger.warning("Graph listener failed", workflow_id=workflow_id, exc_info=True)


__all__ = ["GraphListener", "notify_graph_changed", "register_graph_listener"]

"""Stop an existing workflow runtime before its saved graph is deleted.

Deletion reuses the control plane's Reset barrier; it must not remove the
graph after a failed shutdown. This module neither deletes data nor starts
a runtime for an employee that has never run.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.logging import get_logger

logger = get_logger(__name__)


def _failure(workflow_id: str, detail: str) -> Dict[str, Any]:
    return {
        "success": False,
        "workflow_id": workflow_id,
        "error": "workflow_shutdown_failed",
        "detail": detail,
    }


async def stop_workflow_for_deletion(database: Any, workflow_id: str) -> Optional[Dict[str, Any]]:
    """Return a failure envelope, or ``None`` when deletion can proceed.

    Call this before ``database.delete_workflow`` and return any failure
    unchanged. Reset owns generation cancellation, cron removal, workspace
    task cleanup and retries of an interrupted ``resetting`` operation.
    Merely having a workspace-capable node does not mean tasks have run:
    with no live generation, only an existing workspace controller needs
    Reset. Looking it up uses an already-connected client, never connects
    or launches Temporal.
    """
    try:
        from core.container import container
        from services.deployment.handlers import handle_cancel_deployment, handle_reset_workflow
        from services.node_invocations import controller_status

        control = await database.get_latest_workflow_control(workflow_id)
        needs_reset = control is not None and control.status != "reset"
        if not needs_reset:
            wrapper = container.temporal_client()
            client = wrapper.client if wrapper is not None else None
            if client is not None:
                needs_reset = await controller_status(workflow_id, client=client) is not None

        if needs_reset:
            # The shared Reset handler obtains its DB from DI. Never silently
            # reset a different database from the one the caller will delete.
            if container.database() is not database:
                return _failure(workflow_id, "workflow_database_mismatch")
            reset = await handle_reset_workflow(
                {"workflow_id": workflow_id, "expected_revision": control.revision if control else 0},
                None,
            )
            if not reset.get("success"):
                return _failure(workflow_id, str(reset.get("error") or "workflow_reset_failed"))

        # Legacy deployments have no durable control row. Reset already
        # tears down controlled deployments; this also catches local-only
        # deployments without starting one for an untouched employee.
        status = container.workflow_service().get_deployment_status(workflow_id)
        if status.get("deployed") or status.get("active_runs", 0):
            if container.database() is not database:
                return _failure(workflow_id, "workflow_database_mismatch")
            cancelled = await handle_cancel_deployment({"workflow_id": workflow_id}, None)
            if not cancelled.get("success"):
                return _failure(
                    workflow_id,
                    str(cancelled.get("error") or cancelled.get("message") or "workflow_cancel_failed"),
                )

        # A Start admitted while shutdown yielded must not be deleted as if
        # the generation we just stopped were still the latest one.
        latest = await database.get_latest_workflow_control(workflow_id)
        if latest is not None and latest.status != "reset":
            return _failure(workflow_id, "control_revision_conflict")
        return None
    except Exception as exc:
        logger.warning("Workflow shutdown before deletion failed", workflow_id=workflow_id, exc_info=True)
        return _failure(workflow_id, str(exc) or type(exc).__name__)


__all__ = ["stop_workflow_for_deletion"]

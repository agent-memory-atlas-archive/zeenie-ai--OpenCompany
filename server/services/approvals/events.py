"""``approval_lifecycle``: something happened to a draft.

Type ``com.opencompany.approval.<stage>``: ``requested``, ``decided`` (sent
or discarded), ``undone``, ``restored``, ``sending``, ``sent``, ``failed``,
``expired``, ``cancelled``. Subject = the approval id. Identity only: never
the message, never who it goes to. The client refetches the drafts it shows
(``list_approvals`` / ``get_approvals``) through the authorized handlers.

Broadcast directly: no Temporal consumer listens for these.
"""

from __future__ import annotations

from services.approvals.listeners import ApprovalChange
from services.events.envelope import WorkflowEvent

WIRE_KEY = "approval_lifecycle"
SOURCE = "opencompany://services/approvals"


def approval_lifecycle_event(change: ApprovalChange) -> WorkflowEvent:
    data = {
        "approval_id": change.approval_id,
        "workflow_id": change.workflow_id,
        "status": change.status,
        "revision": change.revision,
    }
    if change.run_id:
        data["run_id"] = change.run_id
    return WorkflowEvent(
        source=SOURCE,
        type=f"com.opencompany.approval.{change.stage}",
        subject=change.approval_id,
        data=data,
    )


async def broadcast_approval_change(change: ApprovalChange) -> None:
    from services.status_broadcaster import get_status_broadcaster

    event = approval_lifecycle_event(change)
    await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)})


__all__ = ["SOURCE", "WIRE_KEY", "approval_lifecycle_event", "broadcast_approval_change"]

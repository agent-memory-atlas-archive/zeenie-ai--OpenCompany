"""Owner cleanup for native browser claims whose generation was terminated.

Direct Workspace tasks use their own cooperative cancellation barrier. A
generation Reset instead verifies each durable claim against its exact native
Workflow's recorded input before asking its owner to retire that task.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from models.browser_owners import BrowserProfileOwner
from services import browser_owners

_OWNER_CLEANUP_TIMEOUT = 60.0
_NATIVE_TASK_TYPES = {"AgentWorkflow", "PiCodingAgentWorkflow", "MachinaWorkflow"}


async def _recorded_scope(client: Any, task_id: str) -> tuple[Any, dict] | None:
    """Read one pinned start event, including already-terminated executions."""
    try:
        description = await client.get_workflow_handle(task_id).describe()
        if description.workflow_type not in _NATIVE_TASK_TYPES:
            return None
        handle = client.get_workflow_handle(task_id, run_id=description.run_id)
        async for event in handle.fetch_history_events(page_size=1):
            if event.event_type != EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                raise RuntimeError("browser_reset_execution_input_unavailable")
            values = await client.data_converter.decode(
                event.workflow_execution_started_event_attributes.input.payloads
            )
            if len(values) != 1 or not isinstance(values[0], dict):
                raise RuntimeError("browser_reset_execution_input_unavailable")
            return description, values[0]
        raise RuntimeError("browser_reset_execution_input_unavailable")
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            # Compatibility claims use local invocation tokens, not native
            # Workflow IDs; their node lifecycle hooks own that cleanup.
            return None
        raise


async def cleanup_generation_browser_tasks(database: Any, client: Any, control: Any) -> int:
    """Require owner receipts for this generation's terminated native claims.

    Durable task tokens and profile owners supply the destination. Recorded
    Workflow input supplies generation proof; editable graphs and token naming
    conventions never authorize cleanup. Failure propagates so Reset retains
    its admission fence and can safely retry.
    """
    if not browser_owners._persistent(database):
        return 0
    async with database.get_session() as session:
        claims = (await session.execute(select(BrowserProfileOwner).where(
            BrowserProfileOwner.workflow_id == str(control.workflow_id),
            BrowserProfileOwner.task_id.is_not(None),
        ))).scalars().all()
    if claims and client is None:
        raise RuntimeError("browser_reset_temporal_unavailable")
    cleaned = 0
    for claim in claims:
        recorded = await _recorded_scope(client, claim.task_id)
        if recorded is None:
            continue
        description, scope = recorded
        if (str(scope.get("workflow_id")) != str(control.workflow_id)
                or str(scope.get("generation")) != str(control.generation)
                or scope.get("_browser_task_id", claim.task_id) != claim.task_id):
            continue
        if description.status == WorkflowExecutionStatus.RUNNING:
            raise RuntimeError("browser_reset_execution_still_running")
        # Resolve current owner recovery routing for the stored profile, never
        # the node's editable profile selection. Both local and remote cleanup
        # check the exact task token again before interrupting anything.
        binding = await browser_owners.bind_profile(database, claim.profile_id, claim.principal_id)
        result = await asyncio.wait_for(browser_owners.cleanup_browser_task(
            database, binding, claim.task_id, reset=True,
        ), timeout=_OWNER_CLEANUP_TIMEOUT)
        async with database.get_session() as session:
            current = await session.get(BrowserProfileOwner, claim.profile_id)
            if current is not None and current.task_id == claim.task_id:
                raise RuntimeError("browser_reset_owner_cleanup_unacknowledged")
        if result.get("released"):
            cleaned += 1
        # A concurrent terminal cleanup already released this exact claim.
    return cleaned

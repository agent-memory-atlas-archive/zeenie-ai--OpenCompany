"""Approval gate: hold a draft until the owner sends or discards it.

An employee answers through ``agent -> approvalGate -> reply``. The gate
records the agent's draft (who it goes to, what it says), shows it on the
employee's card, and waits. Send lets the draft (or the owner's edit of it)
through once the Undo window has passed; Discard (once Restore can no longer
bring it back), an expired draft or a Reset lets nothing through.

The workflow's Ask first rule is read live (services/approvals/rules.py):
with it off, the draft goes straight through and its row records that it
went without asking (``approved_by: auto``), so the owner still sees it. A
workflow with no rule (one built in the editor) always asks.

The wait is durable. Its whole state is one ``approval_requests`` row,
found again by an idempotency key on every attempt: on Temporal the
activity's own identity (so a retry after a restart re-attaches to the
same draft), in-process the execution, node and this run's inputs. A worker
shutting down mid-wait raises ``NodeWaitInterrupted``, which Temporal
retries rather than failing the run.

The output fails closed: ``text``, ``subject`` and ``recipient`` are empty
unless the draft was approved. An empty draft, or exactly NO_REPLY, never
creates a row.

Not an AI tool: no agent can approve its own draft.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.logging import get_logger
from services.approvals import rules, store, waiter
from services.approvals.contract import APPROVAL_GATE_TYPE, DEFAULT_TIMEOUT_HOURS, NO_REPLY
from services.approvals.listeners import change_of, notify_approval_changed
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue
from services.plugin.base import NodeWaitInterrupted
from services.plugin.scaling import RetryPolicy

logger = get_logger(__name__)

#: How often a wait re-reads its row (a decision in another process, an expiry).
POLL_SECONDS = 10.0
MAX_DRAFT = 20000
MAX_EXCERPT = 2000


def _text(value: Any, limit: int) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    return str(value).strip()[:limit] if isinstance(value, str) else ""


class ApprovalGateParams(BaseModel):
    channel: str = Field(default="", description="The app the reply goes out through")
    recipient: str = Field(default="", description="Who the reply goes to (from the trigger)")
    recipient_label: str = Field(default="", description="How the card names them")
    subject: str = Field(default="", description="Subject line, for email")
    draft: str = Field(default="", description="The message waiting for approval", json_schema_extra={"rows": 4})
    context_excerpt: str = Field(default="", description="The message being answered, for the card")
    max_length: int = Field(default=MAX_DRAFT, ge=1, le=MAX_DRAFT, description="Longest edit the channel accepts")
    timeout_hours: int = Field(default=DEFAULT_TIMEOUT_HOURS, ge=1, le=720, description="Hours before the draft expires")

    model_config = ConfigDict(extra="ignore")

    @field_validator("channel", "recipient_label", "subject", mode="before")
    @classmethod
    def _short(cls, value: Any) -> str:
        return _text(value, 500)

    @field_validator("recipient", mode="before")
    @classmethod
    def _recipient(cls, value: Any) -> str:
        # Trigger ids arrive as ints (Telegram chat ids).
        return _text(value, 500)

    @field_validator("draft", mode="before")
    @classmethod
    def _draft(cls, value: Any) -> str:
        return _text(value, MAX_DRAFT)

    @field_validator("context_excerpt", mode="before")
    @classmethod
    def _excerpt(cls, value: Any) -> str:
        return _text(value, MAX_EXCERPT)


class ApprovalGateOutput(BaseModel):
    approved: bool = False
    #: approved | discarded | expired | cancelled | skipped
    status: str = "skipped"
    skipped: bool = False
    text: str = ""
    subject: str = ""
    recipient: str = ""
    recipient_label: str = ""
    approval_id: Optional[str] = None
    edited: bool = False
    #: It went without asking: Ask first was off.
    automatic: bool = False


def _identity(ctx: NodeContext) -> Tuple[str, str]:
    """(runtime, idempotency key) of this wait."""
    try:
        from temporalio import activity

        if activity.in_activity():
            info = activity.info()
            return "temporal", f"t:{info.workflow_id}:{info.workflow_run_id}:{info.activity_id}"
    except Exception:
        pass
    # In-process the execution id can span several runs of one deployment;
    # this run's inputs tell them apart.
    fingerprint = hashlib.sha256(json.dumps(ctx.outputs or {}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return "local", f"p:{ctx.execution_id or ctx.session_id}:{ctx.node_id}:{fingerprint}"


def _outcome(row: Any) -> ApprovalGateOutput:
    approved = row.status == "approved"
    return ApprovalGateOutput(
        approved=approved,
        status=row.status,
        text=(row.final_text if row.final_text is not None else row.draft_text) if approved else "",
        subject=(row.final_subject or row.subject or "") if approved else "",
        recipient=row.recipient if approved else "",
        recipient_label=row.recipient_label if approved else "",
        approval_id=row.id,
        edited=bool(row.edited) if approved else False,
        automatic=approved and row.approved_by == "auto",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _worker_shutting_down() -> bool:
    try:
        from temporalio import activity

        return activity.in_activity() and activity.is_worker_shutdown()
    except Exception:
        return False


async def _show_waiting(ctx: NodeContext, row: Any) -> None:
    try:
        from services.status_broadcaster import get_status_broadcaster

        await get_status_broadcaster().update_node_status(
            ctx.node_id,
            "waiting",
            {"message": "Waiting for you to check the draft", "approval_id": row.id},
            workflow_id=ctx.workflow_id,
        )
    except Exception:
        logger.debug("Could not show the gate as waiting", exc_info=True)


class ApprovalGateNode(ActionNode):
    type = APPROVAL_GATE_TYPE
    display_name = "Approval"
    subtitle = "Ask before sending"
    group = ("workflow",)
    description = "Holds a draft until you send or discard it from the employee's card."
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Draft", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Approved", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": False}
    usable_as_tool = False
    task_queue = TaskQueue.TRIGGERS_EVENT
    # A draft may wait for days: each attempt runs up to a day, and attempts
    # are unlimited, each re-attaching to the same row. Expiry ends it.
    start_to_close_timeout = timedelta(hours=24)
    retry_policy = RetryPolicy(maximum_attempts=0, initial_interval=timedelta(seconds=2), maximum_interval=timedelta(minutes=1))

    Params = ApprovalGateParams
    Output = ApprovalGateOutput

    @Operation("request")
    async def request(self, ctx: NodeContext, params: ApprovalGateParams) -> ApprovalGateOutput:
        draft = params.draft.strip()
        if not draft or draft == NO_REPLY:
            return ApprovalGateOutput(approved=False, status="skipped", skipped=True)

        from core.container import container

        database = container.database()
        runtime, key = _identity(ctx)
        now = _utcnow()
        fields = {
            "owner_id": ctx.user_id or "owner",
            "workflow_id": ctx.workflow_id or "",
            "node_id": ctx.node_id,
            "generation": int(ctx.raw.get("generation") or 0),
            "execution_id": ctx.execution_id,
            "runtime": runtime,
            "channel": params.channel,
            "recipient": params.recipient,
            "recipient_label": params.recipient_label,
            "subject": params.subject or None,
            "draft_text": draft[: params.max_length],
            "context_excerpt": params.context_excerpt or None,
            "max_length": params.max_length,
            "expires_at": now + timedelta(hours=params.timeout_hours),
        }
        if await store.get_by_key(database, key) is None and await rules.ask_first(database, ctx.workflow_id) is False:
            # Ask first is off: it goes now, and the card says it went.
            fields.update(
                status="approved",
                approved_by="auto",
                decided_at=now,
                consumed_at=now,
                final_text=draft[: params.max_length],
                final_subject=params.subject or None,
            )
        row, created = await store.get_or_create(database, idempotency_key=key, fields=fields)
        if created:
            await notify_approval_changed(change_of(row, "decided" if row.status == "approved" else "requested"))
        if row.status == "pending":
            await _show_waiting(ctx, row)
        row = await self._wait(database, row)
        return _outcome(row)

    async def _wait(self, database: Any, row: Any) -> Any:
        """Wait until the row settles for this gate: approved and past its
        Undo window (then it is handed on), discarded past Restore, expired
        or cancelled."""
        while True:
            now = _utcnow()
            if row.status == "pending":
                expires = store.aware(row.expires_at)
                if expires is not None and now >= expires:
                    try:
                        row = await store.settle(database, row.id, expected_revision=row.revision, status="expired")
                        await notify_approval_changed(change_of(row, "expired"))
                    except store.ApprovalConflict:
                        row = await store.get(database, row.id) or row
                    continue
                wait = POLL_SECONDS
            elif row.status == "approved" and row.consumed_at is None:
                grace = store.aware(row.grace_until)
                if grace is None or now >= grace:
                    try:
                        return await store.transition(
                            database,
                            row.id,
                            expected_revision=row.revision,
                            from_statuses=("approved",),
                            status="approved",
                            values={"consumed_at": now},
                        )
                    except store.ApprovalConflict:
                        row = await store.get(database, row.id) or row
                        continue
                wait = min(POLL_SECONDS, (grace - now).total_seconds())
            elif row.status == "discarded":
                restore = store.aware(row.restore_until)
                if restore is None or now >= restore:
                    return row
                wait = min(POLL_SECONDS, (restore - now).total_seconds())
            else:
                return row
            if _worker_shutting_down():
                raise NodeWaitInterrupted("worker shutting down while a draft waits")
            await waiter.wait_for_change(row.id, max(0.01, wait))
            row = await store.get(database, row.id) or row

    @classmethod
    async def reset_execution_state(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        execution_id: str,
        generation: int,
        graph: dict,
        database,
    ) -> dict:
        """A Reset cancels the workflow's waiting drafts: the runs they
        belonged to are over, and a stale Send must not deliver later."""
        del node_id, execution_id, generation, graph
        cancelled = await store.cancel_pending(database, workflow_id=str(workflow_id))
        for row in cancelled:
            waiter.notify(row.id)
            await notify_approval_changed(change_of(row, "cancelled"))
        return {"reset": True, "cancelled_approvals": len(cancelled)}


__all__ = ["ApprovalGateNode", "ApprovalGateOutput", "ApprovalGateParams"]


# The owner's side (listing and deciding drafts, the approval_lifecycle
# broadcast, the cleanup on workflow delete) lives in services/approvals.

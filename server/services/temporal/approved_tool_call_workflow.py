"""Send a tool call the owner approved (services/approvals/execution.py).

One run per Send (its id names the approval and the revision Send left it
at). It sleeps until the Undo window closes, claims the row at that
revision (an Undo, a newer Send or a cancel moved it, and the run ends with
nothing sent), runs the node's own activity once with no retry, since a
message must never go twice, and records how it went: ``sent``, ``not_sent``
(the node said it did not go out) or ``unknown`` (the activity broke off,
so it may have).

``payload_version`` 1: ``{approval_id, revision, grace_until}``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

BOOKKEEPING_TIMEOUT = timedelta(seconds=30)
#: Bookkeeping retries until it lands: the row must not stay "sending".
BOOKKEEPING_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30), maximum_attempts=0)
SEND_TIMEOUT = timedelta(minutes=10)
SEND_HEARTBEAT = timedelta(minutes=2)
#: Never retried: a retry could send it twice.
SEND_ONCE = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="ApprovedToolCallWorkflow", sandboxed=False)
class ApprovedToolCallWorkflow:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(payload.get("approval_id") or "")
        info = workflow.info()
        token = hashlib.sha256(f"{info.workflow_id}:{info.run_id}".encode()).hexdigest()[:32]

        grace_until = payload.get("grace_until")
        if grace_until:
            due = datetime.fromisoformat(str(grace_until))
            wait = (due - workflow.now()).total_seconds()
            if wait > 0:
                await workflow.sleep(timedelta(seconds=wait))

        claim = await workflow.execute_activity(
            "approvals.claim_send",
            {"approval_id": approval_id, "revision": int(payload.get("revision") or 0), "claim_token": token},
            start_to_close_timeout=BOOKKEEPING_TIMEOUT,
            retry_policy=BOOKKEEPING_RETRY,
        )
        if not claim.get("claimed"):
            return {"sent": False, "reason": claim.get("reason")}

        report: Dict[str, Any] = {"approval_id": approval_id, "claim_token": token}
        try:
            result = await workflow.execute_activity(
                str(claim["activity"]),
                claim["context"],
                start_to_close_timeout=SEND_TIMEOUT,
                heartbeat_timeout=SEND_HEARTBEAT,
                retry_policy=SEND_ONCE,
            )
            if isinstance(result, dict) and result.get("success") is False:
                report.update(success=False, error=str(result.get("error") or "It did not go out."))
            else:
                report.update(success=True)
        except ActivityError as exc:
            cause = exc.cause if exc.cause is not None else exc
            report.update(broke_off=True, error=f"{type(cause).__name__}: {cause}")

        recorded = await workflow.execute_activity(
            "approvals.record_outcome",
            report,
            start_to_close_timeout=BOOKKEEPING_TIMEOUT,
            retry_policy=BOOKKEEPING_RETRY,
        )
        return {"sent": recorded.get("status") == "sent", "outcome": recorded.get("outcome")}


__all__ = ["ApprovedToolCallWorkflow"]

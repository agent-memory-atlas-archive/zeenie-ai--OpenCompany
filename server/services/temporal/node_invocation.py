"""Durable single-node execution with the complete saved graph as context."""

from __future__ import annotations
import asyncio
from datetime import timedelta
from typing import Any
from temporalio import workflow
from temporalio.common import RetryPolicy


# The parent Temporal package registers services on import. Match the other
# framework workflows: orchestration remains deterministic, without re-importing
# that package and its logging/DI dependencies inside the workflow sandbox.
@workflow.defn(sandboxed=False)
class NodeInvocationWorkflow:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.status = "queued"

    @workflow.query
    def describe(self) -> dict:
        return {**self.metadata, "status": self.status}

    @workflow.run
    async def run(self, payload: dict) -> dict:
        self.metadata = {k: payload[k] for k in ("principal", "workflow_id", "node_id", "fingerprint")}
        self.status = "running"
        result: dict = {}
        try:
            result = await workflow.execute_activity(
                payload["activity"],
                payload["context"],
                task_queue=payload["task_queue"],
                start_to_close_timeout=timedelta(seconds=payload["timeout_s"]),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            self.status = "completed" if result.get("success") else "failed"
            return result
        except asyncio.CancelledError:
            self.status = "cancelled"
            raise
        except Exception:
            self.status = "failed"
            raise
        finally:

            async def record() -> None:
                await workflow.execute_activity(
                    "workflow_runs.record_completion",
                    {
                        "workflow_id": payload["workflow_id"],
                        "run_id": f"{workflow.info().workflow_id}:{workflow.info().run_id}",
                        "generation": payload["context"].get("generation", 0),
                        "status": "success" if self.status == "completed" else "failed",
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            try:
                await asyncio.shield(asyncio.create_task(record()))
            except Exception as exc:
                # A summary outage must not convert a completed device task into
                # a failure that a user may replay. Temporal remains authoritative.
                workflow.logger.warning("Workspace run summary failed: %s", type(exc).__name__)

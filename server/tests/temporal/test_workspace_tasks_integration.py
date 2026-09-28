"""Real Temporal admission/Reset protocol using only installed local binaries.

Set TEMPORAL_TEST_CLI to a native Temporal CLI binary when it is not on PATH
or installed by the project's temporal-server npm package. This test never
downloads a server or connects to the application's running Temporal server.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _existing_cli() -> Path | None:
    configured = os.environ.get("TEMPORAL_TEST_CLI")
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    executable = "temporal.exe" if sys.platform == "win32" else "temporal"
    candidates = [Path(__file__).parents[3] / "node_modules" / "temporal-server" / "bin" / executable]
    on_path = shutil.which(executable) or shutil.which("temporal")
    if on_path:
        path = Path(on_path)
        # The npm entry point is a launcher with application database/port
        # defaults; start_local needs its native binary instead.
        candidates.append(path.parent / "node_modules" / "temporal-server" / "bin" / executable)
        if path.suffix.lower() not in {".cmd", ".ps1", ".bat"}:
            candidates.append(path)
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "temporal-server" / "bin" / executable)
    return next((path for path in candidates if path.is_file()), None)


async def _run_gate(cli: str) -> None:
    from temporalio import activity
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import (
        WithStartWorkflowOperation,
        WorkflowExecutionStatus,
        WorkflowUpdateFailedError,
    )
    from temporalio.common import WorkflowIDConflictPolicy
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    from services.temporal.node_invocation import NodeInvocationWorkflow
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow

    token = uuid4().hex
    task_queue = f"workspace-reset-test-{token}"
    controller_id = f"workspace-controller-{token}"
    workflow_id = f"workspace-graph-{token}"
    first_id, second_id = f"device-first-{token}", f"device-second-{token}"
    devices = {
        run_id: SimpleNamespace(
            started=asyncio.Event(), finish=asyncio.Event(), cancelled=asyncio.Event(),
            release_cleanup=asyncio.Event(), cleaned=asyncio.Event(),
        )
        for run_id in (first_id, second_id)
    }

    class FakeActivities:
        def __init__(self):
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()
            self.cleanup_calls = 0
            self.summaries = set()

        @activity.defn(name="test.workspace.device")
        async def device(self, context: dict) -> dict:
            state = devices[context["execution_id"]]
            state.started.set()
            try:
                while not state.finish.is_set():
                    activity.heartbeat("fake device is running")
                    try:
                        await asyncio.wait_for(state.finish.wait(), 0.05)
                    except TimeoutError:
                        pass
                return {"success": True}
            except asyncio.CancelledError:
                state.cancelled.set()
                await state.release_cleanup.wait()
                state.cleaned.set()
                raise

        @activity.defn(name="workflow_runs.record_completion")
        async def record(self, payload: dict) -> dict:
            self.summaries.add(payload["run_id"].split(":", 1)[0])
            return {"recorded": True}

        @activity.defn(name="workspace_tasks.reset_runtime")
        async def reset_runtime(self, payload: dict) -> dict:
            assert payload == {"workflow_id": workflow_id}
            assert devices[first_id].cleaned.is_set()
            assert first_id in self.summaries
            self.cleanup_calls += 1
            self.cleanup_started.set()
            while not self.release_cleanup.is_set():
                activity.heartbeat("fake plugin cleanup is running")
                try:
                    await asyncio.wait_for(self.release_cleanup.wait(), 0.05)
                except TimeoutError:
                    pass
            return {"reset_nodes": ["phone-one"]}

    activities = FakeActivities()

    def submission(run_id: str, epoch: int) -> dict:
        return {
            "workflow_id": workflow_id, "node_id": "phone-one", "principal": "test-owner",
            "fingerprint": run_id, "admission_epoch": epoch,
            "activity": "test.workspace.device", "task_queue": task_queue, "timeout_s": 60,
            "context": {"workflow_id": workflow_id, "execution_id": run_id},
        }

    # An explicit native binary disables SDK downloads. The omitted database
    # filename selects in-memory SQLite, and port=None selects a free port.
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=cli, ip="127.0.0.1", port=None,
        ui=False, dev_server_log_level="error",
    ) as environment:
        client = environment.client

        async def update(method, argument):
            return await client.execute_update_with_start_workflow(
                method, argument, id=uuid4().hex,
                start_workflow_operation=WithStartWorkflowOperation(
                    WorkspaceTaskControllerWorkflow.run, {"workflow_id": workflow_id},
                    id=controller_id, task_queue=task_queue,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                ),
            )

        async def refused(request, error_type):
            with pytest.raises(WorkflowUpdateFailedError) as rejected:
                await update(WorkspaceTaskControllerWorkflow.submit, request)
            assert rejected.value.cause.type == error_type

        async with Worker(
            client, task_queue=task_queue,
            workflows=[WorkspaceTaskControllerWorkflow, NodeInvocationWorkflow],
            activities=[activities.device, activities.record, activities.reset_runtime],
            max_heartbeat_throttle_interval=timedelta(milliseconds=50),
            default_heartbeat_throttle_interval=timedelta(milliseconds=50),
        ):
            controller = client.get_workflow_handle(controller_id)
            reset_task = None
            try:
                assert await update(WorkspaceTaskControllerWorkflow.submit, submission(first_id, 0)) == {
                    "run_id": first_id, "status": "accepted",
                }
                await asyncio.wait_for(devices[first_id].started.wait(), 10)
                assert (await controller.query(WorkspaceTaskControllerWorkflow.describe))["active_count"] == 1

                reset_task = asyncio.create_task(update(WorkspaceTaskControllerWorkflow.reset, "reset-one"))
                await asyncio.wait_for(devices[first_id].cancelled.wait(), 10)
                assert not reset_task.done()
                assert not activities.cleanup_started.is_set()
                assert (await controller.query(WorkspaceTaskControllerWorkflow.describe))["reset_request_id"] == "reset-one"
                await refused(submission(second_id, 1), "WorkspaceResetInProgress")

                devices[first_id].release_cleanup.set()
                await asyncio.wait_for(activities.cleanup_started.wait(), 10)
                assert not reset_task.done()
                state = await controller.query(WorkspaceTaskControllerWorkflow.describe)
                assert state["active_count"] == 0 and state["resetting"] and state["epoch"] == 1
                assert (await client.get_workflow_handle(first_id).describe()).status == WorkflowExecutionStatus.CANCELED
                await refused(submission(second_id, 1), "WorkspaceResetInProgress")

                activities.release_cleanup.set()
                reset_result = {"cancelled": 1, "reset_nodes": ["phone-one"]}
                assert await asyncio.wait_for(reset_task, 10) == reset_result
                await refused(submission(second_id, 0), "WorkspaceAdmissionChanged")
                assert (await update(WorkspaceTaskControllerWorkflow.submit, submission(second_id, 1)))["status"] == "accepted"
                await asyncio.wait_for(devices[second_id].started.wait(), 10)
                # Fresh RPC update ID, same logical reset: never cancel work
                # admitted after that reset or rerun runtime cleanup.
                assert await update(WorkspaceTaskControllerWorkflow.reset, "reset-one") == reset_result
                assert not devices[second_id].cancelled.is_set()
                assert activities.cleanup_calls == 1
                devices[second_id].finish.set()
                assert await asyncio.wait_for(client.get_workflow_handle(second_id).result(), 10) == {"success": True}

                async def registry_empty():
                    while (await controller.query(WorkspaceTaskControllerWorkflow.describe))["active_count"]:
                        await asyncio.sleep(0.05)

                await asyncio.wait_for(registry_empty(), 10)
                history = await controller.fetch_history()
                child_closed = next(event.event_id for event in history.events if
                    event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_CANCELED)
                cleanup_scheduled = next(event.event_id for event in history.events if
                    event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                    and event.activity_task_scheduled_event_attributes.activity_type.name == "workspace_tasks.reset_runtime")
                assert child_closed < cleanup_scheduled
                replay = await Replayer(workflows=[WorkspaceTaskControllerWorkflow]).replay_workflow(history)
                assert replay.replay_failure is None
            finally:
                for device in devices.values():
                    device.finish.set()
                    device.release_cleanup.set()
                activities.release_cleanup.set()
                # Only this in-memory test controller is terminated. Release
                # every fake cleanup gate even after a failed assertion so
                # Worker/server context managers can always stop promptly.
                with suppress(Exception):
                    await controller.terminate("isolated integration test finished")
                if reset_task is not None:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(asyncio.gather(reset_task, return_exceptions=True), 5)


def test_real_temporal_admission_reset_waits_for_child_and_plugin_cleanup():
    cli = _existing_cli()
    if cli is None:
        pytest.skip("Native Temporal CLI unavailable; set TEMPORAL_TEST_CLI (test never downloads it)")
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.temporal.test_workspace_tasks_integration", str(cli)],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


if __name__ == "__main__":
    asyncio.run(_run_gate(sys.argv[1]))

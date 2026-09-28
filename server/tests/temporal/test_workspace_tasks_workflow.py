"""Workspace admission/Reset races using cooperative child handles, no server."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

from services.temporal import workspace_tasks_workflow as module


def payload(run_id="node-invoke-one", *, epoch=0, fingerprint="fingerprint-one"):
    return {
        "workflow_id": "workflow-one",
        "node_id": "phone-one",
        "principal": "owner",
        "fingerprint": fingerprint,
        "activity": "node.mobile_use_agent.v1",
        "task_queue": "android",
        "timeout_s": 10800,
        "admission_epoch": epoch,
        "context": {"workflow_id": "workflow-one", "execution_id": run_id},
    }


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
async def harness(monkeypatch):
    children = []

    async def wait_condition(predicate):
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(module.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(
        is_continue_as_new_suggested=lambda: False,
        get_current_history_length=lambda: 0,
    ))
    cleanup_activity = AsyncMock(return_value={"reset": True})
    monkeypatch.setattr(module.workflow, "execute_activity", cleanup_activity)

    async def new_child(*, fail=False):
        child = SimpleNamespace(
            started=asyncio.Event(), finish=asyncio.Event(),
            cancel_received=asyncio.Event(), cleanup_finished=asyncio.Event(),
        )

        async def execute():
            child.started.set()
            try:
                await child.finish.wait()
                if fail:
                    raise RuntimeError("phone task failed")
                return {"success": True}
            except asyncio.CancelledError:
                child.cancel_received.set()
                # Mirrors WAIT_CANCELLATION_COMPLETED: requesting cancel does
                # not complete the handle while the child is cleaning up.
                await child.cleanup_finished.wait()
                raise

        child.task = asyncio.create_task(execute())
        children.append(child)
        await child.started.wait()
        return child

    controller = module.WorkspaceTaskControllerWorkflow({"workflow_id": "workflow-one"})
    yield SimpleNamespace(
        controller=controller, new_child=new_child, children=children,
        cleanup_activity=cleanup_activity,
    )
    for child in children:
        child.finish.set()
        child.cleanup_finished.set()
    await asyncio.gather(*(child.task for child in children), return_exceptions=True)


async def test_submission_returns_after_start_and_releases_completed_or_failed_slots(harness, monkeypatch):
    controller = harness.controller
    for fail in (False, True):
        child = await harness.new_child(fail=fail)
        start = AsyncMock(return_value=child.task)
        monkeypatch.setattr(module.workflow, "start_child_workflow", start)
        request = payload(f"node-invoke-{fail}")

        assert await controller.submit(request) == {"run_id": request["context"]["execution_id"], "status": "accepted"}
        assert not child.task.done()
        assert controller.describe() == {
            "epoch": 0, "resetting": False, "active_count": 1,
            "reset_request_id": None, "last_reset_request_id": None,
        }
        options = start.await_args.kwargs
        assert options["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
        assert options["cancellation_type"] == module.workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED
        assert options["parent_close_policy"] == module.workflow.ParentClosePolicy.REQUEST_CANCEL
        assert "search_attributes" not in options
        assert start.await_args.args == (module.NodeInvocationWorkflow.run, request)

        child.finish.set()
        await settle()
        assert controller.describe()["active_count"] == 0
        assert await controller.submit(request) == {"run_id": request["context"]["execution_id"], "status": "accepted"}
        start.assert_awaited_once()


async def test_reset_waits_for_every_child_cleanup_and_blocks_admission(harness, monkeypatch):
    controller = harness.controller
    first, second = await harness.new_child(), await harness.new_child()
    monkeypatch.setattr(module.workflow, "start_child_workflow", AsyncMock(side_effect=[first.task, second.task]))
    await controller.submit(payload("first"))
    await controller.submit(payload("second"))

    reset = asyncio.create_task(controller.reset("reset-one"))
    await asyncio.wait_for(first.cancel_received.wait(), 1)
    await asyncio.wait_for(second.cancel_received.wait(), 1)
    assert controller.describe() == {
        "epoch": 1, "resetting": True, "active_count": 2,
        "reset_request_id": "reset-one", "last_reset_request_id": None,
    }
    assert not reset.done()
    harness.cleanup_activity.assert_not_awaited()
    with pytest.raises(ApplicationError) as refused:
        await controller.submit(payload("late", epoch=1))
    assert refused.value.type == "WorkspaceResetInProgress"

    duplicate_reset = asyncio.create_task(controller.reset("reset-one"))
    with pytest.raises(ApplicationError) as concurrent:
        await controller.reset("different-reset")
    assert concurrent.value.type == "WorkspaceResetInProgress"
    first.cleanup_finished.set()
    await settle()
    assert not reset.done()
    assert not duplicate_reset.done()
    harness.cleanup_activity.assert_not_awaited()
    second.cleanup_finished.set()
    assert await asyncio.wait_for(reset, 1) == {"cancelled": 2}
    assert await asyncio.wait_for(duplicate_reset, 1) == {"cancelled": 2}
    assert controller.describe() == {
        "epoch": 1, "resetting": False, "active_count": 0,
        "reset_request_id": None, "last_reset_request_id": "reset-one",
    }
    harness.cleanup_activity.assert_awaited_once()
    assert harness.cleanup_activity.await_args.args == (
        "workspace_tasks.reset_runtime", {"workflow_id": "workflow-one"},
    )
    options = harness.cleanup_activity.await_args.kwargs
    assert options["start_to_close_timeout"] == timedelta(seconds=120)
    assert options["heartbeat_timeout"] == timedelta(seconds=30)
    assert options["retry_policy"].maximum_attempts == 3
    assert options["cancellation_type"] == module.workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED


async def test_reset_fences_no_child_workflow_until_local_runtime_cleanup_finishes(harness, monkeypatch):
    controller = harness.controller
    cleanup_started, finish_cleanup = asyncio.Event(), asyncio.Event()

    async def cleanup(*args, **kwargs):
        cleanup_started.set()
        await finish_cleanup.wait()

    harness.cleanup_activity.side_effect = cleanup
    start = AsyncMock()
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    reset = asyncio.create_task(controller.reset("local-runtime-reset"))
    await asyncio.wait_for(cleanup_started.wait(), 1)
    assert controller.describe() == {
        "epoch": 1, "resetting": True, "active_count": 0,
        "reset_request_id": "local-runtime-reset", "last_reset_request_id": None,
    }
    assert not controller._ready_to_roll_over()
    with pytest.raises(ApplicationError) as refused:
        await controller.submit(payload(epoch=1))
    assert refused.value.type == "WorkspaceResetInProgress"
    start.assert_not_awaited()
    assert not reset.done()
    finish_cleanup.set()
    assert await asyncio.wait_for(reset, 1) == {"cancelled": 0}
    assert not controller.describe()["resetting"]


async def test_cleanup_failure_keeps_fence_and_same_reset_can_retry_without_new_epoch(harness, monkeypatch):
    controller = harness.controller
    child = await harness.new_child()
    monkeypatch.setattr(module.workflow, "start_child_workflow", AsyncMock(return_value=child.task))
    await controller.submit(payload())
    harness.cleanup_activity.side_effect = [RuntimeError("runtime cleanup failed"), {"reset": True}]
    reset = asyncio.create_task(controller.reset("retry-cleanup"))
    await asyncio.wait_for(child.cancel_received.wait(), 1)
    child.cleanup_finished.set()
    with pytest.raises(RuntimeError, match="runtime cleanup failed"):
        await asyncio.wait_for(reset, 1)

    assert controller.describe() == {
        "epoch": 1, "resetting": True, "active_count": 0,
        "reset_request_id": "retry-cleanup", "last_reset_request_id": None,
    }
    assert "retry-cleanup" not in controller._resets
    assert not controller._admission_lock.locked()
    assert not controller._ready_to_roll_over()
    with pytest.raises(ApplicationError) as refused:
        await controller.submit(payload("new-request", epoch=1))
    assert refused.value.type == "WorkspaceResetInProgress"
    with pytest.raises(ApplicationError) as other_reset:
        await controller.reset("different-reset")
    assert other_reset.value.type == "WorkspaceResetInProgress"

    assert await controller.reset("retry-cleanup") == {"cancelled": 1}
    assert controller.describe() == {
        "epoch": 1, "resetting": False, "active_count": 0,
        "reset_request_id": None, "last_reset_request_id": "retry-cleanup",
    }
    assert harness.cleanup_activity.await_count == 2
    assert await controller.reset("retry-cleanup") == {"cancelled": 1}
    assert harness.cleanup_activity.await_count == 2
    next_child = await harness.new_child()
    monkeypatch.setattr(module.workflow, "start_child_workflow", AsyncMock(return_value=next_child.task))
    assert (await controller.submit(payload("new-request", epoch=1)))["status"] == "accepted"


async def test_reset_relays_runtime_reset_nodes_and_remembers_them_for_retries(harness):
    harness.cleanup_activity.return_value = {"reset_nodes": ["phone-one"], "legacy_cancelled": 2}
    expected = {"cancelled": 0, "reset_nodes": ["phone-one"]}
    assert await harness.controller.reset("reset-with-nodes") == expected
    assert await harness.controller.reset("reset-with-nodes") == expected
    harness.cleanup_activity.assert_awaited_once()


async def test_reset_includes_child_whose_start_acknowledgement_is_pending(harness, monkeypatch):
    controller = harness.controller
    child = await harness.new_child()
    starting, acknowledge = asyncio.Event(), asyncio.Event()

    async def start(*args, **kwargs):
        starting.set()
        await acknowledge.wait()
        return child.task

    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    submission = asyncio.create_task(controller.submit(payload()))
    await asyncio.wait_for(starting.wait(), 1)
    assert controller.describe()["active_count"] == 1
    reset = asyncio.create_task(controller.reset("reset-during-start"))
    await settle()
    assert controller.describe()["epoch"] == 1
    assert not reset.done()
    acknowledge.set()
    assert (await asyncio.wait_for(submission, 1))["status"] == "accepted"
    await asyncio.wait_for(child.cancel_received.wait(), 1)
    assert not reset.done()
    child.cleanup_finished.set()
    assert await asyncio.wait_for(reset, 1) == {"cancelled": 1}
    assert controller.describe()["active_count"] == 0


async def test_queued_submission_rechecks_reset_fence_after_waiting_for_start_lock(harness, monkeypatch):
    controller = harness.controller
    child = await harness.new_child()
    starting, acknowledge = asyncio.Event(), asyncio.Event()

    async def start(*args, **kwargs):
        starting.set()
        await acknowledge.wait()
        return child.task

    start_mock = AsyncMock(side_effect=start)
    monkeypatch.setattr(module.workflow, "start_child_workflow", start_mock)
    first = asyncio.create_task(controller.submit(payload("first")))
    await asyncio.wait_for(starting.wait(), 1)
    queued = asyncio.create_task(controller.submit(payload("queued")))
    await settle()
    reset = asyncio.create_task(controller.reset("reset-queued"))
    await settle()
    acknowledge.set()
    await first
    with pytest.raises(ApplicationError) as refused:
        await queued
    assert refused.value.type == "WorkspaceResetInProgress"
    await asyncio.wait_for(child.cancel_received.wait(), 1)
    child.cleanup_finished.set()
    await asyncio.wait_for(reset, 1)
    start_mock.assert_awaited_once()


async def test_repeated_reset_preserves_new_work_and_late_network_admission_is_rejected(harness, monkeypatch):
    controller = harness.controller
    assert await controller.reset("first-reset") == {"cancelled": 0}
    child = await harness.new_child()
    start = AsyncMock(return_value=child.task)
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)

    with pytest.raises(ApplicationError) as stale:
        await controller.submit(payload("late-old-request"))
    assert stale.value.type == "WorkspaceAdmissionChanged"
    start.assert_not_awaited()
    await controller.submit(payload("new-request", epoch=1))
    assert await controller.reset("first-reset") == {"cancelled": 0}
    assert not child.cancel_received.is_set()
    assert controller.describe() == {
        "epoch": 1, "resetting": False, "active_count": 1,
        "reset_request_id": None, "last_reset_request_id": "first-reset",
    }


async def test_duplicate_submission_checks_fingerprint_without_starting_twice(harness, monkeypatch):
    child = await harness.new_child()
    start = AsyncMock(return_value=child.task)
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    await harness.controller.submit(payload())
    assert (await harness.controller.submit(payload()))["status"] == "accepted"
    with pytest.raises(ApplicationError) as changed:
        await harness.controller.submit(payload(fingerprint="different-task"))
    assert changed.value.type == "WorkspaceSubmissionConflict"
    start.assert_awaited_once()


async def test_historical_duplicate_requires_service_verification_and_is_not_cached(harness, monkeypatch):
    start = AsyncMock(side_effect=WorkflowAlreadyStartedError("node-invoke-one", "NodeInvocationWorkflow"))
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    assert await harness.controller.submit(payload()) == {"run_id": "node-invoke-one", "status": "duplicate"}
    assert (await harness.controller.submit(payload(fingerprint="unverified-different")))["status"] == "duplicate"
    assert harness.controller.describe()["active_count"] == 0
    assert harness.controller._submissions == {}
    assert start.await_count == 2


async def test_failed_start_does_not_leak_an_admission_slot(harness, monkeypatch):
    monkeypatch.setattr(module.workflow, "start_child_workflow", AsyncMock(side_effect=RuntimeError("start failed")))
    with pytest.raises(RuntimeError, match="start failed"):
        await harness.controller.submit(payload())
    assert harness.controller.describe()["active_count"] == 0
    assert not harness.controller._admission_lock.locked()
    assert harness.controller._submissions == {}


async def test_active_queue_is_bounded_and_completed_bookkeeping_is_bounded(harness, monkeypatch):
    controller = harness.controller
    children = [await harness.new_child() for _ in range(module.MAX_ACTIVE_TASKS)]
    start = AsyncMock(side_effect=[child.task for child in children])
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    for index in range(module.MAX_ACTIVE_TASKS):
        await controller.submit(payload(f"active-{index}"))
    with pytest.raises(ApplicationError) as full:
        await controller.submit(payload("overflow"))
    assert full.value.type == "WorkspaceTaskQueueFull"
    assert start.await_count == module.MAX_ACTIVE_TASKS
    for child in children:
        child.finish.set()
    await settle()

    for index in range(module.MAX_REMEMBERED_SUBMISSIONS + 2):
        child = await harness.new_child()
        monkeypatch.setattr(module.workflow, "start_child_workflow", AsyncMock(return_value=child.task))
        await controller.submit(payload(f"completed-{index}"))
        child.finish.set()
        await settle()
    assert not controller._active
    assert len(controller._submissions) == module.MAX_REMEMBERED_SUBMISSIONS
    assert "completed-0" not in controller._submissions


async def test_continue_as_new_waits_for_children_and_handlers_and_carries_reset_fence(harness, monkeypatch):
    controller = harness.controller
    for index in range(module.MAX_REMEMBERED_RESETS + 2):
        await controller.reset(f"reset-{index}")
    assert len(controller._resets) == module.MAX_REMEMBERED_RESETS
    child = await harness.new_child()
    monkeypatch.setattr(module.workflow, "start_child_workflow", AsyncMock(return_value=child.task))
    await controller.submit(payload(epoch=controller.describe()["epoch"]))
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(
        is_continue_as_new_suggested=lambda: True,
        get_current_history_length=lambda: 10001,
    ))
    assert not controller._ready_to_roll_over()
    child.finish.set()
    await settle()
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: False)
    assert not controller._ready_to_roll_over()
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)
    carried = {}

    class Continued(BaseException):
        pass

    def continue_as_new(value):
        carried.update(value)
        raise Continued()

    monkeypatch.setattr(module.workflow, "continue_as_new", continue_as_new)
    with pytest.raises(Continued):
        await controller.run({"workflow_id": "workflow-one"})
    assert carried["epoch"] == module.MAX_REMEMBERED_RESETS + 2
    assert len(carried["resets"]) == module.MAX_REMEMBERED_RESETS
    replacement = module.WorkspaceTaskControllerWorkflow(carried)
    assert await replacement.reset(f"reset-{module.MAX_REMEMBERED_RESETS + 1}") == {"cancelled": 0}
    assert replacement.describe()["epoch"] == carried["epoch"]
    assert (await replacement.submit(payload(epoch=carried["epoch"])))["status"] == "accepted"
    assert replacement.describe()["active_count"] == 0  # completed dedup survives rollover


@pytest.mark.parametrize("change", [
    {"workflow_id": "other-workflow"},
    {"context": {"workflow_id": "other-workflow", "execution_id": "other"}},
])
async def test_controller_rejects_cross_workflow_submission(harness, monkeypatch, change):
    start = AsyncMock()
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    with pytest.raises(ApplicationError) as error:
        await harness.controller.submit({**payload(), **change})
    assert error.value.type == "WorkspaceScopeMismatch"
    start.assert_not_awaited()


def test_framework_registers_workspace_task_controller():
    from services.temporal.worker import _framework_workflows

    assert _framework_workflows().count(module.WorkspaceTaskControllerWorkflow) == 1

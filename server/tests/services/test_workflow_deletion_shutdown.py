"""Deletion's precondition delegates shutdown to the existing control plane."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import core.container as container_module
from services.deployment import handlers as deployment
from services import node_invocations
from services.workflow_storage.deletion import stop_workflow_for_deletion


@pytest.fixture
def runtime(monkeypatch):
    state = SimpleNamespace(control=None, calls=[])

    async def latest(_workflow_id):
        return state.control

    async def reset(_data, _socket):
        state.calls.append("reset")
        if state.control is not None:
            state.control = SimpleNamespace(status="reset", revision=state.control.revision + 1)
        state.status["deployed"] = False
        return {"success": True}

    async def cancel(_data, _socket):
        state.calls.append("cancel")
        return {"success": True}

    state.database = SimpleNamespace(get_latest_workflow_control=AsyncMock(side_effect=latest), delete_workflow=AsyncMock())
    state.status = {"deployed": False, "active_runs": 0}
    state.client = SimpleNamespace(client=None)
    state.service = SimpleNamespace(get_deployment_status=Mock(side_effect=lambda _: state.status))
    state.container = SimpleNamespace(
        database=lambda: state.database, temporal_client=lambda: state.client, workflow_service=lambda: state.service,
    )
    state.reset = AsyncMock(side_effect=reset)
    state.cancel = AsyncMock(side_effect=cancel)
    state.workspace = AsyncMock(return_value=None)
    monkeypatch.setattr(container_module, "container", state.container)
    monkeypatch.setattr(deployment, "handle_reset_workflow", state.reset)
    monkeypatch.setattr(deployment, "handle_cancel_deployment", state.cancel)
    monkeypatch.setattr(node_invocations, "controller_status", state.workspace)
    return state


async def test_untouched_hire_needs_no_runtime_reset_or_connection(runtime):
    assert await stop_workflow_for_deletion(runtime.database, "7") is None
    runtime.reset.assert_not_awaited()
    runtime.cancel.assert_not_awaited()
    runtime.workspace.assert_not_awaited()
    runtime.database.delete_workflow.assert_not_awaited()


@pytest.mark.parametrize("status", ["starting", "running", "pausing", "paused", "resuming", "failed", "resetting"])
async def test_live_or_interrupted_generation_uses_reset_barrier_with_latest_revision(runtime, status):
    runtime.control = SimpleNamespace(status=status, revision=9)
    assert await stop_workflow_for_deletion(runtime.database, "7") is None
    runtime.reset.assert_awaited_once_with({"workflow_id": "7", "expected_revision": 9}, None)
    runtime.database.delete_workflow.assert_not_awaited()


@pytest.mark.parametrize("resetting", [False, True])
async def test_existing_workspace_controller_resets_even_without_a_generation(runtime, resetting):
    runtime.client.client = object()
    runtime.workspace.return_value = {"active_count": 0 if resetting else 1, "resetting": resetting}
    assert await stop_workflow_for_deletion(runtime.database, "7") is None
    runtime.workspace.assert_awaited_once_with("7", client=runtime.client.client)
    runtime.reset.assert_awaited_once_with({"workflow_id": "7", "expected_revision": 0}, None)


async def test_absent_workspace_controller_does_not_create_one(runtime):
    runtime.client.client = object()
    assert await stop_workflow_for_deletion(runtime.database, "7") is None
    runtime.reset.assert_not_awaited()


async def test_legacy_local_deployment_uses_existing_cancel(runtime):
    runtime.status["deployed"] = True
    assert await stop_workflow_for_deletion(runtime.database, "7") is None
    runtime.cancel.assert_awaited_once_with({"workflow_id": "7"}, None)
    runtime.reset.assert_not_awaited()


@pytest.mark.parametrize("detail", ["control_revision_conflict", "workflow_local_cleanup_failed:busy", "temporal_unavailable"])
async def test_failed_shutdown_retains_graph_and_returns_actionable_failure(runtime, detail):
    runtime.control = SimpleNamespace(status="running", revision=3)
    runtime.reset.side_effect = None
    runtime.reset.return_value = {"success": False, "error": detail}
    result = await stop_workflow_for_deletion(runtime.database, "7")
    assert result == {"success": False, "workflow_id": "7", "error": "workflow_shutdown_failed", "detail": detail}
    runtime.database.delete_workflow.assert_not_awaited()
    runtime.cancel.assert_not_awaited()


async def test_local_cancel_failure_also_refuses_deletion(runtime):
    runtime.status["deployed"] = True
    runtime.cancel.side_effect = RuntimeError("listener cleanup failed")
    result = await stop_workflow_for_deletion(runtime.database, "7")
    assert result["success"] is False
    assert result["detail"] == "listener cleanup failed"
    runtime.database.delete_workflow.assert_not_awaited()


async def test_failed_workspace_status_query_is_not_treated_as_no_tasks(runtime):
    runtime.client.client = object()
    runtime.workspace.side_effect = RuntimeError("query unavailable")
    result = await stop_workflow_for_deletion(runtime.database, "7")
    assert result["detail"] == "query unavailable"
    runtime.database.delete_workflow.assert_not_awaited()


async def test_reset_cannot_mutate_a_different_container_database(runtime):
    runtime.control = SimpleNamespace(status="running", revision=3)
    runtime.container.database = lambda: object()
    result = await stop_workflow_for_deletion(runtime.database, "7")
    assert result["detail"] == "workflow_database_mismatch"
    runtime.reset.assert_not_awaited()


async def test_legacy_cancel_cannot_target_another_database_runtime(runtime):
    runtime.status["deployed"] = True
    runtime.container.database = lambda: object()
    result = await stop_workflow_for_deletion(runtime.database, "7")
    assert result["detail"] == "workflow_database_mismatch"
    runtime.cancel.assert_not_awaited()


async def test_new_generation_admitted_during_cleanup_refuses_deletion(runtime):
    runtime.status["deployed"] = True

    async def start_during_cancel(*_):
        runtime.control = SimpleNamespace(status="starting", revision=12)
        return {"success": True}

    runtime.cancel.side_effect = start_during_cancel
    result = await stop_workflow_for_deletion(runtime.database, "7")
    assert result["detail"] == "control_revision_conflict"
    runtime.database.delete_workflow.assert_not_awaited()


@pytest.fixture
def deletion_boundary(runtime, monkeypatch):
    from services.workflow_storage import handlers, hooks

    runtime.graph_exists = True

    async def delete(_workflow_id):
        runtime.calls.append("delete")
        runtime.graph_exists = False
        return True

    async def archive(*_):
        runtime.calls.append("archive")
        return 1

    async def drain(*_):
        runtime.calls.append("archive")
        return 1, 0

    boundary = SimpleNamespace(
        handlers=handlers,
        supports_outbox=Mock(return_value=True),
        archive=AsyncMock(side_effect=archive),
        drain=AsyncMock(side_effect=drain),
        hooks=AsyncMock(side_effect=lambda *_: runtime.calls.append("hooks")),
        broadcast=AsyncMock(side_effect=lambda *_: runtime.calls.append("broadcast")),
    )
    runtime.database.delete_workflow.side_effect = delete
    monkeypatch.setattr(handlers, "_supports_context_archive_outbox", boundary.supports_outbox)
    monkeypatch.setattr(handlers, "_archive_workflow_contexts", boundary.archive)
    monkeypatch.setattr(handlers, "_drain_context_archive_outbox", boundary.drain)
    monkeypatch.setattr(hooks, "run_workflow_deleted_hooks", boundary.hooks)
    monkeypatch.setattr(handlers, "_broadcast_lifecycle", boundary.broadcast)
    return boundary


@pytest.mark.parametrize("has_outbox", [True, False])
async def test_delete_handler_does_not_archive_or_delete_when_shutdown_fails(runtime, deletion_boundary, has_outbox):
    runtime.control = SimpleNamespace(status="running", revision=3)
    runtime.reset.side_effect = None
    runtime.reset.return_value = {"success": False, "error": "control_revision_conflict"}
    deletion_boundary.supports_outbox.return_value = has_outbox

    result = await deletion_boundary.handlers.delete_workflow_with_context_archival(runtime.database, "7")

    assert result == {
        "success": False, "workflow_id": "7", "error": "workflow_shutdown_failed", "detail": "control_revision_conflict",
    }
    assert runtime.graph_exists is True
    runtime.reset.assert_awaited_once()
    runtime.database.delete_workflow.assert_not_awaited()
    deletion_boundary.archive.assert_not_awaited()
    deletion_boundary.drain.assert_not_awaited()
    deletion_boundary.hooks.assert_not_awaited()
    deletion_boundary.broadcast.assert_not_awaited()


@pytest.mark.parametrize("has_outbox", [True, False])
async def test_delete_handler_waits_for_shutdown_before_archival_and_deletion(runtime, deletion_boundary, has_outbox):
    runtime.control = SimpleNamespace(status="running", revision=3)
    deletion_boundary.supports_outbox.return_value = has_outbox

    result = await deletion_boundary.handlers.delete_workflow_with_context_archival(runtime.database, "7")

    assert result == {"success": True, "workflow_id": "7", "contexts_archived": 1, "context_archives_pending": 0}
    assert runtime.graph_exists is False
    assert runtime.calls == (
        ["reset", "delete", "archive", "hooks", "broadcast"]
        if has_outbox else ["reset", "archive", "delete", "hooks", "broadcast"]
    )
    deletion_boundary.hooks.assert_awaited_once_with(runtime.database, "7")
    deletion_boundary.broadcast.assert_awaited_once_with("deleted", "7")

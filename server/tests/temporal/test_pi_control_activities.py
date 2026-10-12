from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment

from services.temporal.pi_activities import PiControlActivities, PiDriverActivities


CHILD = {"workflow_id": "child", "first_execution_run_id": "first-run"}


def _request(revision=1):
    return {"children": [CHILD], "operation": "wait_for_checkpoint",
            "control": {"state": "paused", "revision": revision, "generation": 1}}


def _bridge(error=None, status=WorkflowExecutionStatus.RUNNING):
    handle = SimpleNamespace(execute_update=AsyncMock(side_effect=error),
                             describe=AsyncMock(return_value=SimpleNamespace(status=status)))
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    return PiControlActivities(client), client, handle


@pytest.mark.asyncio
async def test_child_checkpoint_update_binds_chain_and_retry_identity():
    bridge, client, handle = _bridge()
    environment = ActivityEnvironment()
    assert await environment.run(bridge.child_control, _request()) == {"closed": []}
    first_id = handle.execute_update.call_args.kwargs["id"]
    client.get_workflow_handle.assert_called_with("child", first_execution_run_id="first-run")
    assert handle.execute_update.call_args.args[1]["first_execution_run_id"] == "first-run"
    await environment.run(bridge.child_control, _request())
    assert handle.execute_update.call_args.kwargs["id"] == first_id
    await environment.run(bridge.child_control, _request(2))
    assert handle.execute_update.call_args.kwargs["id"] != first_id


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,closed", [
    (RPCStatusCode.NOT_FOUND, WorkflowExecutionStatus.RUNNING, True),
    (RPCStatusCode.FAILED_PRECONDITION, WorkflowExecutionStatus.COMPLETED, True),
    (RPCStatusCode.FAILED_PRECONDITION, WorkflowExecutionStatus.RUNNING, False),
    (RPCStatusCode.PERMISSION_DENIED, WorkflowExecutionStatus.COMPLETED, False),
])
async def test_only_missing_or_verified_terminal_children_are_pruned(code, status, closed):
    bridge, _, _ = _bridge(RPCError("private transport detail", code, b""), status)
    if closed:
        assert await ActivityEnvironment().run(bridge.child_control, _request()) == {"closed": ["child"]}
    else:
        with pytest.raises(ApplicationError) as failure:
            await ActivityEnvironment().run(bridge.child_control, _request())
        assert failure.value.non_retryable
        assert "private transport detail" not in str(failure.value)


@pytest.mark.asyncio
async def test_child_generation_conflict_is_terminal_and_not_treated_as_closed():
    bridge, _, _ = _bridge(WorkflowUpdateFailedError(ApplicationError(
        "wrong generation", type="StaleExecutionGeneration", non_retryable=True)))
    with pytest.raises(ApplicationError) as failure:
        await ActivityEnvironment().run(bridge.child_control, _request())
    assert failure.value.type == "StaleExecutionGeneration" and failure.value.non_retryable


@pytest.mark.asyncio
async def test_driver_storage_failure_does_not_serialize_private_sql_parameters():
    from sqlalchemy.exc import OperationalError
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter

    driver = PiDriverActivities(MagicMock())
    driver._drive_turn = AsyncMock(side_effect=OperationalError("insert snapshot", {"credential": "private-test-secret"}, OSError("connection lost")))
    with pytest.raises(ApplicationError) as failure:
        await ActivityEnvironment().run(driver.drive_turn, {})
    assert failure.value.type == "PiTransportFailure" and not failure.value.non_retryable
    wire = Failure()
    DefaultFailureConverter().to_failure(failure.value, DefaultPayloadConverter(), wire)
    assert b"private-test-secret" not in wire.SerializeToString()
    assert not wire.HasField("cause")


def test_host_environment_excludes_credentials_and_ambient_loaders():
    from services.temporal.pi_activities import pi_host_environment

    env = pi_host_environment({"Path": "runtime-path", "SystemRoot": "windows", "TMP": "scratch",
        "DATABASE_URL": "postgres://private", "CUSTOM_CREDENTIALS": "private", "OPENAI_API_KEY": "private",
        "AWS_SESSION_TOKEN": "private", "OP_SERVICE_ACCOUNT_TOKEN": "private",
        "NODE_OPTIONS": "--import ambient-loader.js", "NODE_PATH": "ambient-packages",
        "PI_PACKAGE_DIR": "ambient-sdk", "PI_OFFLINE": "0"})
    assert env == {"Path": "runtime-path", "SystemRoot": "windows", "TMP": "scratch",
        "NODE_PATH": "", "PI_OFFLINE": "1"}

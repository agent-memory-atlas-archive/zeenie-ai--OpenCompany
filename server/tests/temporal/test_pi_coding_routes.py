"""Admission markers select Pi without changing already recorded commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import nodes  # noqa: F401

from services.coding_agent_runtime import (
    agent_workflow_name, assert_pi_admissions_enabled, coding_agent_ancestors,
    freeze_generation_harness, freeze_pi_local_control,
)
from services.temporal.workflow import MachinaWorkflow, _frozen_routing_from_input


def test_coding_agent_public_params_default_connected_and_keep_existing_handles():
    from nodes.agent.coding_agent import CodingAgentNode
    from nodes.agent._specialized import SpecializedAgentBase
    assert CodingAgentNode.Params().tool_mode == "connected"
    assert CodingAgentNode.Params(tool_mode="workspace").tool_mode == "workspace"
    assert CodingAgentNode.handles == SpecializedAgentBase.handles
    assert CodingAgentNode.version == 1  # native v1 callable remains registered
    assert CodingAgentNode.Params.model_json_schema()["properties"]["tool_mode"]["enum"] == ["connected", "workspace"]


def test_pi_dispatch_ignores_agent_and_pool_flags_but_old_snapshots_stay_native():
    flags = {"version": 1, "agent_workflow_enabled": False, "per_type_dispatch_enabled": True, "worker_pool_enabled": False}
    run = MachinaWorkflow()
    assert run._resolve_dispatch("coding_agent", routing_snapshot=flags)["name"] == "node.coding_agent.v1"
    frozen = {**flags, "pi_harness_version": 1}
    with patch("core.config.Settings", side_effect=AssertionError("Mutable settings read during workflow routing")):
        assert run._resolve_dispatch("coding_agent", routing_snapshot=frozen) == {"kind": "child_workflow", "name": "PiCodingAgentWorkflow"}
        assert run._resolve_dispatch("aiAgent", routing_snapshot=frozen)["name"] == "node.aiAgent.v1"
        assert run._resolve_dispatch("aiAgent", routing_snapshot=frozen, pi_delegator=True)["name"] == "AgentWorkflow"


def test_unknown_routing_protocol_cannot_enable_pi():
    value = _frozen_routing_from_input({"_temporal_routing_v1": {"version": 999, "pi_harness_version": 1}})
    assert "pi_harness_version" not in value


def test_unsupported_recorded_pi_version_fails_without_native_fallback():
    with pytest.raises(ValueError, match="Unsupported recorded"):
        agent_workflow_name("coding_agent", {"pi_harness_version": 2})
    with pytest.raises(ValueError, match="Unsupported recorded"):
        _frozen_routing_from_input({"_temporal_routing_v1": {"version": 1, "pi_harness_version": 2}})


def test_recovering_old_generation_removes_fresh_default_marker():
    payload = {"pi_harness_version": 1, "_temporal_routing_v1": {"version": 1, "pi_harness_version": 1}}
    freeze_generation_harness(payload, {"execution_control_version": 1})
    assert "pi_harness_version" not in payload
    assert "pi_harness_version" not in payload["_temporal_routing_v1"]
    freeze_generation_harness(payload, {"pi_harness_version": 1})
    assert agent_workflow_name("coding_agent", payload) == "PiCodingAgentWorkflow"
    assert agent_workflow_name("aiAgent", payload) == "AgentWorkflow"


def test_oneoff_local_control_cannot_claim_a_deployment_or_change_legacy_scope():
    native = {}
    freeze_pi_local_control(native)
    assert native == {}
    admitted = {"pi_harness_version": 1}
    freeze_pi_local_control(admitted)
    assert admitted == {"pi_harness_version": 1, "pi_local_control_version": 1}
    central = {"pi_harness_version": 1, "execution_control_version": 1,
               "controller_workflow_id": "actual-controller", "generation": 4}
    before = dict(central)
    freeze_pi_local_control(central)
    assert central == before


def test_only_configured_ancestors_need_durable_dispatch():
    nodes = [{"id": "coding", "type": "coding_agent"}, {"id": "middle", "type": "aiAgent"}, {"id": "lead", "type": "orchestrator_agent"}, {"id": "unrelated", "type": "aiAgent"}]
    edges = [{"source": "coding", "target": "middle", "targetHandle": "input-tools"},
             {"source": "middle", "target": "lead", "targetHandle": "input-teammates"},
             {"source": "coding", "target": "unrelated", "targetHandle": "input-main"}]
    assert coding_agent_ancestors(nodes, edges) == {"coding", "middle", "lead"}


def test_disabled_admission_is_actionable_and_does_not_select_native():
    with pytest.raises(Exception) as error:
        assert_pi_admissions_enabled(SimpleNamespace(coding_pi_admissions_enabled=False))
    assert error.value.as_dict()["error_type"] == "PiAdmissionDisabled"
    assert error.value.as_dict()["requires_user_action"] is True
    assert agent_workflow_name("coding_agent", {"pi_harness_version": 1}) == "PiCodingAgentWorkflow"


@pytest.mark.asyncio
async def test_node_invocation_starts_pi_child_without_node_activity(monkeypatch):
    from services.temporal import node_invocation as module
    context = {"node_id": "coding", "node_type": "coding_agent", "pi_harness_version": 1}
    payload = {"principal": "owner", "workflow_id": "wf", "node_id": "coding", "fingerprint": "digest", "dispatch_version": 1, "dispatch_kind": "pi_agent", "context": context}
    child = AsyncMock(return_value={"success": True, "result": {"response": "done"}})
    activity = AsyncMock()
    monkeypatch.setattr(module.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(module.workflow, "execute_activity", activity)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="invocation", run_id="attempt"))
    assert (await module.NodeInvocationWorkflow().run(payload))["success"] is True
    assert child.await_args.args == ("PiCodingAgentWorkflow", context)
    assert [call.args[0] for call in activity.await_args_list] == ["workflow_runs.record_completion"]


@pytest.mark.asyncio
async def test_inline_pi_target_fails_before_any_child_work_or_service_lookup():
    from services.handlers.tools import _execute_delegated_agent
    answer = await _execute_delegated_agent({"task": "change files"}, {"node_type": "coding_agent", "pi_harness_version": 1})
    assert answer["error_type"] == "CodingAgentRequiresDurableCaller"
    assert answer["requires_user_action"] is True
    # Existing native callers still reach their original validation path.
    native = await _execute_delegated_agent({"task": "old run"}, {"node_type": "coding_agent"})
    assert "requires ai_service" in native["error"]


def _service(*, temporal=True):
    from services.workflow import WorkflowService
    service = object.__new__(WorkflowService)
    service.settings = SimpleNamespace(temporal_enabled=temporal, temporal_worker_pool_enabled=False,
                                      temporal_task_queue="manager", coding_pi_admissions_enabled=True)
    service.database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={"model": "gpt-test", "prompt": "saved", "api_key": "secret"}))
    handle = SimpleNamespace(result=AsyncMock(return_value={"success": True, "result": {"response": "done"}}))
    client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle), get_workflow_handle=MagicMock(return_value=handle),
                             execute_workflow=AsyncMock(side_effect=AssertionError("manual admission must start/attach")))
    service._temporal_executor = SimpleNamespace(client=client) if temporal else None
    service._node_executor = SimpleNamespace(execute=AsyncMock(return_value={"success": True}))
    service._get_workspace_dir = MagicMock(return_value="managed-workspace")
    service._resolve_workflow_slug = AsyncMock(return_value="wf")
    service._param_resolver = SimpleNamespace(resolve=AsyncMock())
    return service


@pytest.mark.asyncio
async def test_manual_coding_admission_is_direct_and_redacts_secrets():
    service = _service()
    answer = await service.execute_node("coding", "coding_agent", {"prompt": "manual", "tool_mode": "workspace"}, workflow_id="wf", execution_id="request", nodes=[{"id": "coding", "type": "coding_agent"}], edges=[])
    assert answer["success"] is True
    service._node_executor.execute.assert_not_awaited()
    call = service._temporal_executor.client.start_workflow.await_args
    assert call.args[0] == "PiCodingAgentWorkflow"
    context = call.args[1]
    import json
    json.dumps(context)  # no bound output-read callback may cross Temporal
    assert context["pi_harness_version"] == 1
    assert context["pi_local_control_version"] == 1
    assert "execution_control_version" not in context
    assert "controller_workflow_id" not in context
    assert context["parameter_snapshot"]["coding"]["prompt"] == "manual"
    assert context["parameter_snapshot"]["coding"]["tool_mode"] == "workspace"
    assert "api_key" not in context["parameter_snapshot"]["coding"]


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [False, True])
async def test_manual_lost_response_attaches_to_inflight_and_completed_pi_execution(completed):
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError
    service = _service()
    client = service._temporal_executor.client
    first = await service.execute_node("coding", "coding_agent", {"prompt": "edit"}, workflow_id="wf", execution_id="request")
    call = client.start_workflow.await_args
    assert call.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    handle = client.start_workflow.return_value
    description = SimpleNamespace(status="COMPLETED" if completed else "RUNNING", memo=AsyncMock(return_value=call.kwargs["memo"]))
    handle.describe = AsyncMock(return_value=description)
    client.start_workflow.side_effect = WorkflowAlreadyStartedError(call.kwargs["id"], "PiCodingAgentWorkflow", run_id="recorded-run")
    second = await service.execute_node("coding", "coding_agent", {"prompt": "edit"}, workflow_id="wf", execution_id="request")
    assert second == first
    client.get_workflow_handle.assert_called_once_with(call.kwargs["id"], run_id="recorded-run")
    assert handle.result.await_count == 2
    service._node_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_same_identity_different_content_cannot_attach_or_repeat_effects():
    from temporalio.exceptions import WorkflowAlreadyStartedError
    service = _service()
    client = service._temporal_executor.client
    await service.execute_node("coding", "coding_agent", {"prompt": "edit"}, workflow_id="wf", execution_id="request")
    call = client.start_workflow.await_args
    handle = client.start_workflow.return_value
    handle.describe = AsyncMock(return_value=SimpleNamespace(memo=AsyncMock(return_value=call.kwargs["memo"])))
    client.start_workflow.side_effect = WorkflowAlreadyStartedError(call.kwargs["id"], "PiCodingAgentWorkflow")
    answer = await service.execute_node("coding", "coding_agent", {"prompt": "different"}, workflow_id="wf", execution_id="request")
    assert answer["error_type"] == "PiAdmissionConflict"
    assert handle.result.await_count == 1


@pytest.mark.asyncio
async def test_manual_lost_ack_after_rollback_attaches_without_a_fresh_start():
    service = _service()
    client = service._temporal_executor.client
    expected = await service.execute_node("coding", "coding_agent", {"prompt": "edit"}, workflow_id="wf",
        execution_id="request", nodes=[{"id": "coding", "type": "coding_agent"}])
    call = client.start_workflow.await_args
    handle = client.start_workflow.return_value
    handle.describe = AsyncMock(return_value=SimpleNamespace(memo=AsyncMock(return_value=call.kwargs["memo"])))
    service.settings.coding_pi_admissions_enabled = False
    actual = await service.execute_node("coding", "coding_agent", {"prompt": "edit"}, workflow_id="wf",
        execution_id="request", nodes=[{"id": "coding", "type": "coding_agent"}])
    assert actual == expected
    client.start_workflow.assert_awaited_once()
    assert handle.result.await_count == 2
    conflict = await service.execute_node("coding", "coding_agent", {"prompt": "changed"}, workflow_id="wf",
        execution_id="request", nodes=[{"id": "coding", "type": "coding_agent"}])
    assert conflict["error_type"] == "PiAdmissionConflict"
    assert handle.result.await_count == 2


@pytest.mark.asyncio
async def test_disabled_manual_new_identity_never_starts_or_executes_a_node():
    from temporalio.service import RPCError, RPCStatusCode
    service = _service()
    service.settings.coding_pi_admissions_enabled = False
    handle = service._temporal_executor.client.get_workflow_handle.return_value
    handle.describe = AsyncMock(side_effect=RPCError("not found", RPCStatusCode.NOT_FOUND, b""))
    answer = await service.execute_node("coding", "coding_agent", {}, workflow_id="wf", execution_id="new",
        nodes=[{"id": "coding", "type": "coding_agent"}])
    assert answer["error_type"] == "PiAdmissionDisabled"
    service._temporal_executor.client.start_workflow.assert_not_awaited()
    service._node_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_rollback_switch_allows_an_admitted_manual_pi_runtime():
    service = _service()
    service.settings.coding_pi_admissions_enabled = False
    answer = await service.execute_node("coding", "coding_agent", {}, workflow_id="wf", execution_id="request", extras={"pi_harness_version": 1})
    assert answer["success"] is True
    service._temporal_executor.client.start_workflow.assert_awaited_once()
    assert "pi_local_control_version" not in service._temporal_executor.client.start_workflow.await_args.args[1]


@pytest.mark.asyncio
async def test_manual_central_controller_and_local_recorded_control_are_preserved():
    for scope in ({"execution_control_version": 1, "controller_workflow_id": "controller", "generation": 3},
                  {"pi_local_control_version": 1, "pi_local_control_state": "paused", "pi_local_control_revision": 6}):
        service = _service()
        service.database.get_latest_workflow_control = AsyncMock(return_value=None)
        await service.execute_node("coding", "coding_agent", {}, workflow_id="wf", execution_id="request",
                                   extras={"pi_harness_version": 1, **scope})
        context = service._temporal_executor.client.start_workflow.await_args.args[1]
        assert {key: context[key] for key in scope} == scope
        if "controller_workflow_id" in scope:
            assert "pi_local_control_version" not in context


@pytest.mark.asyncio
async def test_new_oneoff_graph_freezes_local_control_only_when_coding_is_present():
    from services.temporal.executor import TemporalExecutor
    client = SimpleNamespace(execute_workflow=AsyncMock(return_value={"success": True, "execution_trace": [], "outputs": {}}))
    settings = SimpleNamespace(temporal_agent_workflow_enabled=False, temporal_per_type_dispatch=False,
                               temporal_worker_pool_enabled=False, distributed_mode=False, coding_pi_admissions_enabled=True)
    with patch("core.config.Settings", return_value=settings):
        executor = TemporalExecutor(client)
        await executor.execute_workflow("wf", [{"id": "coding", "type": "coding_agent"}], [], generation=0, graph_version=2)
        payload = client.execute_workflow.await_args.args[1]
        assert payload["pi_local_control_version"] == 1
        assert "execution_control_version" not in payload
        assert "controller_workflow_id" not in payload
        await executor.execute_workflow("wf", [{"id": "other", "type": "aiAgent"}], [])
        assert "pi_local_control_version" not in client.execute_workflow.await_args.args[1]


@pytest.mark.asyncio
async def test_manual_coding_cannot_fall_back_when_temporal_is_unavailable():
    service = _service(temporal=False)
    result = await service.execute_node("coding", "coding_agent", {}, workflow_id="wf")
    assert result["error_type"] == "CodingAgentTemporalRequired"
    service._node_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_v1_activity_keeps_plugin_implementation():
    service = _service()
    with patch("temporalio.activity.in_activity", return_value=True):
        await service.execute_node("coding", "coding_agent", {}, workflow_id="wf")
    service._node_executor.execute.assert_awaited_once()
    service._temporal_executor.client.execute_workflow.assert_not_awaited()
    service._temporal_executor.client.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_admission_precedes_workflow_output_clearing():
    service = _service()
    service.settings.coding_pi_admissions_enabled = False
    service.clear_all_outputs = AsyncMock()
    result = await service.execute_workflow([{ "id": "coding", "type": "coding_agent"}], [], workflow_id="wf")
    assert result["success"] is False
    assert "CODING_PI_ADMISSIONS_ENABLED" in result["error"]
    service.clear_all_outputs.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_manual_coding_ancestor_is_rejected_before_parent_preparation():
    service = _service()
    service.settings.coding_pi_admissions_enabled = False
    nodes = [{"id": "parent", "type": "rlm_agent"}, {"id": "coding", "type": "coding_agent"}]
    edges = [{"source": "coding", "target": "parent", "targetHandle": "input-tools"}]
    result = await service.execute_node("parent", "rlm_agent", {}, workflow_id="wf", nodes=nodes, edges=edges)
    assert result["error_type"] == "PiAdmissionDisabled"
    service._node_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_switch_preserves_recorded_pi_and_explicit_native_parent_contexts():
    service = _service()
    service.settings.coding_pi_admissions_enabled = False
    nodes = [{"id": "parent", "type": "claude_code_agent"}, {"id": "coding", "type": "coding_agent"}]
    edges = [{"source": "coding", "target": "parent", "targetHandle": "input-tools"}]
    await service.execute_node("parent", "claude_code_agent", {}, workflow_id="wf", nodes=nodes, edges=edges,
                               extras={"pi_harness_version": 1})
    assert service._node_executor.execute.await_args.kwargs["context"]["pi_harness_version"] == 1
    await service.execute_node("parent", "claude_code_agent", {}, workflow_id="wf", nodes=nodes, edges=edges,
                               extras={"coding_native_version": 1})
    assert "pi_harness_version" not in service._node_executor.execute.await_args.kwargs["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest", [{}, {"pi_harness_version": 1}])
async def test_rollback_switch_preserves_generation_selected_parent_runtime(manifest):
    service = _service()
    service.settings.coding_pi_admissions_enabled = False
    service.database.get_latest_workflow_control = AsyncMock(return_value=SimpleNamespace(generation=4, resource_manifest=manifest))
    await service.execute_node("parent", "rlm_agent", {}, workflow_id="wf",
        nodes=[{"id": "parent", "type": "rlm_agent"}, {"id": "coding", "type": "coding_agent"}],
        edges=[{"source": "coding", "target": "parent", "targetHandle": "input-tools"}], extras={"generation": 4})
    context = service._node_executor.execute.await_args.kwargs["context"]
    assert context.get("pi_harness_version") == manifest.get("pi_harness_version")


@pytest.mark.asyncio
async def test_new_generation_freezes_pi_before_preparation():
    from services.deployment.control import WorkflowControlService
    db = AsyncMock()
    db.get_workflow_control_by_idempotency_key.return_value = None
    db.get_latest_workflow_control.return_value = None
    db.allocate_execution_id.return_value = "execution"
    db.get_workflow.return_value = SimpleNamespace(slug="workflow")
    db.create_workflow_control.side_effect = lambda record: record
    with patch("core.config.Settings", return_value=SimpleNamespace(coding_pi_admissions_enabled=True)):
        record, created = await WorkflowControlService(db).begin_generation(
            workflow_id="wf", nodes=[{"id": "coding", "type": "coding_agent"}], edges=[],
            session_id="session", idempotency_key="admission", graph_version=2,
        )
    assert created is True
    assert record.resource_manifest["pi_harness_version"] == 1
    assert record.resource_manifest["execution_control_version"] == 1


@pytest.mark.asyncio
async def test_disabled_generation_admission_has_no_mutations_but_duplicates_keep_old_runtime():
    from services.deployment.control import WorkflowControlService
    db = AsyncMock()
    db.get_workflow_control_by_idempotency_key.return_value = None
    with patch("core.config.Settings", return_value=SimpleNamespace(coding_pi_admissions_enabled=False)):
        with pytest.raises(Exception) as error:
            await WorkflowControlService(db).begin_generation(
                workflow_id="wf", nodes=[{"id": "coding", "type": "coding_agent"}], edges=[],
                session_id="session", idempotency_key="admission",
            )
        assert error.value.as_dict()["error_type"] == "PiAdmissionDisabled"
        db.allocate_execution_id.assert_not_awaited()
        db.create_workflow_control.assert_not_awaited()
        existing = SimpleNamespace(resource_manifest={"execution_control_version": 1})
        db.get_workflow_control_by_idempotency_key.return_value = existing
        record, created = await WorkflowControlService(db).begin_generation(
            workflow_id="wf", nodes=[{"id": "coding", "type": "coding_agent"}], edges=[],
            session_id="session", idempotency_key="admission",
        )
    assert record is existing and created is False
    assert "pi_harness_version" not in record.resource_manifest

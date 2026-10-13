"""Contracts for the shared agent pre-dispatch flow (prepare_agent_call)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _connections(*, task_data=None, tool_data=None):
    return (
        None,  # memory_data
        None,  # skill_data
        tool_data,
        None,  # input_data
        task_data,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "error"])
async def test_completion_firing_keeps_tools(status):
    """The taskTrigger prompt tells a lead to list/accept/reassign via the
    Task Manager tool — stripping tools on exactly that firing made the
    lead unable to act on any completion (it could only answer in prose,
    which read as the lead forgetting its plan)."""
    import nodes.agent._inline as inline

    tools = [
        {"node_id": "tm-1", "node_type": "taskManager", "label": "Task Manager"},
        {"node_id": "search-1", "node_type": "braveSearch", "label": "Search"},
    ]
    task = {"task_id": "task-1", "status": status, "result": "done"}

    with patch.object(
        inline,
        "collect_agent_connections",
        AsyncMock(return_value=_connections(task_data=task, tool_data=tools)),
    ), patch.object(
        inline,
        "collect_teammate_connections",
        AsyncMock(return_value=[]),
    ), patch(
        "services.status_broadcaster.get_status_broadcaster",
        MagicMock(),
    ):
        prepared = await inline.prepare_agent_call(
            node_id="lead-1",
            node_type="orchestrator_agent",
            parameters={"prompt": "review"},
            context={},
            database=object(),
        )

    assert prepared["tool_data"] == tools
    # The task context is still injected ahead of the original prompt.
    assert "task-1" in prepared["parameters"]["prompt"]
    assert prepared["parameters"]["prompt"].endswith("review")


@pytest.mark.asyncio
async def test_legacy_completion_does_not_lookup_unbound_employee_jobs():
    """A real current-run event without a team remains an ordinary completion."""
    import nodes.agent._inline as inline

    task = {"task_id": "ordinary-task", "status": "completed", "result": "done"}
    context = {"workflow_id": "wf", "nodes": [{"id": "trigger", "type": "taskTrigger"}],
        "outputs": {"trigger": task}}
    with patch.object(inline, "collect_agent_connections", AsyncMock(return_value=_connections(
            task_data=task, tool_data=[{"node_id": "manager", "node_type": "taskManager"}]))), \
            patch.object(inline, "collect_teammate_connections", AsyncMock(return_value=[])):
        prepared = await inline.prepare_agent_call(node_id="lead", node_type="orchestrator_agent",
            parameters={"prompt": "review"}, context=context, database=object())
    assert "employee_job_id" not in context
    assert prepared["tool_data"][0]["node_id"] == "manager"
    assert "ordinary-task" in prepared["parameters"]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted_job", [None, "job"])
async def test_employee_review_lookup_failure_is_not_silently_ignored(admitted_job):
    """Team-scoped reviews and admitted jobs retain authoritative DB failures."""
    import nodes.agent._inline as inline

    task = {"task_id": "task", "status": "completed", "result": "done"}
    if admitted_job is None:
        task["team_id"] = "managed-team"
    context = {"workflow_id": "wf", "nodes": [{"id": "trigger", "type": "taskTrigger"}],
        "outputs": {"trigger": task}}
    if admitted_job:
        context["employee_job_id"] = admitted_job
    connections = AsyncMock(return_value=_connections(task_data=task, tool_data=[]))
    with patch.object(inline, "collect_agent_connections", connections), \
            patch("services.employees.team_runtime.attach_employee_job", AsyncMock(side_effect=ValueError(
                "The review task does not belong to this employee job"))) as attach, \
            pytest.raises(ValueError, match="does not belong"):
        await inline.prepare_agent_call(node_id="lead", node_type="ai_employee",
            parameters={"prompt": "review"}, context=context, database=object())
    assert attach.await_args.args[2] == task
    connections.assert_not_awaited()


@pytest.mark.asyncio
async def test_collected_review_for_admitted_job_preserves_missing_team_validation():
    """A review found during edge walking cannot bypass admitted-job checks."""
    import nodes.agent._inline as inline

    task = {"task_id": "task", "status": "completed", "result": "done"}
    context = {"workflow_id": "wf", "employee_job_id": "job"}
    async def attach(database, scope, review):
        if review:
            raise ValueError("The review task does not belong to this employee job")
    with patch.object(inline, "collect_agent_connections", AsyncMock(return_value=_connections(
            task_data=task, tool_data=[]))), \
            patch("services.employees.team_runtime.attach_employee_job", AsyncMock(side_effect=attach)), \
            patch("services.employees.team_runtime.employee_job_parameters", AsyncMock(return_value={})), \
            patch("services.employees.team_runtime.employee_lead_prompt", AsyncMock(return_value="review")), \
            pytest.raises(ValueError, match="does not belong"):
        await inline.prepare_agent_call(node_id="lead", node_type="ai_employee",
            parameters={"prompt": "review"}, context=context, database=object())

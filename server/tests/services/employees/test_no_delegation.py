"""Durable no-delegation failures use the same write reservation as assignments."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select
from models.database import AgentTeam, TeamTask
from models.employees import EmployeeJob
from services.employees.jobs import check_lead_assignments, fail_no_assignments, bind_job_team, deliver_job, job_progress
from services.plugin import NodeContext


@pytest.fixture(autouse=True)
def quiet_broadcasts(monkeypatch):
    monkeypatch.setattr("services.employees.events.employee_changed_now", lambda _: None)


async def install(db, *, state="working", delivery=None):
    async with db.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="wf", team_lead_node_id="lead", execution_id="origin"))
        session.add(EmployeeJob(id="job", workflow_id="wf", lead_node_id="lead", origin_execution_id="origin", team_id="team",
            mission="Original email", state=state, source={"nodes": []},
            delivery=delivery or {"node_ids": [], "completed": [], "inflight": None}))
        await session.commit()


def scope(**extra):
    return {"employee_job_id": "job", "workflow_id": "wf", "node_id": "lead", "team_id": "team", **extra}


async def saved(db):
    async with db.get_session() as session:
        return await session.get(EmployeeJob, "job")


async def assignment(db, *, identifier="task", status="queued"):
    return await db.create_durable_team_task(id=identifier, team_id="team", title="Work", created_by="lead", assigned_to="worker", status=status)


async def test_initial_completion_requests_delegation_without_closing_team(real_database):
    await install(real_database)
    result = await check_lead_assignments(real_database, scope())
    assert result["requires_delegation"] and result["job_id"] == "job"
    assert (await saved(real_database)).state == "working"
    async with real_database.get_session() as session:
        assert (await session.get(AgentTeam, "team")).status == "active"


@pytest.mark.parametrize("status", ["blocked", "queued", "running", "submitted", "accepted", "failed", "cancelled", "skipped"])
async def test_any_persisted_assignment_prevents_no_assignment_failure(real_database, status):
    await install(real_database)
    await assignment(real_database, status=status)
    assert not (await check_lead_assignments(real_database, scope())).get("requires_delegation")
    result = await fail_no_assignments(real_database, scope(correction_count=1))
    assert result["reason"] == "assignments_exist"
    assert (await saved(real_database)).state == "working"


async def test_no_assignment_failure_is_visible_idempotent_and_cannot_revive(real_database):
    await install(real_database)
    first = await fail_no_assignments(real_database, scope(correction_count=1))
    assert first["saved"]
    assert not (await fail_no_assignments(real_database, scope(correction_count=0)))["saved"]
    assert await assignment(real_database) is None
    assert await real_database.add_team_task("legacy-task", "team", "Late work", "lead") is None
    assert not await real_database.update_team_status("team", "active")
    assert not await real_database.update_team_status("team", "completed")
    await bind_job_team(real_database, workflow_id="wf", lead_node_id="lead", execution_id="origin", team_id="team")
    row = await saved(real_database)
    assert row.state == "failed" and row.result is None
    assert row.delivery["failure"]["code"] == "lead_no_assignments"
    assert row.delivery["failure"]["correction_count"] == 1
    assert "did not assign work" in (await job_progress(real_database, "wf"))["message"]


async def test_correction_reopens_previously_completed_empty_team(real_database):
    await install(real_database)
    await real_database.update_team_status("team", "completed")
    assert (await check_lead_assignments(real_database, scope()))["requires_delegation"]
    async with real_database.get_session() as session:
        team = await session.get(AgentTeam, "team")
        assert team.status == "active" and team.completed_at is None


async def test_actual_approval_protects_job_even_without_delivery_metadata(real_database):
    from models.approvals import ApprovalRequest
    await install(real_database)
    async with real_database.reserved_session() as session:
        session.add(ApprovalRequest(id="approval", idempotency_key="approval", workflow_id="wf", owner_id="owner",
            node_id="gate", execution_id="job", status="pending"))
        await session.commit()
    assert (await check_lead_assignments(real_database, scope()))["protected"]
    assert (await fail_no_assignments(real_database, scope()))["reason"] == "protected_outcome"
    ctx = NodeContext(node_id="boundary", node_type="employeeJob", workflow_id="wf", execution_id="origin",
        outputs={"lead": {"response": "Draft", "team_id": "team"}})
    assert (await deliver_job(real_database, ctx, lead_node_id="lead"))["state"] == "working"
    assert (await saved(real_database)).state == "working"


async def test_concurrent_intakes_bind_exact_job_not_shared_generation(real_database):
    await install(real_database)
    async with real_database.reserved_session() as session:
        first = await session.get(EmployeeJob, "job")
        first.team_id = None
        session.add(EmployeeJob(id="job2", workflow_id="wf", lead_node_id="lead", origin_execution_id="origin",
            mission="Second email", state="running", source={}, delivery={}))
        await session.commit()
    await bind_job_team(real_database, workflow_id="wf", lead_node_id="lead", execution_id="origin", team_id="team", employee_job_id="job")
    async with real_database.get_session() as session:
        assert (await session.get(EmployeeJob, "job")).team_id == "team"
        assert (await session.get(EmployeeJob, "job2")).team_id is None


async def test_admission_cancellation_disambiguates_only_exact_recorded_lead(real_database, monkeypatch):
    from services.temporal.employee_job_workflow import fail_employee_job, activity
    await install(real_database)
    async with real_database.reserved_session() as session:
        first = await session.get(EmployeeJob, "job")
        first.source = {"runtime": {"namespace": "test", "lead_workflow_id": "lead-a", "lead_initial_run_id": "run-a"}}
        session.add(EmployeeJob(id="job2", workflow_id="wf", lead_node_id="lead", origin_execution_id="origin",
            mission="Second email", state="running", source={"runtime": {"namespace": "test", "lead_workflow_id": "lead-b", "lead_initial_run_id": "run-b"}}, delivery={}))
        await session.commit()
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: real_database))
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity, "info", lambda: SimpleNamespace(workflow_namespace="test", workflow_id="lead-b", workflow_run_id="run-b"))
    result = await fail_employee_job({"workflow_id": "wf", "node_id": "lead", "execution_id": "origin", "runtime_admission": True, "cancelled": True})
    assert result["saved"]
    async with real_database.get_session() as session:
        assert (await session.get(EmployeeJob, "job")).state == "working"
        assert (await session.get(EmployeeJob, "job2")).state == "cancelled"
    monkeypatch.setattr(activity, "info", lambda: SimpleNamespace(workflow_namespace="test", workflow_id="unknown", workflow_run_id="unknown"))
    result = await fail_employee_job({"workflow_id": "wf", "node_id": "lead", "execution_id": "origin", "runtime_admission": True})
    assert result["reason"] == "ambiguous_admission"


@pytest.mark.parametrize("state,delivery", [
    ("cancelled", {}), ("delivered", {}), ("delivering", {}), ("delivery_queued", {}),
    ("delivering", {"inflight": "send"}), ("working", {"inflight": "approval"}),
    ("working", {"inflight": "send"}), ("working", {"completed": ["approval"]}),
])
async def test_failure_preserves_terminal_approval_and_uncertain_send(real_database, state, delivery):
    await install(real_database, state=state, delivery=delivery)
    before = await saved(real_database)
    assert not (await fail_no_assignments(real_database, scope(correction_count=1)))["saved"]
    after = await saved(real_database)
    assert after.state == before.state and after.delivery == before.delivery


async def test_database_outage_is_not_an_empty_task_list(real_database, monkeypatch):
    await install(real_database)
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def broken():
        raise RuntimeError("database unavailable")
        yield
    monkeypatch.setattr(real_database, "reserved_session", broken)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await check_lead_assignments(real_database, scope())


async def test_assignment_failure_race_has_only_one_winner(real_database):
    await install(real_database)
    task, failure = await asyncio.gather(assignment(real_database), fail_no_assignments(real_database, scope(correction_count=1)))
    row = await saved(real_database)
    if task:
        assert failure["reason"] == "assignments_exist" and row.state == "working"
    else:
        assert failure["saved"] and row.state == "failed"
    async with real_database.get_session() as session:
        tasks = list((await session.execute(select(TeamTask).where(TeamTask.team_id == "team"))).scalars())
    assert bool(tasks) == bool(task)


async def test_shared_delivery_cannot_send_undelegated_draft(real_database, monkeypatch):
    await install(real_database)
    execute = AsyncMock()
    monkeypatch.setattr("core.container.container", SimpleNamespace(workflow_service=lambda: SimpleNamespace(execute_node=execute)))
    ctx = NodeContext(node_id="boundary", node_type="employeeJob", workflow_id="wf", execution_id="origin",
        outputs={"lead": {"response": "A draft", "team_id": "team"}})
    result = await deliver_job(real_database, ctx, lead_node_id="lead")
    assert result["state"] == "failed"
    execute.assert_not_awaited()
    assert (await saved(real_database)).result is None


async def test_managed_finish_refuses_empty_team_and_preserves_unmanaged(real_database):
    from services.agent_team import AgentTeamService
    await install(real_database)
    service = AgentTeamService(real_database)
    with pytest.raises(ValueError, match="Assign work"):
        await service.finish_durable_team(workflow_id="wf", team_lead_node_id="lead", execution_id="origin")
    async with real_database.reserved_session() as session:
        await session.delete(await session.get(EmployeeJob, "job"))
        await session.commit()
    result = await service.finish_durable_team(workflow_id="wf", team_lead_node_id="lead", execution_id="origin")
    assert result["status"] == "completed"

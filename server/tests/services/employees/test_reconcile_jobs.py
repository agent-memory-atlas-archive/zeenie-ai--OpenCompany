"""Real database repair guards and Temporal protobuf proof failure cases."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import WorkflowExecutionStatus
from temporalio.converter import DataConverter

from models.approvals import ApprovalRequest
from models.database import AgentTeam, TeamTask, WorkflowControlExecution
from models.employees import EmployeeJob
from services.employees import reconcile_jobs as reconcile


@pytest.fixture
async def database(real_database):
    # Uses the shared isolated SQLite/PostgreSQL fixture; never the live app DB.
    yield real_database


async def install(database, **changes):
    runtime = {"version": 1, "namespace": "default", "root_workflow_id": "root", "root_run_id": "root-first",
               "lead_workflow_id": "lead-child", "lead_initial_run_id": "lead-first", "lead_first_execution_run_id": "lead-first"}
    job = EmployeeJob(id="job", workflow_id="graph", origin_execution_id="shared-generation", lead_node_id="lead",
        team_id="team", state="working", mission="PRIVATE EMAIL CONTENT", source={"generation": 4, "runtime": runtime},
        delivery={"node_ids": ["gate", "send"], "completed": [], "inflight": None}, **changes)
    async with database.reserved_session() as session:
        session.add(job)
        session.add(AgentTeam(id="team", workflow_id="graph", team_lead_node_id="lead", status="completed", config={}))
        await session.commit()
    return job


async def row(database):
    async with database.get_session() as session:
        return await session.get(EmployeeJob, "job")


def valid_client():
    description = SimpleNamespace(run_id="last", status=WorkflowExecutionStatus.COMPLETED)
    return SimpleNamespace(get_workflow_handle=lambda _identifier: SimpleNamespace(describe=AsyncMock(return_value=description)))


@pytest.fixture
def proof(monkeypatch):
    value = {"namespace": "default", "root": {"workflow_id": "root", "run_ids": ["first", "last"]},
             "lead": {"workflow_id": "lead", "run_ids": ["first", "last"]}, "lead_outcome": "success"}
    inspect = AsyncMock(return_value=value)
    monkeypatch.setattr(reconcile, "_execution_proof", inspect)
    # Use a sync recorder because lifecycle broadcasts are synchronous enqueue.
    notifications = []
    monkeypatch.setattr("services.employees.events.employee_changed_now", notifications.append)
    return SimpleNamespace(value=value, inspect=inspect, notifications=notifications)


@pytest.mark.asyncio
async def test_dry_run_is_read_only_and_apply_preserves_source_and_delivery(database, proof, monkeypatch):
    original = await install(database)
    monkeypatch.setattr(database, "get_team_tasks", AsyncMock(side_effect=AssertionError("fail-open task helper must not be used")))
    dry = await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph")
    assert dry[0]["decision"] == "eligible"
    assert (await row(database)).state == "working"
    assert proof.notifications == []
    applied = await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", job_id="job", apply=True)
    assert applied[0]["decision"] == "applied"
    current = await row(database)
    assert current.state == "failed" and current.result is None
    assert current.source == original.source and current.mission == original.mission
    assert current.delivery["node_ids"] == ["gate", "send"]
    assert current.delivery["failure"]["code"] == "lead_no_assignments"
    assert current.delivery["failure"]["reconciliation_proof"] == proof.value
    async with database.get_session() as session:
        assert (await session.get(AgentTeam, "team")).status == "failed"
    assert proof.notifications == ["graph"]
    again = await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True)
    assert again[0]["reason"] == "job_not_active"
    assert proof.notifications == ["graph"]
    assert "PRIVATE EMAIL CONTENT" not in str(applied)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cancelled", "delivered", "delivering", "delivery_queued", "failed", "queued"])
async def test_terminal_delivery_and_queued_states_are_preserved(database, proof, state):
    await install(database)
    async with database.reserved_session() as session:
        job = await session.get(EmployeeJob, "job")
        job.state = state
        await session.commit()
    decisions = await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True)
    assert decisions[0]["decision"] == "skip"
    assert (await row(database)).state == state
    proof.inspect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", [{"inflight": "gate"}, {"inflight": "send"}, {"completed": ["gate"]},
    {"outputs": {"gate": {"approved": False}}}, {"lease_until": "expired"}, {"claim_token": "claim"},
    {"failure": {"code": "other_failure"}}, {"resolution_pending": "owner-decision"}])
async def test_every_delivery_or_approval_intent_is_preserved(database, proof, delivery):
    await install(database)
    async with database.reserved_session() as session:
        job = await session.get(EmployeeJob, "job")
        job.delivery = {**job.delivery, **delivery}
        await session.commit()
    decisions = await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True)
    assert decisions[0]["reason"] == "delivery_or_result_exists"
    assert (await row(database)).state == "working"
    proof.inspect.assert_not_awaited()


@pytest.mark.asyncio
async def test_assignments_and_approval_rows_block_repair(database, proof):
    await install(database)
    async with database.reserved_session() as session:
        session.add(ApprovalRequest(id="approval", idempotency_key="approval-key", owner_id="owner", workflow_id="graph",
            execution_id="job", node_id="gate", status="discarded"))
        await session.commit()
    assert (await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True))[0]["reason"] == "approval_exists"
    proof.inspect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,generation", [("paused", 4), ("pausing", 4), ("resetting", 4), ("running", 5)])
async def test_paused_or_reset_generation_is_preserved(database, proof, status, generation):
    await install(database)
    async with database.reserved_session() as session:
        session.add(WorkflowControlExecution(id="control", workflow_id="graph", generation=generation,
            execution_id="shared-generation", root_execution_id="shared-generation", graph_hash="hash", status=status,
            idempotency_key="control-key"))
        await session.commit()
    assert (await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True))[0]["reason"] == "generation_paused_or_changed"


@pytest.mark.asyncio
async def test_assignment_racing_with_proof_is_rechecked_under_reservation(database, proof):
    await install(database)
    started, release = asyncio.Event(), asyncio.Event()

    async def inspect(*_arguments):
        started.set()
        await release.wait()
        return proof.value

    proof.inspect.side_effect = inspect
    repairing = asyncio.create_task(reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True))
    await started.wait()
    async with database.reserved_session() as session:
        session.add(TeamTask(id="task", team_id="team", title="Work", created_by="lead", assigned_to="member"))
        await session.commit()
    release.set()
    assert (await repairing)[0]["reason"] == "assignments_exist"
    assert (await row(database)).state == "working"


@pytest.mark.asyncio
async def test_unavailable_evidence_or_database_cannot_fail_a_job(database, proof):
    await install(database)
    proof.inspect.side_effect = RuntimeError("PRIVATE EMAIL CONTENT in provider error")
    decision = (await reconcile.reconcile_jobs(database, valid_client(), workflow_id="graph", apply=True))[0]
    assert decision["reason"] == "evidence_or_database_unavailable"
    assert "PRIVATE EMAIL CONTENT" not in str(decision)
    assert (await row(database)).state == "working"


async def chain_client(*, outcome="success", reused=False, continued=False, reset=False, live=False):
    converter = DataConverter.default
    first, last = "first", "last" if continued else "first"
    histories = {}
    for index, run_id in enumerate([first, last] if continued else [first]):
        opening = HistoryEvent(event_id=1, event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED)
        attributes = opening.workflow_execution_started_event_attributes
        attributes.first_execution_run_id = first
        attributes.original_execution_run_id = "reset" if reset else run_id
        attributes.continued_execution_run_id = first if index else ""
        closing = HistoryEvent(event_id=2)
        if continued and index == 0:
            closing.event_type = EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW
            closing.workflow_execution_continued_as_new_event_attributes.new_execution_run_id = last
        elif outcome == "failed":
            closing.event_type = EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED
            closing.workflow_execution_failed_event_attributes.failure.application_failure_info.type = "OtherError"
        else:
            closing.event_type = EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED
            value = {"success": True} if outcome == "success" else {"success": False, "error_type": "EmployeeLeadNoAssignments"}
            closing.workflow_execution_completed_event_attributes.result.payloads.extend(await converter.encode([value]))
        histories[run_id] = SimpleNamespace(events=[opening, closing])
    description = SimpleNamespace(run_id=last, status=WorkflowExecutionStatus.RUNNING if live else WorkflowExecutionStatus.COMPLETED,
        raw_description=SimpleNamespace(workflow_execution_info=SimpleNamespace(first_run_id=first)))

    def get(_identifier, run_id=None):
        return SimpleNamespace(describe=AsyncMock(return_value=description), fetch_history=AsyncMock(return_value=histories[run_id or last]))

    async def visible(**_options):
        for run_id in [*histories, *( ["previous-reuse"] if reused else [] )]:
            yield SimpleNamespace(run_id=run_id)

    return SimpleNamespace(get_workflow_handle=get, list_workflows=visible, data_converter=converter)


@pytest.mark.asyncio
@pytest.mark.parametrize("continued,outcome", [(False, "success"), (True, "success"), (False, "typed")])
async def test_chain_accepts_only_exact_completed_or_typed_runs(continued, outcome):
    chain = await reconcile._current_chain(await chain_client(continued=continued, outcome=outcome), "root", "first")
    assert len(chain.run_ids) == (2 if continued else 1)
    assert chain.typed_failure == (outcome == "typed")


@pytest.mark.asyncio
@pytest.mark.parametrize("options,reason", [({"live": True}, "execution_not_completed"),
    ({"reset": True}, "reset_or_retry_execution"), ({"reused": True}, "execution_reused_or_reset"),
    ({"outcome": "failed"}, "unrelated_execution_failure")])
async def test_unproven_run_chains_are_rejected(options, reason):
    with pytest.raises(reconcile.EvidenceUnavailable, match=reason):
        await reconcile._current_chain(await chain_client(**options), "root", "first")

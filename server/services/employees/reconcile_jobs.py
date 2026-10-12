"""Deliberate, evidence-only repair of completed leads with no assignments.

This module never starts workflows, calls models, or delivers results. CLI
inspection is read-only by default and does not run application startup or
database migrations. Temporal visibility is used only to locate histories;
the histories and exact run-chain identities provide the proof.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus

from models.approvals import ApprovalRequest
from models.database import AgentTeam, TeamTask, WorkflowControlExecution
from models.employees import EmployeeJob

_MAX_CHAIN_RUNS = 128
_ERROR_TYPE = "EmployeeLeadNoAssignments"
_FAILURE_MESSAGE = "The lead completed without assigning work to a teammate."


class EvidenceUnavailable(Exception):
    """A safe, content-free reason to skip a job."""


@dataclass
class RunChain:
    workflow_id: str
    first_run_id: str
    histories: list[Any]
    run_ids: list[str]
    result: dict | None
    typed_failure: bool

    def references(self) -> dict:
        return {"workflow_id": self.workflow_id, "first_run_id": self.first_run_id,
                "run_ids": self.run_ids}


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _decode(client: Any, payloads: Any) -> dict | None:
    values = await client.data_converter.decode(payloads.payloads)
    return values[0] if len(values) == 1 and isinstance(values[0], dict) else None


def _failure_is_typed(failure: Any) -> bool:
    while failure and failure.ByteSize():
        if failure.application_failure_info.type == _ERROR_TYPE:
            return True
        failure = failure.cause
    return False


def _result_is_typed(result: dict | None) -> bool:
    return bool(result and result.get("success") is False and (
        result.get("error_type") == _ERROR_TYPE or result.get("type") == _ERROR_TYPE
        or (isinstance(result.get("error"), dict) and result["error"].get("type") == _ERROR_TYPE)
    ))


async def _current_chain(client: Any, workflow_id: str, first_run_id: str | None = None) -> RunChain:
    """Read every CAN history, rejecting retries, resets, reuse and live runs."""
    latest = await client.get_workflow_handle(workflow_id).describe()
    first = latest.raw_description.workflow_execution_info.first_run_id
    if not first or (first_run_id and first != first_run_id):
        raise EvidenceUnavailable("execution_chain_changed")
    if latest.status not in {WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.FAILED}:
        raise EvidenceUnavailable("execution_not_completed")
    histories, run_ids = [], []
    previous, run_id = "", first
    result, typed_failure = None, False
    for _ in range(_MAX_CHAIN_RUNS):
        if not run_id or run_id in run_ids:
            raise EvidenceUnavailable("invalid_execution_chain")
        history = await client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history()
        if not history.events or history.events[0].event_type != EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
            raise EvidenceUnavailable("history_start_unavailable")
        opening = history.events[0].workflow_execution_started_event_attributes
        if (opening.first_execution_run_id != first or opening.original_execution_run_id != run_id
                or opening.continued_execution_run_id != previous or opening.attempt > 1):
            raise EvidenceUnavailable("reset_or_retry_execution")
        histories.append(history)
        run_ids.append(run_id)
        closing = history.events[-1]
        if closing.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW:
            previous, run_id = run_id, closing.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
            continue
        if run_id != latest.run_id:
            raise EvidenceUnavailable("execution_reused_or_reset")
        if closing.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
            result = await _decode(client, closing.workflow_execution_completed_event_attributes.result)
            typed_failure = _result_is_typed(result)
        elif closing.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
            typed_failure = _failure_is_typed(closing.workflow_execution_failed_event_attributes.failure)
            if not typed_failure:
                raise EvidenceUnavailable("unrelated_execution_failure")
        else:
            raise EvidenceUnavailable("execution_not_completed")
        break
    else:
        raise EvidenceUnavailable("execution_chain_too_long")
    # A closed earlier execution under the same ID is not a CAN predecessor.
    # Reject it even when latest happens to match the stored first run.
    visible_runs = set()
    async for execution in client.list_workflows(query=f"WorkflowId = {_quoted(workflow_id)}"):
        visible_runs.add(execution.run_id)
        if len(visible_runs) > _MAX_CHAIN_RUNS:
            raise EvidenceUnavailable("execution_chain_too_long")
    if visible_runs - set(run_ids):
        raise EvidenceUnavailable("execution_reused_or_reset")
    return RunChain(workflow_id, first, histories, run_ids, result, typed_failure)


async def _child_records(client: Any, root: RunChain) -> list[dict]:
    records = []
    for history, run_id in zip(root.histories, root.run_ids):
        initiated = {}
        for event in history.events:
            if event.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED:
                attributes = event.start_child_workflow_execution_initiated_event_attributes
                initiated[event.event_id] = {
                    "workflow_id": attributes.workflow_id, "context": await _decode(client, attributes.input),
                    "initiated_event_id": event.event_id, "parent_run_id": run_id,
                }
            elif event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED:
                attributes = event.child_workflow_execution_started_event_attributes
                record = initiated.get(attributes.initiated_event_id)
                if record and record["workflow_id"] == attributes.workflow_execution.workflow_id:
                    records.append({**record, "run_id": attributes.workflow_execution.run_id})
    return records


async def _intake_records(client: Any, root: RunChain, job: EmployeeJob) -> list[dict]:
    """Recorded successful intake result, never a shared generation lookup."""
    records = []
    for history, run_id in zip(root.histories, root.run_ids):
        scheduled = {}
        for event in history.events:
            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                attributes = event.activity_task_scheduled_event_attributes
                context = await _decode(client, attributes.input)
                if (context and context.get("workflow_id") == job.workflow_id
                        and context.get("node_type") == "employeeJob"):
                    scheduled[event.event_id] = context
            elif event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                attributes = event.activity_task_completed_event_attributes
                context = scheduled.get(attributes.scheduled_event_id)
                if context:
                    output = await _decode(client, attributes.result)
                    value = output.get("result") if output and output.get("success") is True else None
                    if isinstance(value, dict) and value.get("job_id") == job.id:
                        records.append({"node_id": context.get("node_id"), "completed_event_id": event.event_id,
                                        "parent_run_id": run_id})
    return records


async def _proof_from_root(client: Any, job: EmployeeJob, root: RunChain, runtime: dict) -> dict:
    opening = root.histories[0].events[0].workflow_execution_started_event_attributes
    root_context = await _decode(client, opening.input)
    if not root_context or root_context.get("workflow_id") != job.workflow_id:
        raise EvidenceUnavailable("root_job_scope_mismatch")
    if runtime.get("root_run_id") and runtime["root_run_id"] not in root.run_ids:
        raise EvidenceUnavailable("root_run_not_in_chain")
    context_id = runtime.get("context_execution_id")
    if context_id and context_id != (root_context.get("context_execution_id") or root_context.get("execution_id")):
        raise EvidenceUnavailable("root_context_mismatch")
    intakes = await _intake_records(client, root, job)
    children = await _child_records(client, root)
    eligible = []
    for child in children:
        context = child["context"] or {}
        if context.get("node_id") != job.lead_node_id or context.get("workflow_id") != job.workflow_id:
            continue
        if runtime.get("lead_workflow_id") and child["workflow_id"] != runtime["lead_workflow_id"]:
            continue
        if runtime.get("lead_initial_run_id") and child["run_id"] != runtime["lead_initial_run_id"]:
            continue
        if context.get("employee_job_id") == job.id:
            # New/dispatch jobs carry their own trusted identity into the lead.
            eligible.append(child)
        else:
            inputs = context.get("inputs") or {}
            matching = [intake for intake in intakes if intake["parent_run_id"] == child["parent_run_id"]
                        and intake["completed_event_id"] < child["initiated_event_id"]
                        and isinstance(inputs.get(intake["node_id"]), dict)
                        and inputs[intake["node_id"]].get("job_id") == job.id]
            if len(matching) == 1:
                eligible.append({**child, "intake": matching[0]})
    if len(eligible) != 1:
        raise EvidenceUnavailable("lead_identity_ambiguous")
    child = eligible[0]
    first = runtime.get("lead_first_execution_run_id") or child["run_id"]
    lead = await _current_chain(client, child["workflow_id"], first)
    lead_opening = lead.histories[0].events[0].workflow_execution_started_event_attributes
    if (lead_opening.parent_workflow_execution.workflow_id != root.workflow_id
            or lead_opening.parent_workflow_execution.run_id != child["parent_run_id"]
            or lead_opening.parent_initiated_event_id != child["initiated_event_id"]
            or lead.run_ids[0] != child["run_id"]):
        raise EvidenceUnavailable("lead_parent_mismatch")
    if not lead.typed_failure and not (lead.result and lead.result.get("success") is True):
        raise EvidenceUnavailable("lead_not_successful")
    lead_result = (lead.result or {}).get("result") or {}
    if isinstance(lead_result, dict) and lead_result.get("team_id") and lead_result["team_id"] != job.team_id:
        raise EvidenceUnavailable("lead_team_mismatch")
    return {"namespace": client.namespace, "root": root.references(), "lead": lead.references(),
            "lead_outcome": "no_assignments" if lead.typed_failure else "success",
            "correction_count": int(lead_result.get("correction_count") or 0) if isinstance(lead_result, dict) else 0,
            **({"intake": child["intake"]} if child.get("intake") else {})}


async def _execution_proof(client: Any, job: EmployeeJob) -> dict:
    runtime = (job.source or {}).get("runtime") or {}
    if runtime:
        if runtime.get("namespace") != client.namespace:
            raise EvidenceUnavailable("namespace_mismatch")
        if not all(runtime.get(key) for key in ("root_workflow_id", "root_run_id", "lead_workflow_id",
                                               "lead_initial_run_id", "lead_first_execution_run_id")):
            raise EvidenceUnavailable("incomplete_runtime_identity")
        root = await _current_chain(client, runtime["root_workflow_id"], runtime.get("root_first_execution_run_id"))
        return await _proof_from_root(client, job, root, runtime)
    # Old graph jobs can share origin_execution_id across many Gmail firings.
    # Locate roots by the application's immutable search attribute, then require
    # recorded job intake + the matching lead child. Never invent a child ID.
    candidates = []
    async for execution in client.list_workflows(query=(
            f"EventWorkflowId = {_quoted(job.workflow_id)} AND WorkflowType = 'MachinaWorkflow'")):
        if execution.id not in candidates:
            candidates.append(execution.id)
        if len(candidates) > _MAX_CHAIN_RUNS:
            raise EvidenceUnavailable("too_many_historical_roots")
    # Dispatched v1 jobs have a distinct persisted origin identity. Still require
    # recorded root/lead linkage, rather than trusting the name alone.
    if job.origin_execution_id == job.id and job.id not in candidates:
        candidates.append(job.id)
    proofs = []
    for workflow_id in candidates:
        try:
            root = await _current_chain(client, workflow_id)
            proof = await _proof_from_root(client, job, root, {})
            if proof.get("intake") or job.origin_execution_id == job.id:
                proofs.append(proof)
        except EvidenceUnavailable:
            continue
    if len(proofs) != 1:
        raise EvidenceUnavailable("historical_identity_unproven")
    return proofs[0]


async def _ineligible_reason(session: Any, job: EmployeeJob) -> str | None:
    if job.state not in {"running", "working"}:
        return "job_not_active"
    delivery = job.delivery or {}
    if job.result or any(delivery.get(key) for key in ("inflight", "completed", "outputs", "claim_token", "lease_until", "failure", "resolution_pending")):
        return "delivery_or_result_exists"
    if not job.team_id:
        return "team_unavailable"
    team = await session.get(AgentTeam, job.team_id)
    if (not team or team.workflow_id != job.workflow_id or team.team_lead_node_id != job.lead_node_id
            or team.status not in {"active", "completed"}):
        return "team_scope_or_state_mismatch"
    if (await session.execute(select(TeamTask.id).where(TeamTask.team_id == job.team_id).limit(1))).first():
        return "assignments_exist"
    generation = int((job.source or {}).get("generation") or 0)
    latest = (await session.execute(select(WorkflowControlExecution).where(
        WorkflowControlExecution.workflow_id == job.workflow_id).order_by(
        WorkflowControlExecution.generation.desc()).limit(1))).scalar_one_or_none()
    if latest and (latest.generation != generation or latest.status in {
            "paused", "pausing", "resuming", "resetting", "cancelled", "stopped", "failed"}):
        return "generation_paused_or_changed"
    # Even finished approvals are evidence that the lead reached delivery.
    identities = {job.id, job.origin_execution_id}
    runtime = (job.source or {}).get("runtime") or {}
    identities.update(str(runtime[key]) for key in ("context_execution_id", "root_workflow_id", "lead_workflow_id") if runtime.get(key))
    if (await session.execute(select(ApprovalRequest.id).where(ApprovalRequest.workflow_id == job.workflow_id,
            ApprovalRequest.execution_id.in_(identities)).limit(1))).first():
        return "approval_exists"
    return None


def _snapshot(job: EmployeeJob) -> str:
    return hashlib.sha256(json.dumps({"workflow_id": job.workflow_id, "team_id": job.team_id,
        "origin_execution_id": job.origin_execution_id, "lead_node_id": job.lead_node_id,
        "source": job.source, "delivery": job.delivery}, sort_keys=True, default=str).encode()).hexdigest()


async def reconcile_jobs(database: Any, client: Any, *, workflow_id: str, job_id: str | None = None,
                         apply: bool = False) -> list[dict]:
    """Return content-free decisions; apply only transactionally rechecked proof."""
    async with database.get_session() as session:
        query = select(EmployeeJob).where(EmployeeJob.workflow_id == workflow_id)
        if job_id:
            query = query.where(EmployeeJob.id == job_id)
        jobs = list((await session.execute(query.order_by(EmployeeJob.id))).scalars().all())
    decisions = []
    for job in jobs:
        decision = {"workflow_id": workflow_id, "job_id": job.id, "decision": "skip", "reason": ""}
        try:
            async with database.get_session() as session:
                reason = await _ineligible_reason(session, job)
            if reason:
                decision["reason"] = reason
            else:
                snapshot = _snapshot(job)
                proof = await _execution_proof(client, job)
                decision.update(decision="eligible", reason="completed_lead_without_assignments", proof=proof)
                if apply:
                    async with database.reserved_session() as session:
                        current = await session.get(EmployeeJob, job.id)
                        reason = await _ineligible_reason(session, current) if current else "job_unavailable"
                        if not reason and _snapshot(current) != snapshot:
                            reason = "job_changed_during_inspection"
                        if not reason:
                            # Recheck exact latest runs while holding the same write
                            # reservation used by assignment insertion and cleanup.
                            for part in ("root", "lead"):
                                latest = await client.get_workflow_handle(proof[part]["workflow_id"]).describe()
                                if latest.run_id != proof[part]["run_ids"][-1] or latest.status not in {
                                        WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.FAILED}:
                                    reason = "execution_changed_during_inspection"
                                    break
                        if reason:
                            decision.update(decision="skip", reason=reason)
                        else:
                            now = datetime.now(timezone.utc)
                            current.state = "failed"
                            current.delivery = {**current.delivery, "failure": {"code": "lead_no_assignments",
                                "message": _FAILURE_MESSAGE, "correction_count": int(proof.get("correction_count") or 0),
                                "reconciliation_proof": proof}}
                            current.updated_at = now
                            team = await session.get(AgentTeam, current.team_id)
                            team.status, team.completed_at = "failed", now
                            await session.commit()
                            decision["decision"] = "applied"
                    if decision["decision"] == "applied":
                        from services.employees.events import employee_changed_now
                        employee_changed_now(workflow_id)
        except EvidenceUnavailable as exc:
            decision.update(decision="skip", reason=str(exc))
        except Exception:
            # Provider/database exceptions can contain source inputs. Report the
            # category only and leave the job untouched for deliberate inspection.
            decision.update(decision="skip", reason="evidence_or_database_unavailable")
        decisions.append(decision)
    return decisions


async def _run_cli(arguments: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from temporalio.client import Client
    from core.config import Settings
    from core.database import Database

    settings = Settings()
    database = Database(settings)
    # Do not use Database.startup(): it creates tables and runs backfills even
    # for a dry run. This command requires an existing application database.
    database.engine = create_async_engine(settings.database_url, echo=False)
    database.async_session = async_sessionmaker(database.engine, expire_on_commit=False)
    try:
        client = await Client.connect(settings.temporal_server_address, namespace=settings.temporal_namespace)
        decisions = await reconcile_jobs(database, client, workflow_id=arguments.workflow_id,
                                         job_id=arguments.job_id, apply=arguments.apply)
        for decision in decisions:
            print(json.dumps(decision, sort_keys=True))
        if not decisions:
            print(json.dumps({"workflow_id": arguments.workflow_id, "job_id": arguments.job_id,
                              "decision": "skip", "reason": "no_matching_jobs"}))
        return 0
    except Exception:
        print(json.dumps({"workflow_id": arguments.workflow_id, "job_id": arguments.job_id,
                          "decision": "skip", "reason": "connection_or_database_unavailable"}))
        return 1
    finally:
        await database.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--apply", action="store_true", help="Apply only proven no-assignment failures (default: inspect)")
    return asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

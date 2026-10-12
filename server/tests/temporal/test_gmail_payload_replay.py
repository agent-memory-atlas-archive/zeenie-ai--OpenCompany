"""Native Temporal history replay, same-controller upgrade and queued rollover."""
from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import types
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType, IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
from services.employees.reconcile_jobs import _current_chain, _proof_from_root

_items: list[dict] = []
_spilled: list[list] = []
# Keep the worker-upgrade reproduction on the implementation before #143.
# HEAD moves when the fix is committed, so it cannot identify the old worker.
_PRE_FIX_COMMIT = "988f7c5422ba055095e4d497eea6d390e5224107"


@activity.defn(name="poll.googleGmailReceive.v1")
async def poll(payload: dict) -> dict:
    events = list(_items)
    _items.clear()
    return {"events": events, "seen_ids": [*payload["seen_ids"], *(event["id"] for event in events)]}


@activity.defn(name="broadcast_trigger_status_activity")
async def broadcast(_payload: dict) -> None:
    pass


@activity.defn(name="evaluate_trigger_filter_activity")
async def evaluate_filter(_payload: dict) -> bool:
    return True


@activity.defn(name="controller.queue.spill")
async def spill(payload: dict) -> dict:
    if payload.get("prepend"):
        _spilled[0:0] = payload["events"]
    else:
        _spilled.extend(payload["events"])
    return {"spilled": len(payload["events"])}


@activity.defn(name="controller.queue.read")
async def read(_payload: dict) -> dict:
    events = list(_spilled)
    _spilled.clear()
    return {"events": events, "more": False}


@activity.defn(name="node.employeeJob.v1")
async def intake(context: dict) -> dict:
    return {"success": True, "result": {"job_id": context["job_id"], "mission": "Work"}}


@workflow.defn(name="AgentWorkflow", sandboxed=False)
class ReconcileLead:
    @workflow.run
    async def run(self, context: dict) -> dict:
        if context.get("continue"):
            workflow.continue_as_new({**context, "continue": False})
        return {"success": True, "result": {"response": "Acknowledgement", "team_id": "team"}}


@workflow.defn(name="MachinaWorkflow", sandboxed=False)
class GraphProbe:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        if not payload.get("reconcile"):
            return payload["nodes"][0]["_trigger_output"]
        if payload.get("continue"):
            workflow.continue_as_new({**payload, "continue": False})
        result = await workflow.execute_activity("node.employeeJob.v1", {
            "node_id": "intake", "node_type": "employeeJob", "workflow_id": payload["workflow_id"],
            "job_id": payload["job_id"],
        }, start_to_close_timeout=timedelta(seconds=10), activity_id="intake")
        child = {"node_id": "lead", "workflow_id": payload["workflow_id"], "inputs": {"intake": result["result"]}, "continue": True}
        await workflow.execute_child_workflow("AgentWorkflow", child, id=workflow.info().workflow_id + ":lead")
        return {"success": True}


def _load_old(stem: str):
    package = importlib.import_module("services.temporal")
    name = "services.temporal." + stem
    source = subprocess.check_output(["git", "show", f"{_PRE_FIX_COMMIT}:server/services/temporal/{stem}.py"],
        cwd=Path(__file__).parents[3], encoding="utf-8")
    module = types.ModuleType(name)
    module.__package__ = "services.temporal"
    module.__file__ = "git:" + _PRE_FIX_COMMIT + ":" + stem
    sys.modules[name] = module
    setattr(package, stem, module)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


async def _wait(handle, predicate):
    for _ in range(400):
        status = await handle.query("status")
        if predicate(status):
            return status
        await asyncio.sleep(0.025)
    raise AssertionError(f"Controller did not reach expected state: {status}")


async def _child_result(client, identifier):
    for _ in range(400):
        try:
            handle = client.get_workflow_handle(identifier)
            await handle.describe()
            return await asyncio.wait_for(handle.result(), 10)
        except Exception:
            await asyncio.sleep(0.025)
    raise AssertionError(f"Child did not complete: {identifier}")


async def gate(cli: str):
    from services.temporal import trigger_listener_workflow as current_listener
    from services.temporal import workflow_control_workflow as current_controller
    package = importlib.import_module("services.temporal")
    _load_old("trigger_listener_workflow")
    old_controller = _load_old("workflow_control_workflow")
    old_controller._MIN_POLL_INTERVAL_S = current_controller._MIN_POLL_INTERVAL_S = 1
    current_controller._MAX_CARRIED_EVENTS = 1
    token = uuid4().hex
    controller_id, slug, queue = "controller-" + token, "gmail-" + token, "gmail-replay-" + token
    first = {"id": "A", "from": "sender", "subject": "A", "body": "old body"}
    registration = {"listener_id": "gmail", "workflow_type": "PollingTriggerWorkflow", "trigger_node_id": "gmail",
        "listener_args": {"trigger_node_id": "gmail", "trigger_label": "gmail", "node_type": "googleGmailReceive",
            "workflow_slug": slug, "workflow_id": None, "data_scope_id": "scope", "graphVersion": 2, "generation": 1,
            "filter_params": {"poll_interval": 1}, "nodes": [{"id": "gmail", "type": "googleGmailReceive"}], "edges": []}}
    definitions = [WorkflowControlWorkflow, GraphProbe, ReconcileLead]
    activities = [poll, broadcast, evaluate_filter, spill, read, intake]
    histories = []
    try:
        async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli, ip="127.0.0.1", port=None) as env:
            client = env.client
            await client.operator_service.add_search_attributes(AddSearchAttributesRequest(namespace=client.namespace,
                search_attributes={"EventWorkflowId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD}))
            # Execute exact pre-fix source, not a fabricated/edited history.
            async with Worker(client, task_queue=queue, workflows=[old_controller.WorkflowControlWorkflow, GraphProbe],
                              activities=activities, max_cached_workflows=0):
                handle = await client.start_workflow("WorkflowControlWorkflow", {"triggers": {"gmail": registration},
                    "pending_events": [["gmail", first]]}, id=controller_id, task_queue=queue)
                old_output = await _child_result(client, f"{slug}-gmail-A")
                assert old_output == {"_event_envelope": first}
                await handle.execute_update("set_control_state", "paused")
                old_history = await handle.fetch_history()
            sys.modules[current_listener.__name__] = current_listener
            sys.modules[current_controller.__name__] = current_controller
            package.trigger_listener_workflow = current_listener
            package.workflow_control_workflow = current_controller
            replay = await Replayer(workflows=definitions).replay_workflow(old_history)
            assert replay.replay_failure is None
            # Replay the same open controller in the upgraded worker, then
            # accept a poll page with enough events to carry and spill at CAN.
            current_controller.WorkflowControlWorkflow._history_pressure = lambda self: (
                len(self._seen_event_ids) >= 2 and not workflow.info().continued_run_id)
            _items.extend({"id": key, "from": "sender", "subject": key, "body": "complete " + key,
                           "data": {"business": key}} for key in "BCD")
            async with Worker(client, task_queue=queue, workflows=definitions, activities=activities, max_cached_workflows=0):
                await handle.execute_update("set_control_state", "running")
                for key in "BCD":
                    output = await _child_result(client, f"{slug}-gmail-{key}")
                    assert output["body"] == "complete " + key
                    assert output["data"] == {"business": key}
                    assert output["_event_envelope"]["id"] == key
                await handle.signal("reset")
                await handle.result()
                run_id = handle.first_execution_run_id
                while run_id:
                    history = await client.get_workflow_handle(controller_id, run_id=run_id).fetch_history()
                    histories.append(history)
                    run_id = next((event.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
                        for event in history.events if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW), None)
                assert len(histories) == 2
                starts = [event.start_child_workflow_execution_initiated_event_attributes.workflow_id
                    for history in histories for event in history.events
                    if event.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED]
                assert starts == [f"{slug}-gmail-{key}" for key in "ABCD"]
                # Genuine historical v1 intake/child linkage through both root
                # and lead CAN chains is sufficient proof; origin is deliberately
                # a shared generation ID to catch unsafe historical inference.
                graph_id = "historical-graph-" + token
                root = await client.start_workflow("MachinaWorkflow", {"workflow_id": "app-graph", "job_id": "job-42",
                    "continue": True, "reconcile": True}, id=graph_id, task_queue=queue)
                assert await root.result() == {"success": True}
                chain = await _current_chain(client, graph_id, root.first_execution_run_id)
                assert len(chain.run_ids) == 2
                proof = await _proof_from_root(client, SimpleNamespace(id="job-42", workflow_id="app-graph",
                    lead_node_id="lead", team_id="team", origin_execution_id="shared-generation"), chain, {})
                assert len(proof["lead"]["run_ids"]) == 2
                assert proof["intake"]["node_id"] == "intake"
                assert proof["lead_outcome"] == "success"
            for history in histories:
                replay = await Replayer(workflows=definitions).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        sys.modules[current_listener.__name__] = current_listener
        sys.modules[current_controller.__name__] = current_controller
        package.trigger_listener_workflow = current_listener
        package.workflow_control_workflow = current_controller


def test_native_gmail_old_history_same_controller_upgrade_queue_and_reconciliation():
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to an existing native Temporal CLI binary")
    completed = subprocess.run([sys.executable, "-X", "utf8", "-m", "tests.temporal.test_gmail_payload_replay", cli],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=180, check=False)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-14000:]


if __name__ == "__main__":
    asyncio.run(gate(sys.argv[1]))

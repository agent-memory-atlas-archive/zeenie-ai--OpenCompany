"""Run the detached bridge and existing delegation runner on real Temporal."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.agent_workflow import DelegatedTaskWorkflow
from services.temporal.pi_inline_delegation import PiInlineDelegationWorkflow, _request_digest

EVENTS = []
COUNTS = Counter()
ENTERED = asyncio.Event()
TASK = {}
CONTROL = {"participant_state": "running", "participant_revision": 1, "producers_held": False}


@activity.defn(name="execution_control.register")
async def register(payload):
    EVENTS.append(("register", payload["workflow_id"]))
    return CONTROL


@activity.defn(name="execution_control.unregister")
async def unregister(payload):
    EVENTS.append(("unregister", payload["workflow_id"]))


@activity.defn(name="agent.register_task_execution")
async def register_task(payload):
    return {"registered": True}


@activity.defn(name="agent.acquire_subagent_permit")
async def acquire(payload):
    COUNTS["acquired"] += 1
    return {"lease_id": payload["permit_id"]}


@activity.defn(name="agent.release_subagent_permit")
async def release(payload):
    COUNTS["released"] += 1
    return {"released": True}


@activity.defn(name="agent.begin_delegation")
async def begin(payload):
    TASK["status"] = "running"
    EVENTS.append(("begin", payload["team_task_id"]))
    return {"claimed": True}


@activity.defn(name="agent.finish_delegation")
async def finish(payload):
    TASK.update(status="submitted" if payload.get("success") else "failed", result=payload.get("result"))
    return {"status": TASK["status"]}


@activity.defn(name="agent.cancel_delegation")
async def cancel(payload):
    if TASK.get("status") != "submitted":
        TASK.update(status="cancelled", error=payload.get("reason"))
    return {"status": TASK["status"]}


@activity.defn(name="coding.inline_delegation_status")
async def status(payload):
    # Production validation is exercised with real SQLite by unit contracts.
    EVENTS.append(("verified" if payload.get("status") == "running" else "terminal", TASK.get("status")))
    return dict(TASK)


@activity.defn(name="inline.test.enter")
async def enter(payload):
    EVENTS.append(("effect", payload["node_id"]))
    ENTERED.set()


@workflow.defn(sandboxed=False, name="PiCodingAgentWorkflow")
class StubCodingWorkflow:
    def __init__(self):
        self.ready = False

    @workflow.signal
    async def complete(self):
        self.ready = True

    @workflow.run
    async def run(self, payload):
        await workflow.execute_activity("inline.test.enter", payload, start_to_close_timeout=timedelta(seconds=60))
        await workflow.wait_condition(lambda: self.ready)
        return {"success": True, "result": {"response": "done", "usage": {"total_tokens": 5}}}


def request(identity, *, local=False):
    child = {"node_id": "coding", "node_type": "coding_agent", "pi_harness_version": 1,
        "workflow_id": "wf", "root_execution_id": identity, "user_id": "alice", "team_task_id": "task",
        "execution_control_version": 1, "controller_workflow_id": "controller", "generation": 1,
        "execution_control_state": "running", "execution_control_revision": 1}
    if local:
        for key in ("execution_control_version", "controller_workflow_id", "generation",
                    "execution_control_state", "execution_control_revision"):
            child.pop(key)
        child.update(pi_local_control_version=1, pi_local_control_state="running", pi_local_control_revision=1)
    lifecycle = {"team_id": "team", "team_task_id": "task", "parent_agent_node_id": "parent",
        "child_agent_node_id": "coding", "child_agent_name": "Coding Agent", "root_execution_id": identity, "task": "edit"}
    value = {"inline_delegation_version": 1, "runner_workflow_id": identity, "child_workflow_id": identity + "-agent",
             "limit": 1, "child_context": child, "lifecycle": lifecycle}
    value["request_digest"] = _request_digest(value)
    return value


async def scenario(environment, *, cancelled=False, local=False):
    EVENTS.clear()
    COUNTS.clear()
    ENTERED.clear()
    TASK.clear()
    TASK["status"] = "queued"
    identity = "inline-replay-" + uuid4().hex
    definitions = [PiInlineDelegationWorkflow, DelegatedTaskWorkflow, StubCodingWorkflow]
    activities = [register, unregister, register_task, acquire, release, begin, finish, cancel, status, enter]
    async with Worker(environment.client, task_queue=identity, workflows=definitions, activities=activities):
        handle = await environment.client.start_workflow(PiInlineDelegationWorkflow.run, request(identity, local=local), id=identity, task_queue=identity)
        await asyncio.wait_for(ENTERED.wait(), timeout=15)
        if local:
            assert all(item[0] != "register" for item in EVENTS)
        else:
            assert EVENTS[0] == ("register", identity)
        assert [item[0] for item in EVENTS].index("verified") < [item[0] for item in EVENTS].index("begin")
        if not local:
            assert ("register", identity + "-runner") in EVENTS
        assert COUNTS["acquired"] == 1
        if cancelled:
            await handle.cancel()
            try:
                await handle.result()
            except Exception:
                pass
            assert TASK["status"] == "cancelled", (TASK, EVENTS, COUNTS)
        else:
            await environment.client.get_workflow_handle(identity + "-agent").signal("complete")
            assert (await handle.result())["status"] == "completed"
            assert TASK["status"] == "submitted"
        assert COUNTS["released"] == 1
        assert any(item[0] == "terminal" for item in EVENTS)
        history = await handle.fetch_history()
    replay = await Replayer(workflows=definitions).replay_workflow(WorkflowHistory.from_json(history.workflow_id, history.to_json()))
    assert replay.replay_failure is None


async def main():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        with environment.auto_time_skipping_disabled():
            await scenario(environment)
            await scenario(environment, cancelled=True)
            await scenario(environment, local=True)
            await scenario(environment, cancelled=True, local=True)


def test_detached_wrapper_control_registration_completion_cancel_and_replay():
    result = subprocess.run([sys.executable, "-m", "tests.temporal.test_pi_inline_replay"], cwd=Path(__file__).parents[2],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, check=False)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run(main())

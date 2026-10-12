"""Real Temporal histories for old leads, bounded correction, rollover and restart."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, ToolCall, message_to_wire
from services.temporal.agent_workflow import AgentWorkflow


CALLS: list[tuple[str, dict]] = []
ASSIGNED: set[str] = set()
RESTART_READY = asyncio.Event()
RESTART_RELEASE = asyncio.Event()
QUEUE = "employee-no-delegation-replay"


@activity.defn(name="agent.prepare_payload")
async def prepare(context: dict) -> dict:
    return {
        "node_id": "lead", "node_type": "ai_employee", "workflow_id": "graph", "session_id": "session",
        "execution_id": context["execution_id"], "provider": "openai", "model": "stub", "max_tokens": 100,
        "temperature": 0, "system_message": "Delegate work and review it", "user_prompt": context["mode"],
        "employee_job_id": context["employee_job_id"], "team_id": context["employee_job_id"],
        "team_execution_id": context["employee_job_id"],
        "owns_execution_team": True, "memory_node_id": "", "memory_content": "", "memory_window_size": 10,
        "max_iterations": 1 if context["mode"] == "exhausted" else 4,
        "thinking_config": None, "compaction_threshold": None,
        "tools": [{"name": "assign_stub", "node_type": "_test_assign", "tool_node_id": "assign",
            "version": 1, "parameters": {}, "tool_info": {}, "needs_canvas": False,
            "definition": {"name": "assign_stub", "description": "Assign", "parameters": {"type": "object", "properties": {}}}}],
    }


@activity.defn(name="agent.execute_llm_step")
async def model(payload: dict) -> dict:
    prompt = next(m["content"] for m in payload["messages"] if m["role"] == "user")
    iteration = payload["iteration"]
    if prompt == "provider-retry" and iteration == 1 and activity.info().attempt == 1:
        raise RuntimeError("Transient provider error")
    if prompt == "restart" and iteration == 1 and not RESTART_RELEASE.is_set():
        RESTART_READY.set()
        await RESTART_RELEASE.wait()
    CALLS.append(("model", {"prompt": prompt, "iteration": iteration, "messages": payload["messages"]}))
    if prompt == "success" and iteration == 1:
        return {"kind": "tool_calls", "assistant_message": message_to_wire(Message(role="assistant",
            tool_calls=[ToolCall(id="assignment", name="assign_stub", args={})])),
            "calls": [{"id": "assignment", "name": "assign_stub", "args": {}}], "usage": {"input_tokens": 1}}
    return {"kind": "final", "content": "Lead acknowledgement", "assistant_message": message_to_wire(
        Message(role="assistant", content="Lead acknowledgement")), "usage": {"input_tokens": 1}}


@activity.defn(name="node._test_assign.v1")
async def assign(payload: dict) -> dict:
    ASSIGNED.add(payload["employee_job_id"])
    return {"success": True, "result": {"assigned": True, "status": "queued"}}


@activity.defn(name="agent.finalize_team")
async def finalize(payload: dict) -> dict:
    CALLS.append(("finalize", payload))
    return {"team_id": payload["team_id"], "status": "active", "job_id": payload["team_id"],
        "requires_delegation": payload["team_id"] not in ASSIGNED}


@activity.defn(name="employee.job.failed")
async def fail(payload: dict) -> dict:
    CALLS.append(("fail", payload))
    if payload.get("mode") == "assignment-race":
        return {"saved": False, "reason": "assignments_exist"}
    return {"saved": True}


@activity.defn(name="agent.store_output")
async def store(payload: dict) -> dict:
    CALLS.append(("store", payload))
    return {"saved": True}


@activity.defn(name="agent.skill.clear")
async def clear(payload: dict) -> dict:
    CALLS.append(("clear", payload))
    return {"cleared": True}


@activity.defn(name="agent.broadcast_progress")
async def broadcast(payload: dict) -> dict:
    CALLS.append(("broadcast", payload))
    return {"emitted": True}


ACTIVITIES = [prepare, model, assign, finalize, fail, store, clear, broadcast]


async def gate(cli: str) -> None:
    import services.temporal.trigger_listener_workflow as trigger_module
    original_pressure, original_patch = trigger_module._history_pressure, workflow.patched
    trigger_module._history_pressure = lambda *_: "rollover" in workflow.info().workflow_id and not workflow.info().continued_run_id
    histories = []
    try:
        async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli, ip="127.0.0.1", port=None,
                ui=False, dev_server_log_level="error") as env:
            async with Worker(env.client, task_queue=QUEUE, workflows=[AgentWorkflow], activities=ACTIVITIES):
                for mode in ("old", "failure", "exhausted", "success", "provider-retry", "rollover", "rollover-durable", "assignment-race"):
                    start = len(CALLS)
                    workflow.patched = lambda name: False if name == "employee-no-delegation-repair-v1" and mode == "old" else original_patch(name)
                    identifier = mode + "-" + uuid4().hex
                    context = {"node_id": "lead", "node_type": "ai_employee", "workflow_id": "graph", "execution_id": identifier,
                        "employee_job_id": identifier, "mode": mode}
                    if mode == "rollover-durable":
                        context["native_workspace_version"] = 1
                    handle = await env.client.start_workflow(AgentWorkflow.run, context, id=identifier, task_queue=QUEUE)
                    result = await handle.result()
                    workflow.patched = original_patch
                    current = CALLS[start:]
                    if mode in {"old", "success"}:
                        assert result["success"]
                        assert len([entry for entry in current if entry[0] == "store"]) == 1
                    elif mode == "assignment-race":
                        assert result["success"] and result["result"]["response"] is None
                        assert not any(entry[0] == "store" for entry in current)
                        assert any(entry[0] == "clear" and entry[1]["execution_id"] == identifier for entry in current)
                        assert any(entry[0] == "broadcast" and entry[1].get("phase") == "completed" for entry in current)
                    else:
                        assert result["error_type"] == "EmployeeLeadNoAssignments", result
                        assert result["result"]["correction_count"] == (0 if mode == "exhausted" else 1)
                        assert not any(entry[0] == "store" for entry in current)
                        failures = [entry[1] for entry in current if entry[0] == "fail"]
                        assert len(failures) == 1 and failures[0]["failure_code"] == "lead_no_assignments"
                    turns = [entry[1] for entry in current if entry[0] == "model"]
                    if mode not in {"old", "exhausted"}:
                        assert sum("one corrective attempt" in m.get("content", "") for m in turns[1]["messages"]) == 1
                    if "rollover" in mode:
                        assert result["result"]["correction_count"] == 1
                    history = await handle.fetch_history()
                    histories.append(history)
                    # Follow all runs, including the pre-rollover history.
                    while True:
                        [started] = [e.workflow_execution_started_event_attributes for e in history.events if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED]
                        if not started.continued_execution_run_id:
                            break
                        history = await env.client.get_workflow_handle(identifier, run_id=started.continued_execution_run_id).fetch_history()
                        histories.append(history)
            start = len(CALLS)
            identifier = "restart-" + uuid4().hex
            worker = Worker(env.client, task_queue=QUEUE, workflows=[AgentWorkflow], activities=ACTIVITIES,
                identity="employee-repair-old-worker", graceful_shutdown_timeout=timedelta(seconds=1))
            await worker.__aenter__()
            handle = await env.client.start_workflow(AgentWorkflow.run, {"node_id": "lead", "node_type": "ai_employee",
                "workflow_id": "graph", "execution_id": identifier, "employee_job_id": identifier, "mode": "restart"},
                id=identifier, task_queue=QUEUE)
            await asyncio.wait_for(RESTART_READY.wait(), 30)
            # Stop while the corrective model activity is outstanding. Its
            # retry runs on the replacement worker against the same history.
            await worker.__aexit__(None, None, None)
            RESTART_RELEASE.set()
            async with Worker(env.client, task_queue=QUEUE, workflows=[AgentWorkflow], activities=ACTIVITIES,
                    identity="employee-repair-new-worker"):
                result = await handle.result()
            assert result["error_type"] == "EmployeeLeadNoAssignments"
            assert result["result"]["correction_count"] == 1
            assert len([entry for entry in CALLS[start:] if entry[0] == "fail"]) == 1
            restarted_history = await handle.fetch_history()
            assert any(e.workflow_task_completed_event_attributes.identity == "employee-repair-new-worker"
                for e in restarted_history.events if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED)
            histories.append(restarted_history)
            for history in histories:
                captured = WorkflowHistory.from_json(history.workflow_id, history.to_json())
                replay = await Replayer(workflows=[AgentWorkflow]).replay_workflow(captured, raise_on_replay_failure=False)
                assert replay.replay_failure is None, replay.replay_failure
    finally:
        trigger_module._history_pressure, workflow.patched = original_pressure, original_patch


def test_no_delegation_histories_replay_and_worker_restart():
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to an existing native Temporal CLI")
    result = subprocess.run([sys.executable, "-m", "tests.temporal.test_no_delegation_replay", cli],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-7000:]


if __name__ == "__main__":
    asyncio.run(gate(sys.argv[1]))

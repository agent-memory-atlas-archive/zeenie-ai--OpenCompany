"""Generate history with the unmodified HEAD workflow, replay with Pi edits."""

import asyncio
from pathlib import Path
import subprocess
import sys
import types
import os
import hashlib
import base64
import json
import zlib
from collections import Counter
from uuid import uuid4

from temporalio.client import WorkflowHistory
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.agent_workflow import AgentWorkflow
from tests.temporal.test_agent_workflow_replay import _TEST_ACTIVITIES
from services.llm.protocol import Message, ToolCall, message_to_wire

COUNTS = Counter()
FROZEN_HISTORY = Path(__file__).with_name("fixtures") / "pi_native_pre_cutover_history.json.zlib.b64"


@activity.defn(name="agent.prepare_payload")
async def _prepare_legacy_rollover(context):
    from tests.temporal.test_agent_workflow_replay import _prepared_payload
    COUNTS["prepare"] += 1
    prepared = _prepared_payload()
    prepared.update(max_iterations=3, tools=[{"name": "native_tool", "node_type": "pi_native_tool",
        "version": 1, "tool_node_id": "legacy-tool", "parameters": {}, "tool_info": {},
        "definition": {"name": "native_tool", "description": "legacy v1 tool",
            "parameters": {"type": "object", "properties": {}}}}])
    return prepared


@activity.defn(name="agent.execute_llm_step")
async def _legacy_llm(payload):
    COUNTS["llm"] += 1
    if payload["iteration"] == 0:
        call = {"id": "legacy-v1", "name": "native_tool", "args": {}}
        return {"kind": "tool_calls", "calls": [call], "assistant_message": message_to_wire(
            Message(role="assistant", tool_calls=[ToolCall(**call)])), "usage": {"total_tokens": 3}}
    return {"kind": "final", "content": "done", "assistant_message": message_to_wire(
        Message(role="assistant", content="done")), "usage": {"total_tokens": 3}}


@activity.defn(name="node.pi_native_tool.v1")
async def _legacy_v1_tool(payload):
    COUNTS["tool_attempts"] += 1
    if activity.info().attempt == 1:
        raise ApplicationError("Original v1 retry", type="NativeRetry")
    COUNTS["tool_effects"] += 1
    return {"result": "old native result"}


async def _main():
    source = subprocess.check_output(["git", "show", "HEAD:server/services/temporal/agent_workflow.py"],
        cwd=Path(__file__).parents[3]).decode("utf-8")
    name = "services.temporal._pre_pi_workflow_fixture"
    baseline = types.ModuleType(name)
    baseline.__package__ = "services.temporal"
    baseline.__file__ = str(Path(__file__).parents[2] / "services" / "temporal" / "agent_workflow.py")
    sys.modules[name] = baseline
    exec(compile(source, baseline.__file__, "exec"), baseline.__dict__)
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(environment.client, task_queue="pi-native-baseline", workflows=[baseline.AgentWorkflow],
                activities=_TEST_ACTIVITIES):
            handle = await environment.client.start_workflow("AgentWorkflow", {"node_id": "agent-replay"},
                id="pre-pi-native-" + uuid4().hex, task_queue="pi-native-baseline")
            assert (await handle.result())["success"]
            history = await handle.fetch_history()
        result = await Replayer(workflows=[AgentWorkflow]).replay_workflow(
            WorkflowHistory.from_json(history.workflow_id, history.to_json()))
        assert result.replay_failure is None
        from services.temporal import agent_workflow
        previous_cap = agent_workflow._AGENT_HISTORY_SOFT_CAP
        baseline._AGENT_HISTORY_SOFT_CAP = 1
        agent_workflow._AGENT_HISTORY_SOFT_CAP = 1
        activities = [item for item in _TEST_ACTIVITIES if item.__name__ not in {
            "_prepare_payload", "_execute_llm_step"}]
        activities.extend([_prepare_legacy_rollover, _legacy_llm, _legacy_v1_tool])
        try:
            async with Worker(environment.client, task_queue="pi-native-baseline", workflows=[baseline.AgentWorkflow],
                    activities=activities):
                handle = await environment.client.start_workflow("AgentWorkflow", {"node_id": "agent-replay"},
                    id="pre-pi-native-rollover-" + uuid4().hex, task_queue="pi-native-baseline")
                result = await handle.result()
                assert result["success"] and result["result"]["usage"]["total_tokens"] == 6
                histories = []
                run_id = handle.first_execution_run_id
                while run_id:
                    history = await environment.client.get_workflow_handle(handle.id, run_id=run_id).fetch_history()
                    histories.append(history)
                    run_id = next((event.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
                        for event in history.events if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW), None)
            assert len(histories) == 2 and COUNTS["prepare"] == 2
            assert COUNTS["tool_attempts"] == 2 and COUNTS["tool_effects"] == 1 and COUNTS["llm"] == 2
            for history in histories:
                result = await Replayer(workflows=[AgentWorkflow]).replay_workflow(
                    WorkflowHistory.from_json(history.workflow_id, history.to_json()))
                assert result.replay_failure is None
            if os.environ.get("PI_WRITE_NATIVE_FIXTURE") == "1":
                fixture = {"origin_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                    cwd=Path(__file__).parents[3]).decode().strip(),
                    "native_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "description": "Pre-Pi native v1 tool retry and legacy Continue-As-New histories",
                    "histories": [{"workflow_id": item.workflow_id, "history": json.loads(item.to_json())}
                        for item in histories]}
                FROZEN_HISTORY.parent.mkdir(exist_ok=True)
                FROZEN_HISTORY.write_text(base64.b64encode(zlib.compress(json.dumps(fixture).encode())).decode() + "\n",
                    encoding="ascii")
        finally:
            agent_workflow._AGENT_HISTORY_SOFT_CAP = previous_cap


def test_pre_pi_native_head_history_replays_with_current_workflow():
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_pi_native_compatibility"],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=90, env={**os.environ, "DEBUG": "false"})
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-8000:]


def test_frozen_pre_pi_v1_retry_and_legacy_rollover_histories_replay():
    from services.temporal import agent_workflow
    fixture = json.loads(zlib.decompress(base64.b64decode(FROZEN_HISTORY.read_text(encoding="ascii"))))
    assert len(fixture["origin_commit"]) == 40 and len(fixture["native_source_sha256"]) == 64
    assert len(fixture["histories"]) == 2
    previous = agent_workflow._AGENT_HISTORY_SOFT_CAP
    agent_workflow._AGENT_HISTORY_SOFT_CAP = 1
    async def replay():
        for item in fixture["histories"]:
            result = await Replayer(workflows=[AgentWorkflow]).replay_workflow(
                WorkflowHistory.from_json(item["workflow_id"], item["history"]))
            assert result.replay_failure is None
    try:
        asyncio.run(replay())
    finally:
        agent_workflow._AGENT_HISTORY_SOFT_CAP = previous


if __name__ == "__main__":
    asyncio.run(_main())

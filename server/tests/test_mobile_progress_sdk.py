"""Optional contract test against the installed, isolated mobile-use SDK.

Runs a local graph with fake actions, not a phone or model. No SDK installation
or downloads are performed; point MOBILE_USE_UPSTREAM_SOURCE at an existing
checkout with its own .venv to exercise another pinned installation.
"""

import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


_SERVER = Path(__file__).resolve().parents[1]
_RESULT_PREFIX = "MOBILE_PROGRESS_SDK_RESULT="

# Keep SDK imports in its interpreter: the ordinary server must not acquire
# langchain/mobile-use dependencies just to collect this optional test.
_GRAPH_SCRIPT = textwrap.dedent(r'''
    import asyncio
    import importlib.metadata
    import importlib.util
    import json
    import socket
    import sys
    import types
    from typing import Annotated

    sys.path.insert(0, sys.argv[2])
    if any(importlib.util.find_spec(name) is None for name in ("langgraph", "langchain_core", "pydantic")):
        raise SystemExit(77)

    # The real ExecutorToolNode imports SDK logging/telemetry. They are not
    # under test; isolate them so the probe cannot send telemetry or write logs.
    telemetry_module = types.ModuleType("minitap.mobile_use.services.telemetry")
    telemetry_module.telemetry = None
    sys.modules[telemetry_module.__name__] = telemetry_module
    logger_module = types.ModuleType("minitap.mobile_use.utils.logger")
    logger_module.get_logger = lambda _: types.SimpleNamespace(info=lambda *_args, **_kwargs: None)
    sys.modules[logger_module.__name__] = logger_module

    from pydantic import BaseModel
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool
    from langchain_core.tools.base import InjectedToolCallId
    from langgraph.graph import START, END, StateGraph, add_messages
    from langgraph.prebuilt import InjectedState
    from langgraph.types import Command
    from minitap.mobile_use.agents.executor.tool_node import ExecutorToolNode
    from minitap.mobile_use.agents.planner.types import Subgoal, SubgoalStatus

    spec = importlib.util.spec_from_file_location("mobile_progress_under_test", sys.argv[1])
    progress_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(progress_module)

    class Progress(progress_module.ProgressCallbacks, BaseCallbackHandler):
        # Callback errors must fail this test rather than being logged and ignored.
        raise_error = True

    events = []
    callback = Progress(lambda event, **fields: events.append({"type": event, **fields}))
    screenshot = "SCREENSHOT_BASE64_MUST_NOT_BE_PUBLISHED"
    typed_text = "private-field-value-12345"
    raw_input = "RAW_INPUT_MUST_NOT_BE_PUBLISHED"

    class State(BaseModel):
        subgoal_plan: list[Subgoal]
        executor_messages: Annotated[list[AnyMessage], add_messages]
        latest_screenshot: str

    @tool
    async def tap(
        agent_thought: str,
        target: dict,
        extra_input: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        state: Annotated[State, InjectedState],
    ) -> Command:
        """Fake successful tap; no phone interaction."""
        assert state.latest_screenshot == screenshot
        return Command(update={"executor_messages": [ToolMessage(
            tool_call_id=tool_call_id, content="Tapped Settings.", status="success",
        )]})

    @tool
    async def focus_and_input_text(
        agent_thought: str,
        text: str,
        target: dict,
        tool_call_id: Annotated[str, InjectedToolCallId],
        state: Annotated[State, InjectedState],
    ) -> Command:
        """Fake failed text entry; outcome deliberately echoes private text."""
        return Command(update={"executor_messages": [ToolMessage(
            tool_call_id=tool_call_id, content="Failed to type " + text, status="error",
        )]})

    async def nested_chain():
        # Nested chains inherit langgraph_node. Their result must not be
        # mistaken for a real planner/orchestrator update or phase change.
        await RunnableLambda(lambda _: {"subgoal_plan": [Subgoal(
            id="nested", description="NOT_A_GRAPH_PLAN", status=SubgoalStatus.PENDING,
        )]}, name="nested_parser").ainvoke({"private_input": raw_input})

    class Planner:
        async def __call__(self, state):
            await nested_chain()
            return {"subgoal_plan": [
                Subgoal(id="open", description="Open Settings", status=SubgoalStatus.NOT_STARTED),
                Subgoal(id="name", description="Update display name", status=SubgoalStatus.NOT_STARTED),
            ]}

    async def orchestrator(state):
        await nested_chain()
        return {"subgoal_plan": [
            state.subgoal_plan[0].model_copy(update={"status": SubgoalStatus.PENDING}),
            state.subgoal_plan[1],
        ]}

    async def executor(state):
        return {"executor_messages": [AIMessage(content="", tool_calls=[
            {"name": "tap", "id": "tap-call", "args": {
                "agent_thought": "Open the app settings", "target": {"text": "Settings"},
                "extra_input": raw_input,
            }},
            {"name": "focus_and_input_text", "id": "type-call", "args": {
                "agent_thought": "Enter " + typed_text + " into the display-name field",
                "text": typed_text, "target": {"text": "Display name"},
            }},
            {"name": "tap", "id": "skipped-call", "args": {
                "agent_thought": "This action must be skipped after failure",
                "target": {"text": "Save"}, "extra_input": raw_input,
            }},
        ])]}

    def post_executor_gate(state):
        return "invoke_tools"

    async def main():
        # Install the guard after asyncio has created its internal wakeup
        # socket, so any graph/model/device network attempt fails immediately.
        def refuse_network(*args, **kwargs):
            raise AssertionError("The SDK callback probe must not use the network")
        socket.create_connection = socket.socket.connect = socket.socket.connect_ex = refuse_network

        graph = StateGraph(State)
        graph.add_node("planner", Planner())
        graph.add_node("orchestrator", orchestrator)
        graph.add_node("executor", executor)
        graph.add_node("executor_tools", ExecutorToolNode(
            [tap, focus_and_input_text], messages_key="executor_messages",
        ))
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "orchestrator")
        graph.add_edge("orchestrator", "executor")
        graph.add_conditional_edges("executor", post_executor_gate, {"invoke_tools": "executor_tools"})
        graph.add_edge("executor_tools", END)
        async for _ in graph.compile().astream(
            {"subgoal_plan": [], "executor_messages": [], "latest_screenshot": screenshot},
            config={"callbacks": [callback]},
            stream_mode=["messages", "custom", "updates", "values"],
        ):
            pass

    asyncio.run(main())
    print("MOBILE_PROGRESS_SDK_RESULT=" + json.dumps(events, ensure_ascii=False))
''')


def test_installed_sdk_emits_plans_and_semantic_actions_without_nested_duplicates_or_raw_state(tmp_path):
    default = _SERVER / ".opencompany" / "mobile" / "engine"
    engine = Path(os.environ.get("MOBILE_USE_UPSTREAM_SOURCE", default)).expanduser().resolve()
    python = engine / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file() or not (engine / "minitap/mobile_use/agents/executor/tool_node.py").is_file():
        pytest.skip("Requires the installed mobile engine, or MOBILE_USE_UPSTREAM_SOURCE with its isolated .venv")

    result = subprocess.run(
        [str(python), "-X", "utf8", "-c", _GRAPH_SCRIPT, str(_SERVER / "nodes/mobile/runtime/progress.py"), str(engine)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONUTF8": "1", "MOBILE_USE_TELEMETRY_ENABLED": "false",
             "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if result.returncode == 77:
        pytest.skip("The isolated mobile SDK dependencies are not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    line = next((line for line in result.stdout.splitlines() if line.startswith(_RESULT_PREFIX)), None)
    assert line is not None, result.stdout + result.stderr
    events = json.loads(line.removeprefix(_RESULT_PREFIX))

    assert [event["steps"] for event in events if event["type"] == "progress"] == [1, 2, 3, 4]
    plans = [event for event in events if event.get("kind") == "plan"]
    assert len(plans) == 2
    assert plans[0]["plan"] == [
        {"id": "open", "description": "Open Settings", "status": "not_started", "reason": ""},
        {"id": "name", "description": "Update display name", "status": "not_started", "reason": ""},
    ]
    assert plans[0]["current_goal"] is None
    assert plans[1]["current_goal"] == "Open Settings"
    assert plans[1]["plan"][0]["status"] == "pending"

    actions = [event for event in events if event.get("kind") == "action"]
    assert [event["state"] for event in actions] == ["started", "completed", "started", "failed"]
    assert actions[0]["message"] == "Tap · Settings"
    assert actions[0]["detail"] == "Open the app settings"
    assert actions[1]["action_id"] == actions[0]["action_id"]
    assert actions[1]["outcome"] == "Tapped Settings."
    assert actions[2]["message"] == "Type 25 characters · Display name"
    assert actions[2]["detail"] == "Enter [typed text] into the display-name field"
    assert actions[3]["action_id"] == actions[2]["action_id"]
    assert actions[2]["action_id"] != actions[0]["action_id"]
    assert actions[3]["duration_ms"] >= 0

    serialized = json.dumps(events)
    for forbidden in (
        "NOT_A_GRAPH_PLAN", "SCREENSHOT_BASE64_MUST_NOT_BE_PUBLISHED", "RAW_INPUT_MUST_NOT_BE_PUBLISHED",
        "private-field-value-12345", "latest_screenshot", "executor_messages", "extra_input", "skipped-call",
    ):
        assert forbidden not in serialized

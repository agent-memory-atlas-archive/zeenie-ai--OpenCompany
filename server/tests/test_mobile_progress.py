"""SDK callback contract without installing mobile-use in the main server."""

from enum import Enum
from types import SimpleNamespace

import pytest

from nodes.mobile.runtime.progress import ProgressCallbacks, action_summary, plan_summary


@pytest.fixture
def progress():
    events = []
    handler = ProgressCallbacks(lambda event_type, **data: events.append({"type": event_type, **data}))
    return handler, events


def start_node(handler, role, step, run_id="node"):
    handler.on_chain_start(None, {"screenshot": "private"}, run_id=run_id, name=role,
                           metadata={"langgraph_node": role, "langgraph_step": step}, tags=[f"graph:step:{step}"])


def test_worker_wire_preserves_event_type_and_semantic_kind(monkeypatch):
    import json
    from io import StringIO
    from nodes.mobile.runtime import worker

    wire = StringIO()
    monkeypatch.setattr(worker, "WIRE", wire)
    handler = ProgressCallbacks(worker.emit)
    handler.on_tool_start({"name": "tap"}, "", run_id="tap", inputs={"target": {"text": "Settings"}})
    event = json.loads(wire.getvalue())
    assert event["v"] == 1
    assert event["type"] == "agent_update"
    assert event["kind"] == "action"
    assert event["message"] == "Tap · Settings"


def test_nested_callbacks_and_parallel_nodes_do_not_duplicate_steps(progress):
    handler, events = progress
    start_node(handler, "cortex", 5)
    handler.on_chain_start(None, {}, run_id="nested", name="RunnableSequence",
                           metadata={"langgraph_node": "cortex", "langgraph_step": 5}, tags=["seq:step:1"])
    start_node(handler, "orchestrator", 5, "parallel")
    start_node(handler, "executor", 6, "later")
    start_node(handler, "orchestrator", 5, "late-callback")
    assert events == [{"type": "progress", "steps": 5}, {"type": "progress", "steps": 6}]


def test_plan_reads_sdk_objects_and_replanning_without_cumulative_thoughts(progress):
    class Status(Enum):
        PENDING = "PENDING"
        SUCCESS = "SUCCESS"

    handler, events = progress
    goal = SimpleNamespace(id="g1", description="Open Settings", status=Status.PENDING, completion_reason=None)
    start_node(handler, "planner", 1)
    handler.on_chain_end({"subgoal_plan": [goal], "agents_thoughts": ["PRIVATE REASONING"],
                          "latest_screenshot": "private"}, run_id="node")
    assert events[-1]["current_goal"] == "Open Settings"
    assert events[-1]["plan"][0]["status"] == "pending"
    start_node(handler, "orchestrator", 2)
    handler.on_chain_end({"subgoal_plan": [goal]}, run_id="node")
    assert len([e for e in events if e["type"] == "agent_update"]) == 1
    goal.status, goal.completion_reason = Status.SUCCESS, "Settings screen is visible."
    start_node(handler, "orchestrator", 3)
    handler.on_chain_end({"subgoal_plan": [goal]}, run_id="node")
    assert events[-1]["current_goal"] is None
    assert events[-1]["plan"][0]["reason"] == "Settings screen is visible."
    assert "private" not in str(events).lower()
    # The parent validates the normalized wire plan again without losing reasons.
    assert plan_summary(events[-1]["plan"]) == events[-1]["plan"]


def test_actual_tool_error_result_is_not_reported_as_success(progress):
    handler, events = progress
    handler.on_tool_start({"name": "tap"}, "NOT JSON", run_id="tap1",
                          inputs={"target": {"text": "Search"}, "agent_thought": "Open search to find the app.",
                                  "state": {"screenshot": "PRIVATE"}})
    handler.on_tool_end(SimpleNamespace(update={"executor_messages": [SimpleNamespace(status="error", content="Element not found")]}), run_id="tap1")
    assert events[0]["message"] == "Tap · Search"
    assert events[0]["detail"] == "Open search to find the app."
    assert events[1]["state"] == "failed"
    assert events[1]["outcome"] == "Element not found"
    assert events[1]["duration_ms"] >= 0
    assert "PRIVATE" not in str(events)


def test_typed_payload_and_echoed_field_contents_never_leave_callbacks(progress):
    handler, events = progress
    handler.on_tool_start({"name": "focus_and_input_text"}, "private", run_id="type1", inputs={
        "text": "my-secret", "target": {"resource_id": "search_field"}, "agent_thought": "Enter my-secret in the field."})
    handler.on_tool_end(SimpleNamespace(update={"executor_messages": [SimpleNamespace(status="success", content="Typed my-secret; field content: private")] }), run_id="type1")
    assert events[0]["message"] == "Type 9 characters · search_field"
    assert "my-secret" not in str(events)
    assert "private" not in str(events)
    assert "[typed text]" in events[0]["detail"]
    assert events[-1]["state"] == "completed"


def test_unknown_result_is_unconfirmed_and_exception_does_not_echo_error(progress):
    handler, events = progress
    handler.on_tool_start({"name": "back"}, "", run_id="back", inputs={})
    handler.on_tool_end("arbitrary output is not evidence of success", run_id="back")
    assert events[-1]["state"] == "returned"
    handler.on_tool_start({"name": "back"}, "", run_id="back", inputs={})
    handler.on_tool_error(RuntimeError("token=private"), run_id="back")
    assert events[-1]["state"] == "failed"
    assert "token=" not in str(events)
    handler.on_tool_start({"name": "arbitrary-secret-tool"}, "private", run_id="other", inputs={})
    handler.on_tool_end("private", run_id="other")
    assert len(events) == 4


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("launch_app", {"app_name": "Settings"}, "Open app · Settings"),
    ("stop_app", {"package_name": "com.android.settings"}, "Close app · com.android.settings"),
    ("tap", {"target": {"bounds": {"x": 10, "y": 20, "width": 80, "height": 40}}}, "Tap · area (10, 20, 80 × 40)"),
    ("swipe_percentages", {"start_x_percent": 50, "start_y_percent": 80, "end_x_percent": 50, "end_y_percent": 20}, "Swipe · (50, 80) → (50, 20) %"),
    ("press_key", {"key": "home"}, "Press · Home"),
    ("wait_for_delay", {"time_in_ms": 1500}, "Wait · 1.5s"),
])
def test_concrete_action_labels(name, args, expected):
    assert action_summary(name, args)["message"] == expected


def test_bounds_and_links_are_filtered():
    assert action_summary("open_link", {"url": "https://private.test/?token=secret", "agent_thought": "Open https://private.test/?token=secret now"}) == {
        "message": "Open link", "detail": "Open [link] now"}
    goals = [{"id": "a" * 200, "description": "x" * 1000, "status": "NOT_STARTED"}] * 100
    plan = plan_summary(goals)
    assert len(plan) == 40
    assert len(plan[0]["description"]) == 300
    assert len(plan[0]["id"]) == 100
    assert plan_summary({"unexpected": "private"}) is None

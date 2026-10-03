"""Working steps (services/chat/steps.py): a tool call of the agent answering
a chat run shows as a step labelled by its plugin, hidden tools show none,
the step detail is taken out of the result, finished steps are saved on the
run, and a tool call of a stopped run is not run."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from services.chat import ledger, steps

STREAM = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf", "reply_message_id": "a_r_1"}


@pytest.fixture
def published(monkeypatch):
    sent: List[Dict[str, Any]] = []

    class Hub:
        def publish(self, **kwargs):
            sent.append(kwargs)

    import services.chat.hub as hub_module

    monkeypatch.setattr(hub_module, "get_chat_hub", lambda: Hub())
    return sent


@pytest.fixture
def saved(monkeypatch):
    calls: List[Any] = []

    async def record_step(database, run_id, step):
        calls.append((run_id, step))

    monkeypatch.setattr(ledger, "record_step", record_step)
    import core.container as container_module

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: "db"))
    return calls


def test_labels_come_from_the_plugin():
    assert steps.step_label(SimpleNamespace(chat_step="Searched the web", display_name="Brave Search")) == "Searched the web"
    assert steps.step_label(SimpleNamespace(chat_step="", display_name="Gmail")) == "Used Gmail"
    assert steps.step_label(SimpleNamespace(chat_step_hidden=True, display_name="Current Time")) is None


def test_the_shipped_labels():
    from nodes.search.brave_search import BraveSearchNode
    from nodes.tool.current_time_tool import CurrentTimeToolNode
    from nodes.tool.write_todos import WriteTodosNode

    assert steps.step_label(BraveSearchNode) == "Searched the web"
    assert steps.step_label(CurrentTimeToolNode) is None
    assert steps.step_label(WriteTodosNode) is None


async def test_a_tool_call_of_the_answering_agent_is_a_step(published, saved):
    tool = SimpleNamespace(chat_step="Searched the web")
    step = steps.begin({"chat_stream": STREAM, "tool_call_id": "call_1"}, tool)
    assert step is not None
    await steps.end(step, state="done", detail="3 results for salons near me" + "." * 300)
    started, finished = published
    assert (started["suffix"], started["fields"], started["event_key"]) == (
        "step.started",
        {"step_id": "call_1", "step_name": "Searched the web"},
        "step:call_1:start",
    )
    assert finished["suffix"] == "step.finished" and finished["event_key"] == "step:call_1:end"
    assert finished["fields"]["state"] == "done" and len(finished["fields"]["detail"]) == 200
    assert finished["fields"]["duration_ms"] >= 0
    [(run_id, record)] = saved
    assert run_id == "r_1"
    assert record["name"] == "Searched the web" and record["step_id"] == "call_1" and record["state"] == "done"


def test_other_calls_are_not_steps(published):
    tool = SimpleNamespace(chat_step="Searched the web")
    # No chat stream (another agent, a canvas run), no call id, a hidden tool.
    assert steps.begin({"tool_call_id": "call_1"}, tool) is None
    assert steps.begin({"chat_stream": STREAM}, tool) is None
    assert steps.begin({"chat_stream": {"run_id": "r_1"}, "tool_call_id": "call_1"}, tool) is None
    assert steps.begin({"chat_stream": STREAM, "tool_call_id": "call_1"}, SimpleNamespace(chat_step_hidden=True)) is None
    assert published == []


async def test_ending_no_step_does_nothing(saved):
    await steps.end(None, state="done")
    assert saved == []


@pytest.mark.parametrize(
    ("result", "remaining"),
    [
        ({"success": True, "_step_detail": "Found 3", "x": 1}, {"success": True, "x": 1}),
        ({"success": True, "result": {"_step_detail": "Found 3", "x": 1}}, {"success": True, "result": {"x": 1}}),
    ],
)
def test_the_detail_is_taken_out_of_the_result(result, remaining):
    assert steps.take_detail(result) == "Found 3"
    assert result == remaining


def test_no_detail():
    assert steps.take_detail({"success": True}) is None
    assert steps.take_detail({"_step_detail": "   "}) is None
    assert steps.take_detail("plain") is None


async def test_a_call_of_a_stopped_run_is_not_run(monkeypatch):
    import core.container as container_module

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: "db"))
    stopping = {"r_1": True, "r_2": False}

    async def is_stopping(database, run_id):
        return stopping[run_id]

    monkeypatch.setattr(ledger, "is_stopping", is_stopping)
    assert await steps.run_stopped({"chat_run_id": "r_1", "tool_call_id": "call_1"}) is True
    assert await steps.run_stopped({"chat_run_id": "r_2", "tool_call_id": "call_1"}) is False
    # Only an agent's tool call is skipped: a node of the run itself runs.
    assert await steps.run_stopped({"chat_run_id": "r_1"}) is False

    async def broken(*_args):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ledger, "is_stopping", broken)
    assert await steps.run_stopped({"chat_run_id": "r_1", "tool_call_id": "call_1"}) is False

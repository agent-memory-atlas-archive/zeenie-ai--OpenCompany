"""A tool call run as a node activity (``BaseNode.as_activity``) for the
agent answering a chat run: it shows as a working step with the detail the
tool gave, the detail never reaches the model, a call of a run the owner
stopped is answered without running, and a search numbers the results the
answer may cite."""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

import nodes  # noqa: F401 -- populate plugin registry
from services.chat import ledger, steps
from services.node_registry import get_node_class

STREAM = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf", "reply_message_id": "a_r_1"}
CONTEXT = {
    "node_id": "wf:braveSearch:1",
    "workflow_id": "wf",
    "node_data": {},
    "tool_call_id": "call_1",
    "chat_run_id": "r_1",
    "chat_stream": STREAM,
}


@pytest.fixture
def surroundings(monkeypatch):
    from core.container import container
    import services.chat.hub as hub_module

    workflow_service = MagicMock()
    workflow_service.execute_node = AsyncMock(
        return_value={"success": True, "result": {"results": ["a", "b", "c"], "_step_detail": "3 results"}}
    )
    monkeypatch.setattr(container, "workflow_service", lambda: workflow_service)
    monkeypatch.setattr(container, "database", lambda: "db")

    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()
    broadcaster.update_node_output = AsyncMock()
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)

    published: List[Dict[str, Any]] = []

    class Hub:
        def publish(self, **kwargs):
            published.append(kwargs)

    monkeypatch.setattr(hub_module, "get_chat_hub", lambda: Hub())
    saved: List[Any] = []

    async def record_step(database, run_id, step):
        saved.append((run_id, step))

    stopping = {"value": False}

    async def is_stopping(database, run_id):
        return stopping["value"]

    monkeypatch.setattr(ledger, "record_step", record_step)
    monkeypatch.setattr(ledger, "is_stopping", is_stopping)
    return type(
        "Surroundings",
        (),
        {"service": workflow_service, "broadcaster": broadcaster, "published": published, "saved": saved, "stopping": stopping},
    )


async def test_a_tool_call_shows_as_a_step(surroundings):
    activity_fn = get_node_class("braveSearch").as_activity()
    result = await ActivityEnvironment().run(activity_fn, dict(CONTEXT))
    # The model reads the result without the detail.
    assert result["result"] == {"results": ["a", "b", "c"]}
    started, finished = surroundings.published
    assert (started["suffix"], started["fields"]["step_name"]) == ("step.started", "Searched the web")
    assert (finished["suffix"], finished["fields"]["state"], finished["fields"]["detail"]) == ("step.finished", "done", "3 results")
    [(run_id, step)] = surroundings.saved
    assert run_id == "r_1" and step["detail"] == "3 results"


async def test_a_failed_tool_call_is_a_failed_step(surroundings):
    surroundings.service.execute_node.return_value = {"success": False, "error": "quota", "error_type": "NodeUserError"}
    activity_fn = get_node_class("braveSearch").as_activity()
    await ActivityEnvironment().run(activity_fn, dict(CONTEXT))
    assert surroundings.published[-1]["fields"]["state"] == "failed"


async def test_a_call_of_a_stopped_run_is_not_run(surroundings):
    surroundings.stopping["value"] = True
    activity_fn = get_node_class("braveSearch").as_activity()
    result = await ActivityEnvironment().run(activity_fn, dict(CONTEXT))
    assert result["success"] is True and result["result"] == steps.STOPPED_RESULT
    surroundings.service.execute_node.assert_not_awaited()
    assert surroundings.published == []
    statuses = [call.args[1] for call in surroundings.broadcaster.update_node_status.await_args_list]
    assert statuses == ["skipped"]


async def test_a_node_outside_a_chat_run_is_untouched(surroundings):
    surroundings.stopping["value"] = True
    activity_fn = get_node_class("braveSearch").as_activity()
    result = await ActivityEnvironment().run(activity_fn, {"node_id": "wf:braveSearch:1", "workflow_id": "wf", "node_data": {}})
    surroundings.service.execute_node.assert_awaited_once()
    assert result["success"] is True and surroundings.published == []


async def test_a_search_numbers_the_results_the_answer_may_cite(surroundings, monkeypatch):
    from services.chat import parts, sources

    surroundings.service.execute_node.return_value = {
        "success": True,
        "result": {"results": [{"title": "Bloom", "url": "https://bloom.test/"}, {"title": "No link"}]},
    }
    recorded = []

    async def next_numbers(database, session_id, count):
        assert (database, session_id, count) == ("db", "wf", 1)
        return 5

    async def record_part(database, run_id, *, kind, key, payload):
        recorded.append((run_id, kind, key, payload))

    monkeypatch.setattr(sources, "next_numbers", next_numbers)
    monkeypatch.setattr(parts, "record_part", record_part)
    activity_fn = get_node_class("braveSearch").as_activity()
    result = await ActivityEnvironment().run(activity_fn, dict(CONTEXT))
    # The model reads the number on the result it may cite.
    assert result["result"]["results"][0]["n"] == 5 and "n" not in result["result"]["results"][1]
    assert recorded == [("r_1", "sources", "sources:call_1", {"items": [{"n": 5, "title": "Bloom", "url": "https://bloom.test/"}]})]


async def test_results_are_numbered_only_for_a_search_of_the_answering_agent(monkeypatch):
    from services.chat import sources

    calls = []

    async def number_tool_sources(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(sources, "number_tool_sources", number_tool_sources)
    result = {"success": True, "result": {"results": [{"url": "https://ok.test"}]}}
    await steps.number_sources(CONTEXT, get_node_class("calculatorTool"), result)
    await steps.number_sources({**CONTEXT, "chat_stream": None}, get_node_class("braveSearch"), result)
    await steps.number_sources({**CONTEXT, "tool_call_id": None}, get_node_class("braveSearch"), result)
    assert calls == []
    for search in ("braveSearch", "serperSearch", "perplexitySearch", "duckduckgoSearch"):
        assert get_node_class(search).chat_sources is True

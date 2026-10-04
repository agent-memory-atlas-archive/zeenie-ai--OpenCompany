"""A sending tool call through the node's own Temporal activity
(``BaseNode.as_activity``): held, it never reaches the node and the model
reads that it waits; with Ask first off it runs and is recorded; a send
the owner approved runs under its claim; a later attempt (the one before
broke off) never sends again."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals import rules, store
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

ARGS = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}


def context(**extra):
    return {
        "node_id": "wf:whatsappSend:1",
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": "call_1",
        "node_data": {"message_type": "text", **ARGS},
        "tool_args": dict(ARGS),
        **extra,
    }


@pytest.fixture
def service(monkeypatch, harness):
    import core.container as container_module

    workflow_service = MagicMock()
    workflow_service.execute_node = AsyncMock(return_value={"success": True, "result": {"message_id": "m1"}})
    monkeypatch.setattr(
        container_module,
        "container",
        SimpleNamespace(database=lambda: harness.database, workflow_service=lambda: workflow_service),
    )
    harness.broadcaster.update_node_output = AsyncMock()
    return workflow_service


async def test_a_held_call_never_reaches_the_node(harness, service):
    await rules.set_ask_first(harness.database, "wf", True)
    activity_fn = get_node_class("whatsappSend").as_activity()
    result = await ActivityEnvironment().run(activity_fn, context())
    service.execute_node.assert_not_awaited()
    assert result["success"] is True and result["result"]["status"] == "waiting_for_owner"
    (row,) = await store.list_approvals(harness.database, status="pending")
    assert result["approval_id"] == row.id
    assert ("wf:whatsappSend:1", "success") in [(node_id, status) for node_id, status, *_ in harness.broadcaster.statuses]


async def test_with_ask_first_off_the_call_runs(harness, service):
    await rules.set_ask_first(harness.database, "wf", False)
    activity_fn = get_node_class("whatsappSend").as_activity()
    result = await ActivityEnvironment().run(activity_fn, context())
    service.execute_node.assert_awaited_once()
    assert result["success"] is True and result["result"] == {"message_id": "m1"}
    # Outside a chat nothing is recorded.
    assert await store.list_approvals(harness.database) == []


async def test_an_approved_send_runs_only_under_its_claim(harness, service):
    await rules.set_ask_first(harness.database, "wf", True)
    activity_fn = get_node_class("whatsappSend").as_activity()
    await ActivityEnvironment().run(activity_fn, context())
    (row,) = await store.list_approvals(harness.database, status="pending")
    approved = await store.transition(harness.database, row.id, expected_revision=row.revision, from_statuses=("pending",), status="approved")
    sending = await store.transition(
        harness.database, row.id, expected_revision=approved.revision, from_statuses=("approved",), status="sending", values={"claim_token": "t1"}
    )
    from services.approvals.execution import node_context

    send = node_context(sending, "t1")
    result = await ActivityEnvironment().run(activity_fn, send)
    service.execute_node.assert_awaited_once()
    assert service.execute_node.await_args.kwargs["extras"]["tool_args"] == {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}
    assert result["success"] is True
    stale = await ActivityEnvironment().run(activity_fn, node_context(sending, "someone-else"))
    assert stale["success"] is False and stale["error_type"] == "ApprovalNotClaimed"
    service.execute_node.assert_awaited_once()


def attempt(number: int) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, attempt=number)
    return env


async def test_a_later_attempt_does_not_send_again(harness, service):
    await rules.set_ask_first(harness.database, "wf", False)
    result = await attempt(2).run(get_node_class("whatsappSend").as_activity(), context())
    service.execute_node.assert_not_awaited()
    assert result["success"] is False and result["error_type"] == "SendOutcomeUnknown"
    assert "may already have gone out" in result["error"]
    assert ("wf:whatsappSend:1", "error") in [(node_id, status) for node_id, status, *_ in harness.broadcaster.statuses]


async def test_a_later_attempt_in_the_chat_is_recorded_as_unknown(harness, service, monkeypatch):
    from services.chat import parts

    async def show_approval(database, stream, **_):
        return None

    monkeypatch.setattr(parts, "show_approval", show_approval)
    await rules.set_ask_first(harness.database, "wf", False)
    stream = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf"}
    await attempt(3).run(get_node_class("whatsappSend").as_activity(), context(chat_stream=stream))
    service.execute_node.assert_not_awaited()
    (row,) = await store.list_approvals(harness.database, status="failed")
    assert (row.approved_by, row.outcome) == ("auto", "unknown")


async def test_a_later_attempt_still_holds_or_runs_what_does_not_send(harness, service):
    # A draft is made once per call, so holding it again is safe.
    await rules.set_ask_first(harness.database, "wf", True)
    held = await attempt(2).run(get_node_class("whatsappSend").as_activity(), context())
    assert held["result"]["status"] == "waiting_for_owner"
    # A call that does not send (a search) retries as before.
    search = {"operation": "search", "query": "invoice"}
    reading = context(node_id="wf:googleGmail:1", node_data=dict(search), tool_args=dict(search))
    result = await attempt(2).run(get_node_class("googleGmail").as_activity(), reading)
    service.execute_node.assert_awaited_once()
    assert result["success"] is True


async def test_a_later_browser_attempt_resumes_instead_of_refusing(harness, service):
    # The browser's later attempt resumes a wait the worker cut short
    # (NodeWaitInterrupted); Ask first restricts it rather than holding it.
    browsing = {"operation": "click", "ref": "e3"}
    call = context(node_id="wf:browser:1", node_data=dict(browsing), tool_args=dict(browsing))
    for rule in (None, False, True):
        if rule is not None:
            await rules.set_ask_first(harness.database, "wf", rule)
        result = await attempt(2).run(get_node_class("browser").as_activity(), call)
        assert result["success"] is True, rule
    assert service.execute_node.await_count == 3


async def test_the_first_attempt_sends_as_before(harness, service):
    await rules.set_ask_first(harness.database, "wf", False)
    result = await attempt(1).run(get_node_class("whatsappSend").as_activity(), context())
    service.execute_node.assert_awaited_once()
    assert result["success"] is True

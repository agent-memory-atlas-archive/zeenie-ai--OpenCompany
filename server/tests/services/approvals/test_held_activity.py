"""A sending tool call through the node's own Temporal activity
(``BaseNode.as_activity``): held, it never reaches the node and the model
reads that it waits; with Ask first off it runs and is recorded; a send
the owner approved runs under its claim."""

from __future__ import annotations

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

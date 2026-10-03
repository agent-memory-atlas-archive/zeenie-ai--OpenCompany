"""Show UI (nodes/chat/chat_ui): the employee's tool for an interface in its
chat reply. A spec that breaks a rule fails the call with every reason, a
call outside a chat reply shows nothing and says so, a call of the agent
answering the owner shows it, and the tool's schema and description are the
catalog's, pinned and flat."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nodes.chat.chat_ui import ChatUiNode, ChatUiParams
from services.genui.spec import describe_catalog
from services.plugin import NodeContext, NodeUserError

FIXTURES = Path(__file__).resolve().parents[3] / "client" / "src" / "features" / "chat" / "__fixtures__"
STREAM = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf", "reply_message_id": "a_r_1"}


def booking():
    return json.loads((FIXTURES / "saturday-booking.spec.json").read_text(encoding="utf-8"))


def ctx(raw=None) -> NodeContext:
    return NodeContext(node_id="wf:chatUi:1", node_type="chatUi", workflow_id="wf", raw=raw or {})


@pytest.fixture
def shown(monkeypatch):
    calls = []

    async def show_ui(database, stream, *, tool_call_id, spec):
        calls.append({"stream": stream, "tool_call_id": tool_call_id, "spec": spec})
        return "ui_1234"

    monkeypatch.setattr("services.chat.parts.show_ui", show_ui)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: "db")
    return calls


async def test_the_agent_answering_the_owner_shows_it(shown):
    result = await ChatUiNode().show(ctx({"chat_stream": STREAM, "tool_call_id": "call_1"}), ChatUiParams(spec=booking()))
    assert (result.shown, result.ui_id, result.elements) == (True, "ui_1234", 11)
    assert result.dropped is None and result.notes is None
    [call] = shown
    assert call["stream"] == STREAM and call["tool_call_id"] == "call_1"
    assert set(call["spec"]["elements"]) == set(booking()["elements"])


async def test_outside_a_chat_reply_nothing_shows(shown):
    for raw in ({}, {"chat_stream": STREAM}, {"tool_call_id": "call_1"}):
        result = await ChatUiNode().show(ctx(raw), ChatUiParams(spec=booking()))
        assert result.shown is False and "only in your reply" in result.message
    assert shown == []


async def test_a_spec_that_breaks_a_rule_is_refused_with_every_reason(shown):
    bad = booking()
    bad["elements"]["svc"]["props"]["options"] = ["Only one"]
    bad["elements"]["hold"]["on"]["press"]["action"] = "pop"
    with pytest.raises(NodeUserError) as error:
        await ChatUiNode().show(ctx({"chat_stream": STREAM, "tool_call_id": "call_1"}), ChatUiParams(spec=bad))
    message = str(error.value)
    assert "call show_ui again" in message and "options" in message and "pop is not available" in message
    assert shown == []


async def test_what_was_left_out_is_reported(shown):
    spec = booking()
    spec["elements"]["ghost"] = {"type": "Marquee", "props": {}}
    result = await ChatUiNode().show(ctx({"chat_stream": STREAM, "tool_call_id": "call_1"}), ChatUiParams(spec=spec))
    assert result.dropped == [{"id": "ghost", "reason": "unknown type 'Marquee'"}]


def test_a_spec_sent_as_a_json_string_is_read():
    assert ChatUiParams(spec=json.dumps(booking())).spec == booking()


def test_the_tool_is_the_catalogs_pinned_and_flat():
    schema = ChatUiNode.as_tool_schema()
    assert schema["name"] == "show_ui"
    assert ChatUiNode.tool_schema_locked is True
    assert ChatUiNode.tool_description == describe_catalog()
    assert ChatUiNode.chat_step_hidden is True

    def no_refs(value):
        if isinstance(value, dict):
            assert "$defs" not in value and "$ref" not in value
            for child in value.values():
                no_refs(child)
        elif isinstance(value, list):
            for child in value:
                no_refs(child)

    no_refs(schema)


def test_the_tool_is_allowed_for_hires():
    allowlist = json.loads((Path(__file__).resolve().parents[2] / "config" / "node_allowlist.json").read_text(encoding="utf-8"))
    assert "chatUi" in allowlist["enabled_nodes"]

"""The owner's chat message reaches chatTrigger listeners through the canary
path only.

Locks the contract: ``services.chat.events.dispatch_chat_message_received``
routes through :func:`services.events.dispatch.emit` and never through the
legacy ``event_waiter.dispatch`` (removed in Wave 13: chatTrigger is
canary-registered and the legacy collector has no consumers). The envelope
carries the owner's text, so it is never broadcast to every socket.

Source-level assertions, in the style of
``tests/test_credential_broadcasts.py``, catch the wire contract drifting
without standing up Temporal.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, List
from unittest.mock import MagicMock

import pytest

# Stub the root `cli` namespace.
if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


_EVENT_WAITER_DISPATCH_PATTERN = re.compile(r"event_waiter\.dispatch\s*\(")
_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")


class TestChatMessageProducerCanaryEmit:
    """The producer emits via the canary CloudEvents path only."""

    def test_dispatcher_is_async(self):
        from services.chat.events import dispatch_chat_message_received

        assert inspect.iscoroutinefunction(dispatch_chat_message_received)

    def test_dispatcher_uses_canary_path_only(self):
        from services.chat import events

        src = inspect.getsource(events.dispatch_chat_message_received)
        assert _EVENTS_EMIT_PATTERN.search(src), "must call services.events.dispatch.emit(envelope, ...)"
        assert not _EVENT_WAITER_DISPATCH_PATTERN.search(src), (
            "must NOT call event_waiter.dispatch: chatTrigger is canary-registered and the legacy collector has no consumers"
        )

    @pytest.mark.asyncio
    async def test_runtime_emits_canary_envelope(self, monkeypatch):
        from services.chat import events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        result = await events.dispatch_chat_message_received(
            {"message": "hello", "session_id": "sess-1", "timestamp": "2026-05-14T00:00:00"}
        )

        assert result is None
        [call] = emit_calls
        event = call["event"]
        assert event.type == "com.opencompany.chat.message.received"
        assert event.source == "opencompany://services/chat"
        assert event.subject == "sess-1"
        assert call["wire_routing_key"] == "chat_message_received"
        # The envelope carries the owner's text; it reaches the signalled
        # workflows only, never every connected socket.
        assert call["broadcast"] is False
        # No scope passed -> unscoped envelope (broadcast semantics).
        assert event.workflow_id is None

    @pytest.mark.asyncio
    async def test_scope_and_run_id_ride_the_envelope_verbatim(self, monkeypatch):
        """The factory plumbs ``workflow_id`` and the event id without any
        decision logic: the scoping rule lives in the send handler and the
        narrowing in core dispatch. The event id is the run id, so the run
        a listener spawns has a predictable id."""
        from services.chat import events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        await events.dispatch_chat_message_received(
            {"message": "hi", "session_id": "wf-1", "timestamp": "t", "run_id": "r_1"},
            workflow_id="wf-1",
            event_id="r_1",
        )

        event = emit_calls[0]["event"]
        assert event.workflow_id == "wf-1"
        assert event.id == "r_1"
        assert event.data["run_id"] == "r_1"

    def test_the_send_handler_decides_the_scope_not_the_factory(self):
        from services.chat import access, events, handlers

        factory_src = inspect.getsource(events.chat_message_received)
        assert '"default"' not in factory_src, "the factory must not embed the session != 'default' scoping rule"

        handler_src = inspect.getsource(handlers.handle_send_chat_message)
        assert "workflow_id=scope.workflow_id" in handler_src
        assert "session_id == DEFAULT_SESSION" in inspect.getsource(access.authorize_session)

    def test_the_workflow_trusts_the_same_source_and_type(self):
        """MachinaWorkflow reads a chat run id only from an event this
        module produced; the two must agree."""
        from services.chat import events
        from services.temporal import workflow

        assert workflow.CHAT_MESSAGE_SOURCE == events.SOURCE
        assert workflow.CHAT_MESSAGE_TYPE == events.MESSAGE_RECEIVED_TYPE

    def test_the_trigger_listens_on_the_same_type(self):
        import nodes  # noqa: F401 - registers the trigger
        from services.chat import events
        from services.deployment.canary_registry import cloudevent_type_for

        assert cloudevent_type_for("chatTrigger") == events.MESSAGE_RECEIVED_TYPE

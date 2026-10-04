"""Chat runs: the conversation between the owner and a workflow, as runs.

The wire contract is docs-internal/chat_protocol.md. This package never
imports ``nodes/``.

- ``ledger``: admits a run with the owner's message, starts and finishes it,
  and the watchdog's sweep ends runs nothing will.
- ``hub``: delivers run events to the sockets subscribed to a session.
- ``reducer``: a run's snapshot, folded from its events.
- ``events``: the CloudEvents (the owner's message to the listeners, run
  events).
- ``access``: who may use a session.
- ``handlers``: the WebSocket commands.
- ``activities``: the Temporal activities MachinaWorkflow starts and
  finishes runs through.
- ``watchdog``: the periodic sweep main.py runs.
- ``relay``: a standalone worker's events, sent to the backend's hub.

Thread rows (and ``chat.updated``) stay with ``services/chat_thread.py``.

Side-effect import (from main.py) registers the WebSocket commands and the
disconnect listener that drops a closed socket's subscriptions.
"""

from __future__ import annotations

from services.status_broadcaster import register_disconnect_listener as _register_disconnect_listener
from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .handlers import WS_HANDLERS as _CHAT_WS_HANDLERS
from .hub import get_chat_hub
from .relay import WS_HANDLERS as _RELAY_WS_HANDLERS


def _drop_socket(websocket) -> None:
    get_chat_hub().drop_socket(websocket)


_register_ws_handlers(_CHAT_WS_HANDLERS)
_register_ws_handlers(_RELAY_WS_HANDLERS)
_register_disconnect_listener(_drop_socket)

__all__ = ["get_chat_hub"]

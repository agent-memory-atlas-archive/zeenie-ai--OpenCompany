"""A workflow's chat thread: the one write path for chat rows.

The thread is the chat session whose id is the workflow id (``"default"``
is the editor's chat with no workflow open). The owner's messages come in
through ``send_chat_message``; answers come from the "Reply in Chat" node
(``chatReply``). Normal mode shows the thread on the employee's page as
Talk, and the editor shows it in its chat pane.

- ``delivery_for``: where a message sent now goes, by the deployment's
  latest control state: ``"now"`` while it runs (or is starting or
  resuming), ``"queued"`` while it is paused or pausing (the controller
  keeps the message and starts a run on Resume), None otherwise: nothing
  would read it.
- ``record_chat_message``: add a row stamped with the session's live
  generation (``root_execution_id``, as ``chat_execution_id`` says; the
  editor's chat reads one generation, so a row without it would never show
  there), then announce it.
- ``clear_chat_thread``: delete the session's rows, every generation, then
  announce it when there were any.
- ``clear_chat_session``: the owner's Clear. The thread goes, and the
  listeners registered with ``register_chat_cleared_listener`` forget what
  it held: the Context plugin clears the workflow's conversations, so the
  agent starts over with the chat.

A workflow's thread lives as long as its generation and its workflow. A
Reset (every restart, Home's Apply and Turn on Talk included) clears it
through the chat nodes' ``reset_execution_state`` (``chatTrigger`` and
``chatReply``), in the same Reset in which the Context node forgets the
conversation, so no screen shows a conversation the agent no longer has.
Deleting the workflow deletes its thread (a workflow-deleted hook the
``chatReply`` plugin registers).

Every insert, and every clear that removed rows, is announced as
``chat.updated`` (CloudEvent type
``com.opencompany.chat.updated``, data ``{workflow_id, session_id, role}``,
``role`` None for a clear). Identity only: the frame reaches every socket,
and clients refetch the thread through ``get_chat_messages``. Broadcast
directly through the status broadcaster, not ``services.events.dispatch
.emit``: no trigger consumes it, so the canary path would run a Visibility
query that matches nothing on every message (the same reason as
``context.updated``).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, List, Optional

from core.logging import get_logger
from services.events.envelope import WorkflowEvent

logger = get_logger(__name__)

#: ``await listener(database=..., workflow_id=...)`` after the owner clears
#: a workflow's chat. Plugins register (this module never imports
#: ``nodes/``); a Reset and a workflow delete forget the conversation
#: through their own paths.
ChatClearedListener = Callable[..., Awaitable[None]]
_CLEARED_LISTENERS: List[ChatClearedListener] = []

#: The editor's chat when no workflow is open: not a workflow's thread.
DEFAULT_SESSION = "default"
WIRE_KEY = "chat.updated"
SOURCE = "opencompany://services/chat_thread"

#: Control states whose controller takes a message now.
_TAKES_NOW = frozenset({"starting", "running", "resuming"})
#: Control states whose controller keeps a message for Resume.
_KEEPS_FOR_RESUME = frozenset({"pausing", "paused"})


def chat_execution_id(control: Any) -> Optional[str]:
    """The generation a chat row belongs to: the live one's
    ``root_execution_id``, or None when nothing was started since the last
    Reset."""
    return control.root_execution_id if control is not None and control.status != "reset" else None


def delivery_for(control: Any) -> Optional[str]:
    """``"now"``, ``"queued"``, or None when no controller would read a
    message sent now."""
    status = control.status if control is not None else None
    if status in _TAKES_NOW:
        return "now"
    if status in _KEEPS_FOR_RESUME:
        return "queued"
    return None


def chat_updated(*, session_id: str, role: Optional[str]) -> WorkflowEvent:
    """A thread gained a message (``role``) or was cleared (``role`` None)."""
    return WorkflowEvent(
        source=SOURCE,
        type="com.opencompany.chat.updated",
        subject=session_id,
        data={
            "workflow_id": session_id if session_id != DEFAULT_SESSION else None,
            "session_id": session_id,
            "role": role,
        },
    )


async def _announce(session_id: str, role: Optional[str]) -> None:
    from services.status_broadcaster import get_status_broadcaster

    event = chat_updated(session_id=session_id, role=role)
    try:
        await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)})
    except Exception:
        logger.warning("chat.updated broadcast failed", session_id=session_id, exc_info=True)


async def record_chat_message(database: Any, session_id: str, role: str, message: str) -> bool:
    """Add ``message`` to the thread, in the session's live generation, and
    announce it. False when the row could not be saved (nothing is
    announced)."""
    control = await database.get_latest_workflow_control(session_id)
    saved = await database.add_chat_message(session_id, role, message, execution_id=chat_execution_id(control))
    if saved:
        await _announce(session_id, role)
    return bool(saved)


async def clear_chat_thread(database: Any, session_id: str) -> int:
    """Delete the thread, every generation of it, and announce it when there
    was anything to delete (a Reset runs one clear per chat node). Returns
    the rows deleted."""
    count = await database.clear_chat_messages(session_id)
    if count:
        await _announce(session_id, None)
    return count


def register_chat_cleared_listener(listener: ChatClearedListener) -> None:
    """Run ``await listener(database=..., workflow_id=...)`` after the owner
    clears a workflow's chat. Registering the same listener twice is a
    no-op."""
    if listener not in _CLEARED_LISTENERS:
        _CLEARED_LISTENERS.append(listener)


async def clear_chat_session(database: Any, session_id: str) -> int:
    """The owner's Clear: delete the thread, then let the listeners forget
    what it held, so the agent starts over with the chat. Only a workflow's
    session has listeners to tell (``"default"`` is no workflow's). A
    failing listener is logged and never fails the clear. Returns the rows
    deleted."""
    count = await clear_chat_thread(database, session_id)
    if session_id == DEFAULT_SESSION:
        return count
    for listener in list(_CLEARED_LISTENERS):
        try:
            await listener(database=database, workflow_id=session_id)
        except Exception:
            logger.warning(
                "Chat-cleared listener failed",
                listener=getattr(listener, "__qualname__", repr(listener)),
                workflow_id=session_id,
                exc_info=True,
            )
    return count


__all__ = [
    "DEFAULT_SESSION",
    "WIRE_KEY",
    "ChatClearedListener",
    "chat_execution_id",
    "chat_updated",
    "clear_chat_session",
    "clear_chat_thread",
    "delivery_for",
    "record_chat_message",
    "register_chat_cleared_listener",
]

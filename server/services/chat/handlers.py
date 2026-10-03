"""Chat's WebSocket commands (docs-internal/chat_protocol.md).

Moved here from ``routers/websocket.py`` with the run runtime. Every command
that names a session checks the socket first (``services/chat/access.py``).

``send_chat_message`` saves the owner's message and dispatches it to the
workflow's chat triggers. A workflow's session takes a message only while
its deployment would read it (``delivery``: ``"now"`` while it runs,
``"queued"`` while it is paused); otherwise nothing is saved and the answer
is ``not_running``. When a deployed chat trigger will answer the session,
the message starts a run, admitted in the same transaction
(``services/chat/ledger.py``), and the response names it. The editor's
``"default"`` session is saved and dispatched to every deployment, without a
run, as it always was.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.container import container
from core.logging import get_logger
from services.chat import ledger, reducer
from services.chat.access import ChatAccessDenied, authorize_session, session_id_of
from services.chat.events import MESSAGE_WIRE_ROUTING_KEY, dispatch_chat_message_received
from services.chat.hub import get_chat_hub
from services.chat_thread import (
    DEFAULT_SESSION,
    announce_chat_updated,
    chat_execution_id,
    clear_chat_session,
    delivery_for,
    record_chat_message,
)
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)

PROTOCOL_VERSION = 1
_DENIED = {"success": False, "error": "access_denied"}


def _wire_run(run: Any) -> Dict[str, Any]:
    """The run a message started or answers, as a message carries it: enough
    to show how it ended after a reload (a failed run leaves no reply)."""
    wire: Dict[str, Any] = {"run_id": run.run_id, "state": run.state, "outcome": run.outcome}
    if run.state == "error":
        wire["error"] = {"message": run.error, "code": run.error_code}
        hint = (run.result or {}).get("hint")
        if hint:
            wire["error"]["hint"] = hint
    return wire


def _wire_message(row: Dict[str, Any], runs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A stored message as ``get_chat_messages`` sends it."""
    message_id = row.get("uid") or f"m{row.get('id')}"
    wire: Dict[str, Any] = {
        "id": message_id,
        "legacy_id": row.get("id"),
        "role": row.get("role"),
        "kind": row.get("kind") or "text",
        "text": row.get("message"),
        "message": row.get("message"),
        "timestamp": row.get("timestamp"),
        "run_key": row.get("execution_id"),
        "run_id": row.get("run_id"),
        "parent_id": row.get("parent_uid"),
        "status": row.get("status") or "complete",
        "attachments": row.get("attachments") or [],
        "parts": row.get("parts") or {},
        "feedback": None,
        "siblings": {"index": 0, "count": 1, "ids": [message_id]},
        # Editing arrives with branches; until then no message is editable.
        "editable": False,
    }
    client_message_id = (row.get("meta") or {}).get("client_message_id")
    if client_message_id:
        wire["client_message_id"] = client_message_id
    run = (runs or {}).get(row.get("run_id") or "")
    if run is not None:
        wire["run"] = _wire_run(run)
    return wire


async def run_snapshots(database: Any, session_id: str) -> List[Dict[str, Any]]:
    """Snapshots of a session's live runs. The hub is read before the rows,
    so an event published in between carries a newer ``seq`` than the
    snapshot and still reaches a subscriber."""
    hub = get_chat_hub()
    live = hub.session_live(session_id)
    rows = await ledger.live_runs(database, session_id)
    return [{**reducer.merge_snapshot(reducer.snapshot_from_row(run), live.get(run.run_id)), "hub_epoch": hub.epoch} for run in rows]


async def _thread_state(database: Any, session_id: str) -> Dict[str, Any]:
    from models.chat import ChatThread

    async with database.get_session() as session:
        thread = await session.get(ChatThread, session_id)
    return {
        "active_leaf_id": thread.active_leaf_uid if thread is not None else None,
        "revision": thread.revision if thread is not None else 0,
    }


async def _answers_session(database: Any, control: Any, session_id: str) -> bool:
    """Whether a chat trigger in the deployed graph accepts this session, so
    a run started for the message will be picked up. Decided from the
    trigger's own filter (``event_waiter.build_filter``), never from a node
    type name."""
    from services import event_waiter

    graph = getattr(control, "graph_snapshot", None) or {}
    for node in graph.get("nodes") or []:
        node_type = str(node.get("type") or "")
        config = event_waiter.get_trigger_config(node_type)
        if config is None or config.event_type != MESSAGE_WIRE_ROUTING_KEY:
            continue
        if (node.get("data") or {}).get("disabled"):
            continue
        params = await database.get_node_parameters(str(node.get("id") or "")) or {}
        try:
            if event_waiter.build_filter(node_type, params)({"session_id": session_id, "message": ""}):
                return True
        except Exception:  # noqa: BLE001 - a broken filter admits, as the listener's does
            return True
    return False


@ws_handler("message")
async def handle_send_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save the owner's message, start its run, and dispatch it. Answers
    ``{message_id, run_id, delivery, timestamp}``; ``run_in_progress`` (with
    the live ``run_id``), ``not_running``, ``save_failed``, ``access_denied``
    or ``invalid_request`` otherwise."""
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return {"success": False, "error": "invalid_request", "detail": "message must be text"}
    if data.get("role", "user") != "user":
        return {"success": False, "error": "invalid_request", "detail": "send_chat_message sends the owner's messages"}
    session_id = session_id_of(data)
    timestamp = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
    database = container.database()
    try:
        scope = await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)

    # The scope rides the envelope's ``workflow_id``, so ``dispatch.emit``
    # signals only this workflow's listeners (without it one workflow's chat
    # fired every deployed chat trigger). The editor's "default" session
    # keeps its unscoped delivery.
    control = None
    delivery: Optional[str] = None
    if scope.workflow_id is not None:
        control = await database.get_latest_workflow_control(session_id)
        delivery = delivery_for(control)
        if delivery is None:
            return {"success": False, "error": "not_running"}
    track = scope.workflow_id is not None and await _answers_session(database, control, session_id)

    try:
        admission = await ledger.admit_message(
            database,
            session_id=session_id,
            workflow_id=scope.workflow_id,
            execution_id=chat_execution_id(control) if scope.workflow_id is not None else None,
            text=message,
            track=track,
            state="queued" if delivery == "queued" else "pending",
            client_message_id=data.get("client_message_id"),
        )
    except ledger.RunInProgress as exc:
        return {"success": False, "error": "run_in_progress", "run_id": exc.run.run_id}
    except ValueError as exc:
        return {"success": False, "error": "invalid_request", "detail": str(exc)}
    except Exception:
        logger.warning("Chat message could not be saved", session_id=session_id, exc_info=True)
        return {"success": False, "error": "save_failed"}

    row, run = admission.message, admission.run
    if admission.created:
        await announce_chat_updated(session_id, "user")
        event_data: Dict[str, Any] = {
            "message": message,
            "timestamp": timestamp,
            "session_id": session_id,
            "message_id": row["uid"],
        }
        if run is not None:
            event_data["run_id"] = run.run_id
        await dispatch_chat_message_received(
            event_data,
            workflow_id=scope.workflow_id,
            event_id=run.run_id if run is not None else None,
        )
        logger.info("Chat message dispatched", session_id=session_id, run_id=run.run_id if run is not None else None)

    response: Dict[str, Any] = {
        "success": True,
        "message": "Chat message sent",
        "timestamp": timestamp,
        "message_id": row["uid"],
        "run_id": run.run_id if run is not None else None,
    }
    if delivery is not None:
        response["delivery"] = delivery
    return response


@ws_handler()
async def handle_get_chat_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """A session's messages, oldest first, the newest ``limit`` of them, with
    the thread's state and its live runs. The live generation's messages
    only, unless ``all_generations`` (Home's thread, which also shows a
    message from before a Start). A read that fails answers ``read_failed``,
    never an empty thread."""
    session_id = session_id_of(data)
    limit = data.get("limit")
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    try:
        if data.get("all_generations"):
            rows = await database.read_chat_messages(session_id, limit)
        else:
            control = await database.get_latest_workflow_control(session_id)
            if control is not None and control.status == "reset":
                rows = []
            else:
                rows = await database.read_chat_messages(
                    session_id, limit,
                    execution_id=control.root_execution_id if control is not None else None,
                )
        thread = await _thread_state(database, session_id)
        active_runs = await run_snapshots(database, session_id)
        runs = await ledger.runs_by_id(database, [row.get("run_id") for row in rows])
    except Exception:
        logger.warning("Chat messages could not be read", session_id=session_id, exc_info=True)
        return {"success": False, "error": "read_failed", "session_id": session_id}
    return {
        "success": True,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
        "messages": [_wire_message(row, runs) for row in rows],
        "thread": thread,
        "active_runs": active_runs,
    }


@ws_handler()
async def handle_chat_subscribe(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Send the session's run events to this socket from now on. Answers
    the hub's epoch and the live runs' snapshots; events after a snapshot
    carry a higher ``seq`` than it."""
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    hub = get_chat_hub()
    hub.subscribe(websocket, session_id)
    try:
        active_runs = await run_snapshots(database, session_id)
    except Exception:
        logger.warning("Chat runs could not be read", session_id=session_id, exc_info=True)
        return {"success": False, "error": "read_failed", "session_id": session_id, "hub_epoch": hub.epoch}
    return {"success": True, "session_id": session_id, "hub_epoch": hub.epoch, "active_runs": active_runs}


@ws_handler()
async def handle_chat_unsubscribe(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Stop sending the session's run events to this socket."""
    session_id = session_id_of(data)
    hub = get_chat_hub()
    hub.unsubscribe(websocket, session_id)
    return {"success": True, "session_id": session_id, "hub_epoch": hub.epoch}


@ws_handler("run_id")
async def handle_get_chat_run(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """One run's snapshot, live or ended."""
    database = container.database()
    run = await ledger.get_run(database, str(data["run_id"]))
    if run is None:
        return {"success": False, "error": "not_found"}
    try:
        await authorize_session(database, websocket, run.session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    hub = get_chat_hub()
    live = hub.live_snapshot(run.run_id)
    run = await ledger.get_run(database, run.run_id) or run
    snapshot = reducer.merge_snapshot(reducer.snapshot_from_row(run), live)
    return {"success": True, "run": {**snapshot, "hub_epoch": hub.epoch}}


@ws_handler()
async def handle_clear_chat_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Clear a session's chat, every generation of it, with its runs. For a
    workflow's session the agent forgets the conversation too
    (``services.chat_thread.clear_chat_session``)."""
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    count = await clear_chat_session(database, session_id)
    return {"success": True, "message": f"Cleared {count} chat messages", "cleared_count": count}


@ws_handler("message", "role")
async def handle_save_chat_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save one message with the given role, without dispatching it."""
    role = data["role"]
    if role not in ("user", "assistant"):
        return {"success": False, "error": "invalid_request", "detail": "role is user or assistant"}
    session_id = session_id_of(data)
    database = container.database()
    try:
        await authorize_session(database, websocket, session_id)
    except ChatAccessDenied:
        return dict(_DENIED)
    saved = await record_chat_message(database, session_id, role, data["message"])
    return {"success": bool(saved), "message": "Chat message saved" if saved else "Failed to save chat message"}


WS_HANDLERS = {
    "send_chat_message": handle_send_chat_message,
    "get_chat_messages": handle_get_chat_messages,
    "chat_subscribe": handle_chat_subscribe,
    "chat_unsubscribe": handle_chat_unsubscribe,
    "get_chat_run": handle_get_chat_run,
    "clear_chat_messages": handle_clear_chat_messages,
    "save_chat_message": handle_save_chat_message,
}

__all__ = ["DEFAULT_SESSION", "PROTOCOL_VERSION", "WS_HANDLERS", "run_snapshots"]

"""Live run events for the sockets that asked for them.

A socket subscribes to a chat session (``chat_subscribe``, once the handler
has authorized it) and from then on receives that session's
``chat_run_event`` frames, in order, from its own writer task, so a slow
socket never holds up the publisher or any other socket. Run events never
go through ``StatusBroadcaster.broadcast``, which reaches every socket.

- ``seq`` counts a run's events from 1 within this process. ``epoch`` names
  the process: after a restart the counters start over and the epoch
  changes, which tells a client to take a fresh snapshot.
- An event whose ``event_key`` the run already published is dropped, so a
  retried activity publishes once. Nothing is published for a run after its
  terminal event.
- The hub keeps each live run's snapshot (``services/chat/reducer.py``), so a
  client that subscribes mid-run gets what the earlier events built.
- Each socket queues at most ``hub.subscriber_queue_size`` frames
  (``config/chat_defaults.json``). A socket that falls that far behind has
  its queue replaced by one ``opencompany.resync`` frame per session it
  follows: the client takes a fresh snapshot instead of reading a stream
  with a hole in it.

Delivery is in-process. An activity on a worker in another process
publishes into that process's hub, which no socket here reads.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, Mapping, Optional, Set
from uuid import uuid4

import orjson
from starlette.websockets import WebSocketState

from core.logging import get_logger
from services.chat import reducer
from services.chat.events import RUN_WIRE_KEY, SOURCE, TERMINAL_SUFFIXES, chat_run_event
from services.events.envelope import WorkflowEvent

logger = get_logger(__name__)

#: Ended runs remembered so a late publish for one is dropped.
_ENDED_RUNS_KEPT = 2048


def _open(websocket: Any) -> bool:
    return (
        getattr(websocket, "client_state", WebSocketState.CONNECTED) is WebSocketState.CONNECTED
        and getattr(websocket, "application_state", WebSocketState.CONNECTED) is WebSocketState.CONNECTED
    )


class _Subscriber:
    """One socket's subscriptions and its writer."""

    def __init__(self, hub: "ChatRunHub", websocket: Any) -> None:
        self.hub = hub
        self.websocket = websocket
        self.sessions: Set[str] = set()
        self.frames: Deque[str] = deque()
        self.wake = asyncio.Event()
        self.task: Optional[asyncio.Task] = None

    def offer(self, frame: str) -> None:
        if len(self.frames) >= self.hub.queue_size:
            self.frames.clear()
            for session_id in sorted(self.sessions):
                self.frames.append(self.hub.resync_frame(session_id))
            logger.info("Chat subscriber fell behind; sent resync", sessions=len(self.sessions))
        self.frames.append(frame)
        self.wake.set()
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._write(), name="chat-hub-writer")

    async def _write(self) -> None:
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                while self.frames:
                    frame = self.frames.popleft()
                    if not _open(self.websocket):
                        self.hub.drop_socket(self.websocket)
                        return
                    await self.websocket.send_text(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the socket is gone; forget it
            logger.debug("Chat hub send failed; dropping the socket", error=str(exc))
            self.hub.drop_socket(self.websocket)

    def close(self) -> None:
        self.frames.clear()
        if self.task is not None and not self.task.done() and self.task is not asyncio.current_task():
            self.task.cancel()


class ChatRunHub:
    def __init__(self, *, queue_size: Optional[int] = None) -> None:
        if queue_size is None:
            from services.chat.config import hub_setting

            queue_size = hub_setting("subscriber_queue_size")
        self.queue_size = max(1, int(queue_size))
        self.epoch = f"e_{uuid4().hex[:12]}"
        # Keyed by the socket object itself, never ``id()``: a reused id
        # would hand a new socket an old one's sessions.
        self._subscribers: Dict[Any, _Subscriber] = {}
        self._sessions: Dict[str, Set[Any]] = {}
        self._seq: Dict[str, int] = {}
        self._keys: Dict[str, Set[str]] = {}
        self._live: Dict[str, Dict[str, Any]] = {}
        self._ended: "OrderedDict[str, None]" = OrderedDict()

    # ---- subscriptions -------------------------------------------------

    def subscribe(self, websocket: Any, session_id: str) -> None:
        subscriber = self._subscribers.get(websocket)
        if subscriber is None:
            subscriber = _Subscriber(self, websocket)
            self._subscribers[websocket] = subscriber
        subscriber.sessions.add(session_id)
        self._sessions.setdefault(session_id, set()).add(websocket)

    def unsubscribe(self, websocket: Any, session_id: Optional[str] = None) -> None:
        """Stop sending one session (or, without ``session_id``, any) to
        the socket."""
        subscriber = self._subscribers.get(websocket)
        if subscriber is None:
            return
        sessions = [session_id] if session_id is not None else list(subscriber.sessions)
        for session in sessions:
            subscriber.sessions.discard(session)
            members = self._sessions.get(session)
            if members is not None:
                members.discard(websocket)
                if not members:
                    del self._sessions[session]
        if not subscriber.sessions:
            subscriber.close()
            del self._subscribers[websocket]

    def drop_socket(self, websocket: Any) -> None:
        """Forget a closed socket (the status broadcaster calls this on
        disconnect)."""
        self.unsubscribe(websocket)

    def subscribed(self, websocket: Any, session_id: str) -> bool:
        subscriber = self._subscribers.get(websocket)
        return subscriber is not None and session_id in subscriber.sessions

    # ---- runs ----------------------------------------------------------

    def current_seq(self, run_id: str) -> int:
        return self._seq.get(run_id, 0)

    def live_snapshot(self, run_id: str) -> Optional[Dict[str, Any]]:
        snapshot = self._live.get(run_id)
        return dict(snapshot) if snapshot is not None else None

    def session_live(self, session_id: str) -> Dict[str, Dict[str, Any]]:
        """The live snapshots of a session's runs, by run id. Read it before
        reading the runs' rows: an event published in between then reaches a
        subscriber with a ``seq`` above its snapshot's, never below."""
        return {run_id: dict(snapshot) for run_id, snapshot in self._live.items() if snapshot.get("session_id") == session_id}

    def ended(self, run_id: str) -> bool:
        return run_id in self._ended

    def publish(
        self,
        *,
        run_id: str,
        session_id: str,
        workflow_id: Optional[str],
        suffix: str,
        fields: Optional[Mapping[str, Any]] = None,
        event_key: Optional[str] = None,
    ) -> Optional[WorkflowEvent]:
        """Publish one run event to the session's subscribers. Returns the
        event, or None when it was dropped (a repeated ``event_key``, or the
        run already ended). Never raises for delivery problems: a socket that
        cannot be written to is dropped."""
        if run_id in self._ended:
            return None
        keys = self._keys.setdefault(run_id, set())
        if event_key is not None:
            if event_key in keys:
                return None
            keys.add(event_key)
        seq = self._seq.get(run_id, 0) + 1
        self._seq[run_id] = seq
        event = chat_run_event(
            suffix=suffix,
            run_id=run_id,
            seq=seq,
            hub_epoch=self.epoch,
            workflow_id=workflow_id,
            session_id=session_id,
            fields=fields,
        )
        payload = event.model_dump(mode="json", exclude_none=True)
        self._live[run_id] = reducer.apply_event(self._live.get(run_id) or reducer.empty_snapshot(run_id), payload)
        if suffix in TERMINAL_SUFFIXES:
            self._end(run_id)
        self._fan_out(session_id, orjson.dumps({"type": RUN_WIRE_KEY, "data": payload}).decode())
        return event

    def _end(self, run_id: str) -> None:
        self._seq.pop(run_id, None)
        self._keys.pop(run_id, None)
        self._live.pop(run_id, None)
        self._ended[run_id] = None
        while len(self._ended) > _ENDED_RUNS_KEPT:
            self._ended.popitem(last=False)

    def _fan_out(self, session_id: str, frame: str) -> None:
        members = self._sessions.get(session_id)
        if not members:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for websocket in list(members):
            subscriber = self._subscribers.get(websocket)
            if subscriber is not None and session_id in subscriber.sessions:
                subscriber.offer(frame)

    def resync_frame(self, session_id: str) -> str:
        event = WorkflowEvent(
            id=f"resync:{uuid4().hex}",
            source=SOURCE,
            type="com.opencompany.chat.run.custom",
            subject=session_id,
            data={
                "session_id": session_id,
                "hub_epoch": self.epoch,
                "name": "opencompany.resync",
                "value": {},
            },
        )
        return orjson.dumps({"type": RUN_WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)}).decode()


_hub: Optional[ChatRunHub] = None


def get_chat_hub() -> ChatRunHub:
    """The process's hub."""
    global _hub
    if _hub is None:
        _hub = ChatRunHub()
    return _hub


def reset_chat_hub_for_tests(hub: Optional[ChatRunHub] = None) -> Optional[ChatRunHub]:
    """Swap the process hub (tests). Returns the previous one."""
    global _hub
    previous, _hub = _hub, hub
    return previous


__all__ = ["ChatRunHub", "get_chat_hub", "reset_chat_hub_for_tests"]

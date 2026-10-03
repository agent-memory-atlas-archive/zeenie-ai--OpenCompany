"""Chat runs: admitted with the owner's message, started and finished by the
run that answers it, and ended by the watchdog when nothing will.

**Admission** (``admit_message``) writes the owner's message and its run in
one reserved transaction (``Database.reserved_session``), after checking the
lane: one live run per session, resume runs aside, also enforced by the
partial unique index on ``chat_runs``. A message re-sent with the same
``client_message_id`` returns the first send's message and run instead of
writing a second one; the id maps to a message id that only this session can
produce (``client_message_uid``).

**Start** (``start_run``) moves ``pending``/``queued`` to ``running`` and
records the Temporal workflow that claimed it. Several chatTrigger nodes in
one graph each spawn a run for the same message: the first claims the chat
run, the others run untracked. **Finish** (``finish_run``) is accepted only
from the claimant. A retried start or finish publishes nothing new: events
carry an ``event_key`` the hub publishes once.

**The watchdog** (``sweep``) ends runs nothing will end: a pending run never
picked up (``not_delivered``, counted from the later of its creation and
this process's start, so a restart does not expire runs it held up), a
queued run whose deployment stopped or did not pick it up after resuming, a
running run past ``runs.max_running_s`` (``timed_out``), and a running run
whose Temporal workflow closed without finishing it: finished when its reply
was saved, ``interrupted`` otherwise.

Every transition commits before its event is published, so a client that
reads the run after an event never sees an older state than the event.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from core.logging import get_logger
from models.chat import LIVE_STATES, TERMINAL_STATES, ChatRun
from models.database import ChatMessage
from services.chat.config import runs_setting
from services.chat.hub import get_chat_hub
from services.chat_thread import delivery_for

logger = get_logger(__name__)

PROCESS_STARTED = datetime.now(timezone.utc)

_CLIENT_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
#: How long a closed Temporal workflow may leave its run open before the
#: watchdog ends it (its finish activity may still be on its way).
_CLOSED_GRACE = timedelta(seconds=60)

#: ``await status(temporal_workflow_id, temporal_run_id)`` -> True when the
#: workflow has closed, False while it runs, None when unknown.
TemporalStatus = Callable[[str, Optional[str]], Awaitable[Optional[bool]]]


class RunInProgress(Exception):
    """The session's lane is held by ``run``."""

    def __init__(self, run: ChatRun) -> None:
        super().__init__("run_in_progress")
        self.run = run


@dataclass(frozen=True)
class Admission:
    #: The owner's message, as ``Database.chat_row`` shapes it.
    message: Dict[str, Any]
    #: Its run, or None for a message that starts none.
    run: Optional[ChatRun]
    #: False when ``client_message_id`` matched an earlier send.
    created: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def new_run_id() -> str:
    from uuid import uuid4

    return f"r_{uuid4().hex}"


def reply_uid(run_id: str) -> str:
    """The id a run's reply is saved under."""
    return f"a_{run_id}"


def client_message_uid(session_id: str, client_message_id: Any) -> str:
    """The message id for a client's ``client_message_id`` in a session.
    Hashed with the session, so a client cannot name another chat's
    message."""
    if not isinstance(client_message_id, str) or not _CLIENT_MESSAGE_ID.match(client_message_id):
        raise ValueError("client_message_id must be 1 to 100 letters, digits or the characters _.:-")
    digest = hashlib.sha256(f"{session_id}\x00{client_message_id}".encode()).hexdigest()[:40]
    return f"m_{digest}"


# ---- reads -------------------------------------------------------------


async def get_run(database: Any, run_id: str) -> Optional[ChatRun]:
    async with database.get_session() as session:
        return await session.get(ChatRun, run_id)


async def lane_run(database: Any, session_id: str) -> Optional[ChatRun]:
    """The run holding the session's lane, if any."""
    async with database.get_session() as session:
        result = await session.execute(
            select(ChatRun)
            .where(ChatRun.session_id == session_id, ChatRun.state.in_(LIVE_STATES), ChatRun.kind != "resume")
            .limit(1)
        )
        return result.scalar_one_or_none()


async def runs_by_id(database: Any, run_ids: List[str]) -> Dict[str, ChatRun]:
    """The named runs that exist, by id (one query)."""
    wanted = sorted({run_id for run_id in run_ids if run_id})
    if not wanted:
        return {}
    async with database.get_session() as session:
        result = await session.execute(select(ChatRun).where(ChatRun.run_id.in_(wanted)))
        return {run.run_id: run for run in result.scalars().all()}


async def live_runs(database: Any, session_id: Optional[str] = None) -> List[ChatRun]:
    """Live runs, oldest first: one session's, or every session's."""
    async with database.get_session() as session:
        query = select(ChatRun).where(ChatRun.state.in_(LIVE_STATES))
        if session_id is not None:
            query = query.where(ChatRun.session_id == session_id)
        result = await session.execute(query.order_by(ChatRun.created_at, ChatRun.run_id))
        return list(result.scalars().all())


async def _message_exists(database: Any, uid: Optional[str]) -> bool:
    if not uid:
        return False
    async with database.get_session() as session:
        result = await session.execute(select(ChatMessage.id).where(ChatMessage.uid == uid))
        return result.scalar_one_or_none() is not None


# ---- admission ---------------------------------------------------------


async def admit_message(
    database: Any,
    *,
    session_id: str,
    workflow_id: Optional[str],
    execution_id: Optional[str],
    text: str,
    track: bool,
    state: str = "pending",
    kind: str = "message",
    message_kind: str = "text",
    client_message_id: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Admission:
    """Save the owner's message and, when ``track``, the run that will answer
    it. Raises :class:`RunInProgress` when the lane is held, ``ValueError``
    for a malformed ``client_message_id``."""
    if state not in ("pending", "queued"):
        raise ValueError(f"a run is admitted pending or queued, not {state!r}")
    uid = client_message_uid(session_id, client_message_id) if client_message_id is not None else None
    meta = {"client_message_id": client_message_id} if client_message_id is not None else {}
    async with database.reserved_session() as session:
        if uid is not None:
            found = (await session.execute(select(ChatMessage).where(ChatMessage.uid == uid))).scalar_one_or_none()
            if found is not None:
                run = await session.get(ChatRun, found.run_id) if found.run_id else None
                return Admission(message=database.chat_row(found), run=run, created=False)
        run_id: Optional[str] = None
        if track:
            held = await session.execute(
                select(ChatRun)
                .where(ChatRun.session_id == session_id, ChatRun.state.in_(LIVE_STATES), ChatRun.kind != "resume")
                .limit(1)
            )
            holder = held.scalar_one_or_none()
            if holder is not None:
                # Detached first: the rollback on the way out would expire it.
                session.expunge(holder)
                raise RunInProgress(holder)
            run_id = new_run_id()
        row = await database.append_chat_row(
            session,
            session_id=session_id,
            role="user",
            message=text,
            execution_id=execution_id,
            uid=uid,
            run_id=run_id,
            kind=message_kind,
            attachments=attachments,
            meta=meta,
        )
        run: Optional[ChatRun] = None
        if run_id is not None:
            run = ChatRun(
                run_id=run_id,
                session_id=session_id,
                workflow_id=workflow_id,
                run_key=execution_id,
                kind=kind,
                state=state,
                user_message_uid=row.uid,
                reply_message_uid=reply_uid(run_id),
                options=dict(options or {}),
            )
            session.add(run)
        try:
            await session.commit()
        except IntegrityError:
            # Without a write reservation (another database) the lane index
            # is what refuses the second run.
            await session.rollback()
            holder = await lane_run(database, session_id)
            if holder is not None:
                raise RunInProgress(holder)
            raise
    return Admission(message=database.chat_row(row), run=run, created=True)


async def post_reply(
    database: Any,
    *,
    run: ChatRun,
    node_id: str,
    text: str,
    execution_id: Optional[str],
) -> Dict[str, Any]:
    """Save a reply to the run's thread and return the row.

    The run's first reply takes the run's reply id (``a_<run id>``), the id
    its events name, so a client can match the answer it watched to the one
    saved. Another reply node in the same run gets ``a_<run id>.<n>``. A
    retry from the same node returns the row it saved. Serialized by the
    write reservation, so two reply nodes at once cannot take the same id.
    """
    async with database.reserved_session() as session:
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.run_id == run.run_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.id)
        )
        replies = list(result.scalars().all())
        for reply in replies:
            if (reply.meta or {}).get("node_id") == node_id:
                return database.chat_row(reply)
        uid = run.reply_message_uid or reply_uid(run.run_id)
        if replies:
            uid = f"{uid}.{len(replies) + 1}"
        row = await database.append_chat_row(
            session,
            session_id=run.session_id,
            role="assistant",
            message=text,
            execution_id=execution_id,
            uid=uid,
            run_id=run.run_id,
            meta={"node_id": node_id},
        )
        await session.commit()
        return database.chat_row(row)


# ---- transitions -------------------------------------------------------


def _publish(run: ChatRun, suffix: str, fields: Dict[str, Any], event_key: str) -> None:
    try:
        get_chat_hub().publish(
            run_id=run.run_id,
            session_id=run.session_id,
            workflow_id=run.workflow_id,
            suffix=suffix,
            fields=fields,
            event_key=event_key,
        )
    except Exception:  # noqa: BLE001 - the row is the record; an event is a courtesy
        logger.warning("Chat run event could not be published", run_id=run.run_id, suffix=suffix, exc_info=True)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    moment = _aware(moment)
    return moment.isoformat() if moment is not None else None


def publish_started(run: ChatRun) -> None:
    fields: Dict[str, Any] = {
        "kind": run.kind,
        "reply_message_id": run.reply_message_uid,
        "started_at": _iso(run.started_at),
    }
    if run.user_message_uid:
        fields["user_message_id"] = run.user_message_uid
    if run.parent_run_id:
        fields["parent_run_id"] = run.parent_run_id
    _publish(run, "started", fields, "started")


def publish_terminal(run: ChatRun) -> None:
    started, finished = _aware(run.started_at), _aware(run.finished_at)
    duration_ms = int((finished - started).total_seconds() * 1000) if started and finished else 0
    result = dict(run.result or {})
    if run.state == "error":
        fields: Dict[str, Any] = {"message": run.error or "The run failed", "code": run.error_code or "run_failed"}
        for key in ("hint", "requires_user_action"):
            if result.get(key) is not None:
                fields[key] = result[key]
        _publish(run, "failed", fields, "terminal")
        return
    outcome: Dict[str, Any] = {"type": run.outcome or "success"}
    interrupts = result.pop("interrupts", None)
    if run.outcome == "interrupt":
        outcome["interrupts"] = list(interrupts or [])
    _publish(
        run,
        "finished",
        {"outcome": outcome, "result": result, "duration_ms": duration_ms, "step_count": len(run.steps or [])},
        "terminal",
    )


async def start_run(
    database: Any,
    *,
    run_id: str,
    temporal_workflow_id: str,
    temporal_run_id: Optional[str],
) -> Optional[ChatRun]:
    """Claim a run for the Temporal workflow that answers it. Returns the
    run when this workflow holds it (also on a retry), None when another
    workflow claimed it first or it can no longer start."""
    async with database.get_session() as session:
        await session.execute(
            update(ChatRun)
            .where(ChatRun.run_id == run_id, ChatRun.state.in_(("pending", "queued")))
            .values(
                state="running",
                started_at=_utcnow(),
                temporal_workflow_id=temporal_workflow_id,
                temporal_run_id=temporal_run_id,
            )
        )
        await session.commit()
    run = await get_run(database, run_id)
    if (
        run is None
        or run.state not in ("running", "stopping")
        or run.temporal_workflow_id != temporal_workflow_id
        or run.temporal_run_id != temporal_run_id
    ):
        return None
    publish_started(run)
    return run


async def _settle(database: Any, run: ChatRun, values: Dict[str, Any]) -> Optional[ChatRun]:
    """Move ``run`` from the state it was read in to a terminal one, then
    publish it. None when the run moved meanwhile."""
    async with database.get_session() as session:
        result = await session.execute(
            update(ChatRun).where(ChatRun.run_id == run.run_id, ChatRun.state == run.state).values(finished_at=_utcnow(), **values)
        )
        await session.commit()
        if not result.rowcount:
            return None
    settled = await get_run(database, run.run_id)
    if settled is not None and settled.state in TERMINAL_STATES:
        publish_terminal(settled)
    return settled


async def _success_values(database: Any, run: ChatRun, outcome: str) -> Dict[str, Any]:
    if await _message_exists(database, run.reply_message_uid):
        result: Dict[str, Any] = {"reply_message_id": run.reply_message_uid}
    else:
        result = {"no_reply": True}
    return {"state": "stopped" if outcome == "stopped" else "finished", "outcome": outcome, "result": result}


async def finish_run(
    database: Any,
    *,
    run_id: str,
    temporal_workflow_id: str,
    temporal_run_id: Optional[str],
    success: bool,
    error: Optional[str] = None,
    code: Optional[str] = None,
    hint: Optional[str] = None,
    requires_user_action: Optional[bool] = None,
) -> Optional[ChatRun]:
    """End a run its claimant answered. A run stopped meanwhile ends
    ``stopped``. Returns the ended run, or None when the caller does not
    hold it."""
    run = await get_run(database, run_id)
    if run is None or run.temporal_workflow_id != temporal_workflow_id or run.temporal_run_id != temporal_run_id:
        return None
    if run.state in TERMINAL_STATES:
        publish_terminal(run)
        return run
    if run.state not in ("running", "stopping"):
        return None
    if run.state == "stopping":
        values = await _success_values(database, run, "stopped")
    elif success:
        values = await _success_values(database, run, "success")
    else:
        details = {key: value for key, value in (("hint", hint), ("requires_user_action", requires_user_action)) if value is not None}
        values = {
            "state": "error",
            "error": (error or "The run failed")[:500],
            "error_code": (code or "run_failed")[:60],
            "result": details,
        }
    return await _settle(database, run, values)


async def fail_run(database: Any, run: ChatRun, *, code: str, message: str) -> Optional[ChatRun]:
    """End a live run with an error. None when it moved meanwhile."""
    if run.state not in LIVE_STATES:
        return None
    return await _settle(database, run, {"state": "error", "error": message[:500], "error_code": code[:60], "result": {}})


def publish_cleared(runs: List[ChatRun], *, code: str) -> None:
    """Tell subscribers that the runs ended with their conversation (a Reset
    or the owner's Clear deleted them)."""
    message = "The conversation was reset." if code == "reset" else "The conversation was cleared."
    for run in runs:
        _publish(run, "failed", {"message": message, "code": code}, "terminal")


# ---- the watchdog ------------------------------------------------------


async def sweep(
    database: Any,
    *,
    now: Optional[datetime] = None,
    process_started: datetime = PROCESS_STARTED,
    temporal_status: Optional[TemporalStatus] = None,
) -> List[str]:
    """End the live runs nothing will end. Returns their ids."""
    now = now or _utcnow()
    pickup = timedelta(seconds=runs_setting("pickup_timeout_s"))
    longest = timedelta(seconds=runs_setting("max_running_s"))
    ended: List[str] = []
    for run in await live_runs(database):
        try:
            closed = await _sweep_one(database, run, now, process_started, pickup, longest, temporal_status)
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            logger.warning("Chat run sweep failed for a run", run_id=run.run_id, exc_info=True)
            continue
        if closed is not None:
            ended.append(closed.run_id)
    if ended:
        logger.info("Ended chat runs nothing would finish", count=len(ended))
    return ended


async def _sweep_one(
    database: Any,
    run: ChatRun,
    now: datetime,
    process_started: datetime,
    pickup: timedelta,
    longest: timedelta,
    temporal_status: Optional[TemporalStatus],
) -> Optional[ChatRun]:
    created = max(_aware(run.created_at) or now, process_started)
    if run.state == "pending":
        if now - created > pickup:
            return await fail_run(database, run, code="not_delivered", message="The employee did not pick up this message.")
        return None
    if run.state == "queued":
        control = await database.get_latest_workflow_control(run.workflow_id) if run.workflow_id else None
        delivery = delivery_for(control)
        if delivery == "queued":
            return None
        if delivery == "now":
            resumed = max(created, _aware(getattr(control, "updated_at", None)) or created)
            if now - resumed > pickup:
                return await fail_run(database, run, code="not_delivered", message="The employee did not pick up this message.")
            return None
        return await fail_run(database, run, code="not_delivered", message="The employee stopped before reading this message.")
    started = _aware(run.started_at) or created
    if now - started > longest:
        return await fail_run(database, run, code="timed_out", message="The employee took too long to answer.")
    if temporal_status is None or not run.temporal_workflow_id or now - started < _CLOSED_GRACE:
        return None
    if await temporal_status(run.temporal_workflow_id, run.temporal_run_id) is not True:
        return None
    current = await get_run(database, run.run_id)
    if current is None or current.state not in ("running", "stopping"):
        return None
    if await _message_exists(database, current.reply_message_uid):
        return await _settle(database, current, await _success_values(database, current, "stopped" if current.state == "stopping" else "success"))
    return await fail_run(database, current, code="interrupted", message="The run ended before it answered.")


__all__ = [
    "Admission",
    "PROCESS_STARTED",
    "RunInProgress",
    "admit_message",
    "client_message_uid",
    "fail_run",
    "finish_run",
    "get_run",
    "lane_run",
    "live_runs",
    "new_run_id",
    "post_reply",
    "publish_cleared",
    "publish_started",
    "publish_terminal",
    "reply_uid",
    "runs_by_id",
    "start_run",
    "sweep",
]

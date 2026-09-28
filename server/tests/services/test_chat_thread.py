"""A workflow's chat thread (services/chat_thread.py) and the chat
WebSocket handlers built on it: rows carry the live generation, every
insert and clear is announced on ``chat.updated``, a message reaches a
workflow only while a deployment would read it, and Home's history spans
restarts with the generation on every row."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import nodes.trigger.chat_trigger._events as chat_events
import services.status_broadcaster as status_broadcaster
from models.database import WorkflowControlExecution
from routers import websocket as ws_router
from services import chat_thread


@pytest.fixture
async def database(tmp_path: Path):
    """The real Database on a throwaway SQLite file (the root conftest stubs
    ``core.database``, so the module is loaded privately)."""
    module_name = f"tests._chat_thread_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "core" / "database.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"chat-thread-{uuid.uuid4().hex}.db"
    db = module.Database(
        SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}", database_echo=False, database_pool_size=5, database_max_overflow=5)
    )
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()
        sys.modules.pop(module_name, None)


@pytest.fixture
def frames(monkeypatch):
    sent: list = []

    class Broadcaster:
        async def broadcast(self, message):
            sent.append(message)

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    return sent


@pytest.fixture
def router(monkeypatch, database, frames):
    """The router's chat handlers on the test database, with the chat
    trigger dispatch captured."""
    dispatched: list = []

    async def dispatch(event_data, *, workflow_id=None):
        dispatched.append((dict(event_data), workflow_id))

    monkeypatch.setattr(ws_router, "container", SimpleNamespace(database=lambda: database))
    monkeypatch.setattr(chat_events, "dispatch_chat_message_received", dispatch)
    return SimpleNamespace(database=database, frames=frames, dispatched=dispatched)


async def control(database, workflow_id, status, generation=1):
    async with database.get_session() as session:
        session.add(
            WorkflowControlExecution(
                id=f"{workflow_id}-{generation}",
                workflow_id=workflow_id,
                generation=generation,
                execution_id=f"gen-{generation}",
                root_execution_id=f"gen-{generation}",
                graph_hash="0" * 64,
                status=status,
                idempotency_key=f"k-{generation}",
            )
        )
        await session.commit()


def updates(frames):
    return [frame["data"]["data"] for frame in frames if frame["type"] == "chat.updated"]


# ----- the service -----


@pytest.mark.parametrize(
    ("status", "delivery"),
    [
        ("running", "now"),
        ("starting", "now"),
        ("resuming", "now"),
        ("paused", "queued"),
        ("pausing", "queued"),
        ("reset", None),
        ("resetting", None),
        ("failed", None),
    ],
)
def test_delivery_follows_the_control_state(status, delivery):
    assert chat_thread.delivery_for(SimpleNamespace(status=status)) == delivery
    assert chat_thread.delivery_for(None) is None


def test_rows_belong_to_the_live_generation():
    assert chat_thread.chat_execution_id(SimpleNamespace(status="paused", root_execution_id="gen-3")) == "gen-3"
    assert chat_thread.chat_execution_id(SimpleNamespace(status="reset", root_execution_id="gen-3")) is None
    assert chat_thread.chat_execution_id(None) is None


async def test_a_recorded_message_is_stamped_and_announced(database, frames):
    await control(database, "7", "running", generation=2)
    assert await chat_thread.record_chat_message(database, "7", "assistant", "Done.") is True
    [row] = await database.get_chat_messages("7")
    assert (row["role"], row["message"], row["execution_id"]) == ("assistant", "Done.", "gen-2")
    [frame] = frames
    assert frame["type"] == "chat.updated"
    assert frame["data"]["type"] == "com.opencompany.chat.updated"
    assert frame["data"]["subject"] == "7"
    # Identity only: the text stays behind get_chat_messages.
    assert frame["data"]["data"] == {"workflow_id": "7", "session_id": "7", "role": "assistant"}


async def test_the_editors_default_chat_is_no_workflows_thread(database, frames):
    await chat_thread.record_chat_message(database, "default", "user", "hi")
    [row] = await database.get_chat_messages("default")
    assert row["execution_id"] is None
    assert updates(frames) == [{"workflow_id": None, "session_id": "default", "role": "user"}]


async def test_clearing_removes_every_generation_and_is_announced(database, frames):
    await database.add_chat_message("7", "user", "old", execution_id="gen-1")
    await database.add_chat_message("7", "user", "new", execution_id="gen-2")
    assert await chat_thread.clear_chat_thread(database, "7") == 2
    assert await database.get_chat_messages("7") == []
    assert updates(frames) == [{"workflow_id": "7", "session_id": "7", "role": None}]


async def test_history_is_the_newest_rows_oldest_first_in_utc(database):
    for text in ("one", "two", "three"):
        await database.add_chat_message("7", "user", text, execution_id="gen-1")
    rows = await database.get_chat_messages("7", limit=2)
    assert [row["message"] for row in rows] == ["two", "three"]
    assert rows[0]["id"] < rows[1]["id"]
    assert datetime.fromisoformat(rows[0]["timestamp"]).utcoffset().total_seconds() == 0


# ----- the WebSocket handlers -----


async def send(message, session_id):
    return await ws_router.handle_send_chat_message({"message": message, "session_id": session_id, "timestamp": "t"}, None)


async def test_a_workflow_that_is_not_running_takes_no_message(router):
    assert await send("hello?", "7") == {"success": False, "error": "not_running"}
    await control(router.database, "7", "failed")
    assert (await send("hello?", "7"))["error"] == "not_running"
    assert await router.database.get_chat_messages("7") == []
    assert router.dispatched == [] and router.frames == []


@pytest.mark.parametrize(("status", "delivery"), [("running", "now"), ("paused", "queued")])
async def test_a_message_goes_now_or_waits_for_resume(router, status, delivery):
    await control(router.database, "7", status, generation=3)
    result = await send("Book Tuesday", "7")
    assert result["success"] is True and result["delivery"] == delivery and result["timestamp"] == "t"
    [row] = await router.database.get_chat_messages("7")
    assert (row["role"], row["message"], row["execution_id"]) == ("user", "Book Tuesday", "gen-3")
    # Only this workflow's triggers hear it.
    assert router.dispatched == [({"message": "Book Tuesday", "timestamp": "t", "session_id": "7"}, "7")]
    assert updates(router.frames) == [{"workflow_id": "7", "session_id": "7", "role": "user"}]


async def test_the_default_session_keeps_its_old_behaviour(router):
    result = await ws_router.handle_send_chat_message({"message": "hi"}, None)
    assert result["success"] is True and "delivery" not in result
    assert datetime.fromisoformat(result["timestamp"]).utcoffset() is not None
    assert [row["message"] for row in await router.database.get_chat_messages("default")] == ["hi"]
    assert router.dispatched[0][1] is None


async def test_history_is_one_generation_unless_all_are_asked_for(router):
    await control(router.database, "7", "reset", generation=1)
    await router.database.add_chat_message("7", "user", "before the restart", execution_id="gen-1")
    await router.database.add_chat_message("7", "assistant", "an answer", execution_id="gen-1")
    await control(router.database, "7", "running", generation=2)
    await router.database.add_chat_message("7", "user", "after it", execution_id="gen-2")

    live = await ws_router.handle_get_chat_messages({"session_id": "7"}, None)
    assert [message["message"] for message in live["messages"]] == ["after it"]

    everything = await ws_router.handle_get_chat_messages({"session_id": "7", "all_generations": True, "limit": 200}, None)
    assert [(m["role"], m["message"], m["run_key"]) for m in everything["messages"]] == [
        ("user", "before the restart", "gen-1"),
        ("assistant", "an answer", "gen-1"),
        ("user", "after it", "gen-2"),
    ]
    assert all(isinstance(m["id"], int) and datetime.fromisoformat(m["timestamp"]).utcoffset() is not None for m in everything["messages"])
    newest = await ws_router.handle_get_chat_messages({"session_id": "7", "all_generations": True, "limit": 2}, None)
    assert [m["message"] for m in newest["messages"]] == ["an answer", "after it"]


async def test_after_a_reset_only_all_generations_shows_the_thread(router):
    await router.database.add_chat_message("7", "user", "kept", execution_id="gen-1")
    await control(router.database, "7", "reset", generation=1)
    assert (await ws_router.handle_get_chat_messages({"session_id": "7"}, None))["messages"] == []
    assert len((await ws_router.handle_get_chat_messages({"session_id": "7", "all_generations": True}, None))["messages"]) == 1


async def test_save_and_clear_go_through_the_thread(router):
    await control(router.database, "7", "running", generation=4)
    saved = await ws_router.handle_save_chat_message({"message": "An answer", "role": "assistant", "session_id": "7"}, None)
    assert saved["success"] is True
    [row] = await router.database.get_chat_messages("7")
    assert row["execution_id"] == "gen-4"
    cleared = await ws_router.handle_clear_chat_messages({"session_id": "7"}, None)
    assert cleared["cleared_count"] == 1
    assert [update["role"] for update in updates(router.frames)] == ["assistant", None]

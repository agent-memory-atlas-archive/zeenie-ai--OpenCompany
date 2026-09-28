"""Reply in Chat (``chatReply``): posts an answer to the thread of the
workflow it runs in, as the assistant, in the live generation, and says so
on ``chat.updated``; posts nothing for an empty answer or NO_REPLY."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.node_contract


@pytest.fixture
def thread(harness):
    """The live generation and the frames broadcast."""
    frames: list = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

    harness.database.get_latest_workflow_control = AsyncMock(return_value=SimpleNamespace(status="running", root_execution_id="gen-2"))
    harness.database.add_chat_message = AsyncMock(return_value=True)
    with patch("services.status_broadcaster.get_status_broadcaster", return_value=Broadcaster()):
        yield SimpleNamespace(database=harness.database, frames=frames)


async def _reply(harness, message, *, workflow_id="wf-1"):
    return await harness.execute("chatReply", {"message": message}, context=harness.build_context(workflow_id=workflow_id))


async def test_the_answer_lands_in_the_workflows_thread(harness, thread):
    result = await _reply(harness, "  Booked you for 3pm.  ")
    harness.assert_envelope(result, success=True)
    assert result["result"] == {"posted": True, "message": "Booked you for 3pm."}
    thread.database.add_chat_message.assert_awaited_once_with("wf-1", "assistant", "Booked you for 3pm.", execution_id="gen-2")
    [frame] = thread.frames
    assert frame["type"] == "chat.updated"
    assert frame["data"]["type"] == "com.opencompany.chat.updated"
    assert frame["data"]["data"] == {"workflow_id": "wf-1", "session_id": "wf-1", "role": "assistant"}


@pytest.mark.parametrize("message", ["", "   ", "NO_REPLY", " NO_REPLY\n", None])
async def test_nothing_to_say_posts_nothing(harness, thread, message):
    result = await _reply(harness, message)
    harness.assert_envelope(result, success=True)
    assert result["result"]["posted"] is False
    thread.database.add_chat_message.assert_not_awaited()
    assert thread.frames == []


async def test_an_answer_that_is_not_text_is_posted_as_text(harness, thread):
    # A whole-value template keeps the upstream value's type.
    result = await _reply(harness, {"items": ["milk", "eggs"]})
    harness.assert_envelope(result, success=True)
    assert thread.database.add_chat_message.await_args.args[2] == '{"items": ["milk", "eggs"]}'


async def test_it_needs_a_saved_workflow(harness, thread):
    result = await _reply(harness, "hello", workflow_id=None)
    harness.assert_envelope(result, success=False)
    assert "save the workflow" in result["error"]
    thread.database.add_chat_message.assert_not_awaited()


async def test_a_failed_save_is_an_error_and_announces_nothing(harness, thread):
    thread.database.add_chat_message = AsyncMock(return_value=False)
    result = await _reply(harness, "hello")
    harness.assert_envelope(result, success=False)
    assert thread.frames == []


def test_it_is_a_sink_the_hire_builder_may_use():
    import nodes  # noqa: F401
    from services.node_allowlist import CONFIG_PATH, NodeAllowlistService
    from services.node_spec import get_node_spec

    spec = get_node_spec("chatReply")
    assert spec["displayName"] == "Reply in Chat"
    assert [handle["name"] for handle in spec["handles"]] == ["input-main"]
    assert spec.get("hideOutputHandle") is True
    assert isinstance(spec["uiHints"]["executionTimeoutMs"], int)
    assert "isConsoleSink" not in spec["uiHints"]
    service = NodeAllowlistService(config_path=CONFIG_PATH)
    assert service.is_hire_allowed("chatReply") and service.is_hire_allowed("agentBuilder")

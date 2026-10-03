"""Notes to the employee (services/chat/notes.py) and the guide and
follow-ups of a chat reply (services/chat/guide.py): a note is told once,
on the turn after it was written, again when a turn that claimed it failed
or when it changed meanwhile; the newest note of a kind replaces an untold
one; what the owner sets in an interface becomes a [ui-state] note; the
reply's <followups> block becomes buttons and never shows."""

from __future__ import annotations

import json

import pytest

from services.chat import guide, ledger, notes, parts
from tests.services.chat._helpers import talking

CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")


async def test_a_note_is_told_once_on_the_next_turn(database):
    await notes.upsert_note(database, session_id="wf", key="ui-state:ui_1", kind="ui-state", text="[ui-state]{}[/ui-state]")
    claimed = await notes.claim_notes(database, session_id="wf", run_id="r_1")
    assert [note.text for note in claimed] == ["[ui-state]{}[/ui-state]"]
    # A retried claim by the same run gets the same notes; another session none.
    assert [note.key for note in await notes.claim_notes(database, session_id="wf", run_id="r_1")] == ["ui-state:ui_1"]
    assert await notes.claim_notes(database, session_id="other", run_id="r_x") == []
    assert await notes.deliver_notes(database, "r_1") == 1
    assert await notes.claim_notes(database, session_id="wf", run_id="r_2") == []


async def test_a_failed_turn_leaves_its_notes_for_the_next(database):
    await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text="first")
    await notes.claim_notes(database, session_id="wf", run_id="r_failed")
    # Nothing delivered for the failed run; the next one gets the note.
    [note] = await notes.claim_notes(database, session_id="wf", run_id="r_next")
    assert note.claimed_run_id == "r_next"


async def test_a_newer_note_replaces_an_untold_one_and_is_told_again(database):
    await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text="slot s1")
    await notes.claim_notes(database, session_id="wf", run_id="r_1")
    # Changed after the claim: told again, even though r_1 finished.
    await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text="slot s2")
    assert await notes.deliver_notes(database, "r_1") == 0
    [note] = await notes.claim_notes(database, session_id="wf", run_id="r_2")
    assert note.text == "slot s2"
    await notes.deliver_notes(database, "r_2")
    # Changed after it was told: told again.
    await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text="slot s3")
    assert [n.text for n in await notes.claim_notes(database, session_id="wf", run_id="r_3")] == ["slot s3"]
    await notes.clear_notes(database, "wf")
    assert await notes.claim_notes(database, session_id="wf", run_id="r_4") == []


async def test_a_finished_run_tells_its_notes_and_a_failed_one_does_not(database, hub):
    for success in (False, True):
        admission = await ledger.admit_message(database, session_id="wf", workflow_id="wf", execution_id="g", text="hi", track=True)
        run = await ledger.start_run(database, run_id=admission.run.run_id, **CLAIM)
        await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text=f"try {success}")
        await notes.claim_notes(database, session_id="wf", run_id=run.run_id)
        await ledger.finish_run(database, run_id=run.run_id, success=success, error=None if success else "boom", **CLAIM)
    assert await notes.claim_notes(database, session_id="wf", run_id="r_after") == []


async def test_a_run_stopped_before_writing_leaves_its_notes(database, hub):
    admission = await ledger.admit_message(database, session_id="wf", workflow_id="wf", execution_id="g", text="hi", track=True)
    run = await ledger.start_run(database, run_id=admission.run.run_id, **CLAIM)
    await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text="slot s1")
    await notes.claim_notes(database, session_id="wf", run_id=run.run_id)
    await ledger.request_stop(database, run.run_id)
    ended = await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert ended.state == "stopped" and ended.result == {"no_reply": True}
    assert [note.key for note in await notes.claim_notes(database, session_id="wf", run_id="r_next")] == ["k"]


async def test_what_the_owner_sets_becomes_a_ui_state_note(chat):
    from services.genui.spec import check_spec

    await talking(chat.database)
    sent = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, None)
    run = await ledger.start_run(chat.database, run_id=sent["run_id"], **CLAIM)
    spec = check_spec({"root": "r", "state": {"slot": "s1"}, "elements": {"r": {"type": "Toggle", "props": {"label": "Remind", "checked": {"$bindState": "/remind"}}}}}).spec
    stream = {"run_id": run.run_id, "session_id": "wf", "workflow_id": "wf"}
    part_id = await parts.show_ui(chat.database, stream, tool_call_id="call_1", spec=spec)
    await chat.handlers.handle_chat_ui_state({"session_id": "wf", "part_id": part_id, "changes": [{"path": "/remind", "value": True}]}, None)
    [note] = await notes.claim_notes(chat.database, session_id="wf", run_id="r_next")
    assert note.kind == "ui-state" and note.text.startswith("[ui-state]") and note.text.endswith("[/ui-state]")
    assert json.loads(note.text[len("[ui-state]"):-len("[/ui-state]")]) == {"ui_id": part_id, "state": {"slot": "s1", "remind": True}}
    # Clearing the chat forgets them.
    await chat.handlers.handle_clear_chat_messages({"session_id": "wf"}, None)
    assert await notes.claim_notes(chat.database, session_id="wf", run_id="r_later") == []


# ----- the guide and follow-ups -----


def test_the_guide_is_added_once_and_never_changes():
    once = guide.with_chat_guide("You are Maya.")
    assert once.startswith("You are Maya.\n\n") and once.endswith(guide.CHAT_REPLY_GUIDE)
    assert guide.with_chat_guide(once) == once
    assert guide.with_chat_guide("") == guide.CHAT_REPLY_GUIDE
    assert "[ui-event]" in guide.CHAT_REPLY_GUIDE and "<followups>" in guide.CHAT_REPLY_GUIDE


async def test_the_answering_agent_reads_the_guide_and_the_untold_notes(database):
    await notes.upsert_note(database, session_id="wf", key="k", kind="ui-state", text="[ui-state]{}[/ui-state]")
    stream = {"run_id": "r_1", "session_id": "wf"}
    system, prompt = await guide.chat_turn(database, stream, system_message="You are Maya.", prompt="Book it")
    assert system == guide.with_chat_guide("You are Maya.")
    assert prompt == "[ui-state]{}[/ui-state]\n\nBook it"
    # Nothing untold: the owner's message as it is.
    other = {"run_id": "r_2", "session_id": "other"}
    assert await guide.chat_turn(database, other, system_message="", prompt="Hi") == (guide.CHAT_REPLY_GUIDE, "Hi")

    class Broken:
        def reserved_session(self):
            raise RuntimeError("database down")

    # Notes that cannot be read wait for the next turn; this one goes on.
    assert await guide.chat_turn(Broken(), stream, system_message="S", prompt="Hi") == (guide.with_chat_guide("S"), "Hi")


def test_only_the_agent_answering_the_chat_reads_the_guide():
    import inspect

    from services.temporal import agent_activities

    source = inspect.getsource(agent_activities.prepare_agent_payload)
    assert "chat_stream = chat_stream_for(context)" in source
    assert "if chat_stream:" in source and "await chat_turn(" in source


@pytest.mark.parametrize(
    ("text", "visible", "items"),
    [
        ('Booked.\n<followups>["Move it?", "Cancel it?"]</followups>', "Booked.", ["Move it?", "Cancel it?"]),
        ("Booked.\n<followups>\n- Move it?\n- Cancel it?\n</followups>", "Booked.", ["Move it?", "Cancel it?"]),
        ('Done.<followups>["a", "a", "b", "c", "d", " "]', "Done.", ["a", "b", "c"]),
        ("No block here.", "No block here.", []),
        ('<followups>["Only this?"]</followups>', "", ["Only this?"]),
        ('Text <followups>["x"]</followups> after', "Text  after", ["x"]),
    ],
)
def test_followups_are_taken_off_the_reply(text, visible, items):
    assert guide.split_followups(text) == (visible, items)


async def test_reply_in_chat_saves_followups_as_buttons(database, hub):
    admission = await ledger.admit_message(database, session_id="wf", workflow_id="wf", execution_id="g", text="hi", track=True)
    run = await ledger.start_run(database, run_id=admission.run.run_id, **CLAIM)
    reply = await ledger.post_reply(
        database, run=run, node_id="n", text='Two today.\n<followups>["And tomorrow?"]</followups>', execution_id="g"
    )
    assert reply["message"] == "Two today."
    assert reply["parts"]["followups"] == ["And tomorrow?"]


async def test_reply_in_chat_never_shows_the_block(monkeypatch, database, hub):
    from nodes.chat.chat_reply import ChatReplyNode, ChatReplyParams

    saved = []

    async def record_chat_message(database, session_id, role, text):
        saved.append(text)
        return {"uid": "m_1"}

    monkeypatch.setattr("services.chat_thread.record_chat_message", record_chat_message)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    node = ChatReplyNode()
    ctx = type("Ctx", (), {"workflow_id": "wf", "node_id": "wf:chatReply:1", "raw": {}})()
    out = await node.reply(ctx, ChatReplyParams(message='Done.\n<followups>["More?"]</followups>'))
    assert (out.posted, out.message, saved) == (True, "Done.", ["Done."])
    # A message that is only the block says nothing.
    out = await node.reply(ctx, ChatReplyParams(message='<followups>["More?"]</followups>'))
    assert out.posted is False and saved == ["Done."]

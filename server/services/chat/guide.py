"""What the agent answering the owner's chat reads besides its own system
prompt, and reading its follow-up suggestions back out of its answer
(docs-internal/chat_protocol.md, "Notes to the employee" and "Follow-ups").

``CHAT_REPLY_GUIDE`` is fixed text, the same byte for byte on every turn, so
the agent's system prompt stays stable for prompt caching. It is appended by
``agent.prepare_payload`` only for the agent whose answer is the chat's reply
(the one with a ``chat_stream``).

``split_followups`` takes the ``<followups>`` block off a reply: the chat
shows its questions as buttons (``parts.followups``) and the block itself
never shows (the stream holds it back too, ``services/chat/stream.py``).
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Mapping, Tuple

from core.logging import get_logger
from services.chat.stream import FOLLOWUPS_OPEN

logger = get_logger(__name__)

FOLLOWUPS_CLOSE = "</followups>"
#: The most suggestions a reply keeps, and how long each may be.
MAX_FOLLOWUPS = 3
MAX_FOLLOWUP_CHARS = 160

CHAT_REPLY_GUIDE = (
    "You are answering the owner in a chat.\n"
    "- A line in square brackets at the start of the owner's message comes from OpenCompany, not from the owner: "
    "[ui-event]{...}[/ui-event] is a button they pressed in an interface you showed (with what they picked), and "
    "[ui-state]{...}[/ui-state] is what they set in one without pressing anything. Treat it as the owner's choice.\n"
    "- A search result numbered n is a source: cite the ones you rely on as [n] right after what they support.\n"
    "- When a few short next questions would help the owner, end your reply with "
    '<followups>["...", "..."]</followups>: at most 3, each a short question in the owner\'s own words. The chat '
    "shows them as buttons; the block itself is never shown."
)

_LIST_MARK = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def with_chat_guide(system_message: str) -> str:
    """The system prompt with the guide after it (once)."""
    text = system_message or ""
    if CHAT_REPLY_GUIDE in text:
        return text
    return f"{text.rstrip()}\n\n{CHAT_REPLY_GUIDE}" if text.strip() else CHAT_REPLY_GUIDE


async def chat_turn(database: Any, chat_stream: Mapping[str, Any], *, system_message: str, prompt: str) -> Tuple[str, str]:
    """The answering agent's turn: its system prompt with the guide, and the
    owner's message with the session's untold notes ahead of it (claimed for
    this run). Notes that cannot be read are left for the next turn."""
    from services.chat.notes import claim_notes, notes_prompt

    system = with_chat_guide(system_message)
    try:
        claimed = await claim_notes(database, session_id=str(chat_stream["session_id"]), run_id=str(chat_stream["run_id"]))
    except Exception:  # noqa: BLE001 - the turn goes on without them
        logger.warning("Chat notes could not be read for a turn", run_id=chat_stream.get("run_id"), exc_info=True)
        claimed = []
    if claimed:
        lines = notes_prompt(claimed)
        prompt = f"{lines}\n\n{prompt}" if prompt else lines
    return system, prompt


def _items(body: str) -> List[str]:
    body = body.strip()
    raw: List[str]
    try:
        parsed = json.loads(body)
        raw = [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []
    except ValueError:
        raw = [_LIST_MARK.sub("", line).strip().strip('"') for line in body.splitlines()]
    kept: List[str] = []
    for item in raw:
        text = " ".join(item.split())[:MAX_FOLLOWUP_CHARS]
        if text and text not in kept:
            kept.append(text)
    return kept[:MAX_FOLLOWUPS]


def split_followups(text: str) -> Tuple[str, List[str]]:
    """``(the reply without its last <followups> block, the suggestions in
    it)``. A block left open runs to the end of the reply."""
    start = text.rfind(FOLLOWUPS_OPEN)
    if start == -1:
        return text, []
    body = text[start + len(FOLLOWUPS_OPEN):]
    end = body.find(FOLLOWUPS_CLOSE)
    inner, after = (body[:end], body[end + len(FOLLOWUPS_CLOSE):]) if end != -1 else (body, "")
    visible = (text[:start] + after).rstrip()
    return visible, _items(inner)


__all__ = ["CHAT_REPLY_GUIDE", "FOLLOWUPS_CLOSE", "MAX_FOLLOWUPS", "chat_turn", "split_followups", "with_chat_guide"]

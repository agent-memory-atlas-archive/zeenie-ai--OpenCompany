"""Sources an answer can cite (docs-internal/chat_protocol.md, "Sources").

A tool whose plugin declares ``chat_sources`` (the web searches) returns
results with addresses. When the agent answering a chat run calls it,
``number_tool_sources`` numbers those results for the conversation (the
session's counter on ``chat_threads.next_source``, so a number never repeats
in a conversation and an older ``[n]`` keeps meaning what it meant), writes
the number on each result the model reads (``n``), and saves them as a
``sources`` part of the run. The agent cites ``[n]``; the reply lists the
sources it cited, numbered 1, 2, ... in the order it cites them (the
client's job).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from core.logging import get_logger
from models.chat import ChatThread

logger = get_logger(__name__)

#: The most results one call numbers.
MAX_SOURCES_PER_CALL = 10
_MAX_TITLE = 160
_MAX_DETAIL = 240
_MAX_URL = 2048


def _clean(item: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    url = item.get("url")
    if not isinstance(url, str) or len(url) > _MAX_URL:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    title = item.get("title")
    title = " ".join(title.split())[:_MAX_TITLE] if isinstance(title, str) and title.strip() else parts.netloc
    detail = item.get("snippet") if isinstance(item.get("snippet"), str) else item.get("detail")
    source: Dict[str, Any] = {"title": title, "url": url.strip()}
    if isinstance(detail, str) and detail.strip():
        source["detail"] = " ".join(detail.split())[:_MAX_DETAIL]
    return source


async def next_numbers(database: Any, session_id: str, count: int) -> int:
    """Take ``count`` numbers from the session's counter; returns the first."""
    async with database.reserved_session() as session:
        thread = await session.get(ChatThread, session_id)
        if thread is None:
            thread = ChatThread(session_id=session_id)
        first = thread.next_source or 1
        thread.next_source = first + count
        session.add(thread)
        await session.commit()
    return first


async def number_tool_sources(database: Any, stream: Mapping[str, Any], *, tool_call_id: str, payload: Any) -> List[Dict[str, Any]]:
    """Number the results with addresses in a tool's result (``payload``,
    the dict holding ``results``), writing ``n`` on each, and save them on
    the run. Returns the sources saved; nothing changes without results."""
    from services.chat.parts import record_part

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return []
    picked = []
    for item in payload["results"]:
        if len(picked) >= MAX_SOURCES_PER_CALL:
            break
        if isinstance(item, dict):
            source = _clean(item)
            if source is not None:
                picked.append((item, source))
    if not picked:
        return []
    first = await next_numbers(database, stream["session_id"], len(picked))
    saved = []
    for offset, (item, source) in enumerate(picked):
        n = first + offset
        item["n"] = n
        saved.append({"n": n, **source})
    await record_part(database, stream["run_id"], kind="sources", key=f"sources:{tool_call_id}", payload={"items": saved})
    return saved


__all__ = ["MAX_SOURCES_PER_CALL", "next_numbers", "number_tool_sources"]

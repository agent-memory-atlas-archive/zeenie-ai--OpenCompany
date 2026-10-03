"""Working steps: the tool calls of the agent answering a chat run, shown in
the chat while it works ("Checked Google Calendar", docs-internal/
chat_protocol.md).

``BaseNode.as_activity`` calls these around a tool call. A call is a step
only when it comes from the agent that answers the run: its payload carries
``chat_stream`` (``agent.prepare_payload`` -> AgentWorkflow), and a tool
call carries a ``tool_call_id``, which is the step id.

- **The label** is the plugin's ``chat_step``, else "Used <display name>";
  ``chat_step_hidden`` plugins (the clock, a checklist) show nothing.
- **Detail.** A tool may put a short line in its result as ``_step_detail``
  ("3 events on Saturday"); it is shown under the step and taken out of the
  result before the model reads it.
- **Saved** on the run as it finishes (``ledger.record_step``), so a reload
  can say what the employee did.
- **Stopped runs.** A tool call of a run the owner stopped is not run: it
  gets a result saying so, which keeps the conversation whole (every call
  answered) while the agent's next model step ends the run.
- **Sources.** The web results of a ``chat_sources`` tool (the searches) are
  numbered for the conversation in the result the model reads
  (``number_sources``, ``services/chat/sources.py``).

Nothing here fails a tool call: publishing and saving are best effort.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from core.logging import get_logger

logger = get_logger(__name__)

#: The key a tool result may carry its step detail under.
STEP_DETAIL_KEY = "_step_detail"
#: What a tool call of a stopped run returns instead of running.
STOPPED_RESULT = {"stopped": True, "message": "Not run: the owner stopped this answer."}


def step_label(node_class: Any) -> Optional[str]:
    """How a call of this tool reads as a step; None when it shows none."""
    if getattr(node_class, "chat_step_hidden", False):
        return None
    label = getattr(node_class, "chat_step", "") or ""
    if label:
        return label
    name = getattr(node_class, "display_name", "") or getattr(node_class, "type", "") or "a tool"
    return f"Used {name}"


@dataclass
class Step:
    run_id: str
    session_id: str
    workflow_id: Optional[str]
    step_id: str
    name: str
    started: float


def _stream_of(context: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    stream = context.get("chat_stream")
    if not isinstance(stream, Mapping):
        return None
    if not isinstance(stream.get("run_id"), str) or not isinstance(stream.get("session_id"), str):
        return None
    return stream


def _publish(step: Step, suffix: str, fields: Dict[str, Any], event_key: str) -> None:
    try:
        from services.chat.hub import get_chat_hub

        get_chat_hub().publish(
            run_id=step.run_id,
            session_id=step.session_id,
            workflow_id=step.workflow_id,
            suffix=suffix,
            fields=fields,
            event_key=event_key,
        )
    except Exception:  # noqa: BLE001 - a step is a courtesy
        logger.warning("Chat step event could not be published", run_id=step.run_id, exc_info=True)


def begin(context: Mapping[str, Any], node_class: Any) -> Optional[Step]:
    """Announce a tool call as a step; None when it is not one."""
    stream = _stream_of(context)
    step_id = context.get("tool_call_id")
    if stream is None or not isinstance(step_id, str) or not step_id:
        return None
    name = step_label(node_class)
    if name is None:
        return None
    workflow_id = stream.get("workflow_id")
    step = Step(
        run_id=stream["run_id"],
        session_id=stream["session_id"],
        workflow_id=workflow_id if isinstance(workflow_id, str) else None,
        step_id=step_id,
        name=name,
        started=time.monotonic(),
    )
    _publish(step, "step.started", {"step_id": step_id, "step_name": name}, f"step:{step_id}:start")
    return step


async def end(step: Optional[Step], *, state: str, detail: Optional[str] = None) -> None:
    """Close a step (``done``, ``failed`` or ``skipped``) and save it."""
    if step is None:
        return
    fields: Dict[str, Any] = {
        "step_id": step.step_id,
        "step_name": step.name,
        "state": state,
        "duration_ms": int((time.monotonic() - step.started) * 1000),
    }
    if detail:
        fields["detail"] = detail[:200]
    _publish(step, "step.finished", fields, f"step:{step.step_id}:end")
    try:
        from core.container import container
        from services.chat import ledger

        saved = {key: fields[key] for key in ("step_id", "state", "duration_ms", "detail") if key in fields}
        saved["name"] = step.name
        await ledger.record_step(container.database(), step.run_id, saved)
    except Exception:  # noqa: BLE001
        logger.warning("Chat step could not be saved", run_id=step.run_id, exc_info=True)


async def run_stopped(context: Mapping[str, Any]) -> bool:
    """Whether the call belongs to a chat run the owner stopped."""
    run_id = context.get("chat_run_id")
    if not isinstance(run_id, str) or not run_id or not context.get("tool_call_id"):
        return False
    try:
        from core.container import container
        from services.chat import ledger

        return await ledger.is_stopping(container.database(), run_id)
    except Exception:  # noqa: BLE001 - never skip a call over a failed read
        logger.warning("Could not read whether a chat run is stopping", exc_info=True)
        return False


async def number_sources(context: Mapping[str, Any], node_class: Any, result: Any) -> None:
    """Number the web results of a ``chat_sources`` tool the answering agent
    called, in the result the model reads (``services/chat/sources.py``)."""
    if not getattr(node_class, "chat_sources", False):
        return
    stream = _stream_of(context)
    tool_call_id = context.get("tool_call_id")
    if stream is None or not isinstance(tool_call_id, str) or not tool_call_id or not isinstance(result, dict):
        return
    payload = result.get("result") if isinstance(result.get("result"), dict) else result
    try:
        from core.container import container
        from services.chat.sources import number_tool_sources

        await number_tool_sources(container.database(), stream, tool_call_id=tool_call_id, payload=payload)
    except Exception:  # noqa: BLE001 - the answer goes on without numbers
        logger.warning("Chat sources could not be numbered", run_id=stream.get("run_id"), exc_info=True)


def take_detail(result: Any) -> Optional[str]:
    """Remove a tool's ``_step_detail`` from its result, wherever the
    plugin's result shape puts it, and return it."""
    if not isinstance(result, dict):
        return None
    detail = result.pop(STEP_DETAIL_KEY, None)
    inner = result.get("result")
    if isinstance(inner, dict):
        detail = inner.pop(STEP_DETAIL_KEY, None) or detail
    return detail if isinstance(detail, str) and detail.strip() else None


__all__ = [
    "STEP_DETAIL_KEY",
    "STOPPED_RESULT",
    "Step",
    "begin",
    "end",
    "number_sources",
    "run_stopped",
    "step_label",
    "take_detail",
]

"""Bounded, user-facing mobile-use progress; no SDK imports in the server.

The pinned SDK exposes plans through graph updates and action intent/targets through
tool callbacks. Never serialize graph state, prompts, screenshots, or LLM responses.
These summaries belong to the owner-only in-memory task status, not diagnostic logs.
"""

from __future__ import annotations

import re
import threading
import time


NODES = frozenset({"planner", "orchestrator", "contextor", "cortex", "executor", "executor_tools", "summarizer", "convergence"})
TOOLS = {
    "tap": "Tap", "long_press_on": "Long press", "focus_and_input_text": "Type",
    "focus_and_clear_text": "Clear text", "erase_one_char": "Erase one character",
    "launch_app": "Open app", "stop_app": "Close app", "back": "Go back",
    "press_key": "Press", "open_link": "Open link", "wait_for_delay": "Wait",
    "swipe": "Swipe", "swipe_coordinates": "Swipe", "swipe_percentages": "Swipe",
}


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def summary(value, limit=500):
    if not isinstance(value, str):
        return ""
    # URLs can contain login tokens. Render all summaries as plain text in the UI.
    value = re.sub(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>]+", "[link]", value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def plan_summary(value):
    if not isinstance(value, list):
        return None
    result = []
    for item in value[:40]:
        description = summary(field(item, "description"), 300)
        status = field(item, "status")
        status = str(getattr(status, "value", status)).lower()
        if description and status in {"not_started", "pending", "success", "failure"}:
            result.append({"id": summary(field(item, "id"), 100), "description": description,
                           "status": status, "reason": summary(field(item, "completion_reason") or field(item, "reason"), 300)})
    return result


def action_summary(name, args):
    """Select display fields, excluding typed payloads and injected state."""
    label = TOOLS.get(name)
    if not label or not isinstance(args, dict):
        return None
    target = args.get("target")
    target_label = summary(field(target, "text"), 100) or summary(field(target, "resource_id"), 120)
    if not target_label and target:
        bounds = field(target, "bounds")
        coords = [field(bounds, key) for key in ("x", "y", "width", "height")]
        if all(isinstance(v, (int, float)) for v in coords):
            target_label = f"area ({coords[0]}, {coords[1]}, {coords[2]} × {coords[3]})"
    if name in {"tap", "long_press_on", "focus_and_clear_text"}:
        label += f" · {target_label or 'screen target'}"
    elif name == "focus_and_input_text":
        text = args.get("text")
        if target_label == text:
            target_label = summary(field(target, "resource_id"), 120)
        label = f"Type {len(text) if isinstance(text, str) else 0} characters · {target_label or 'text field'}"
    elif name in {"launch_app", "stop_app"}:
        label += " · " + (summary(args.get("app_name") or args.get("package_name"), 140) or "current app")
    elif name == "press_key":
        key = args.get("key")
        key = getattr(key, "value", key)
        label += " · " + (key.title() if isinstance(key, str) and key.lower() in {"home", "back", "enter"} else "navigation key")
    elif name in {"swipe_coordinates", "swipe_percentages"}:
        suffix = "_percent" if name == "swipe_percentages" else ""
        coords = [args.get(key + suffix) for key in ("start_x", "start_y", "end_x", "end_y")]
        if all(isinstance(v, (int, float)) for v in coords):
            unit = "%" if suffix else "px"
            label += f" · ({coords[0]}, {coords[1]}) → ({coords[2]}, {coords[3]}) {unit}"
    elif name == "wait_for_delay":
        duration = args.get("time_in_ms")
        if isinstance(duration, (int, float)):
            label += f" · {min(max(0, duration), 60000) / 1000:g}s"
    # agent_thought is the SDK's explicit tool intent, not provider thinking blocks.
    intent = args.get("agent_thought")
    if name == "focus_and_input_text" and isinstance(args.get("text"), str) and args["text"]:
        if isinstance(intent, str):
            # Replacing a single typed letter would corrupt every word in the intent.
            intent = intent.replace(args["text"], "[typed text]") if len(args["text"]) > 2 else "Entering text in the selected field."
    return {"message": summary(label, 240), "detail": summary(intent)}


class ProgressCallbacks:
    """Mixin for the isolated engine's BaseCallbackHandler (callbacks may be threaded)."""

    def __init__(self, emit):
        self.emit = emit
        self._lock = threading.RLock()
        self._nodes = {}
        self._tools = {}
        self._step = -1
        self._plan = None

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, name=None, tags=None, **kwargs):
        role = (metadata or {}).get("langgraph_node")
        if role not in NODES or name != role or not any(tag.startswith("graph:step:") for tag in tags or []):
            return
        with self._lock:
            self._nodes[str(run_id)] = role
            step = (metadata or {}).get("langgraph_step")
            if isinstance(step, int) and step > self._step:
                self._step = max(0, step)
                self.emit("progress", steps=self._step)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        with self._lock:
            role = self._nodes.pop(str(run_id), None)
            if role not in {"planner", "orchestrator"}:
                return
            plan = plan_summary(field(outputs, "subgoal_plan"))
            if plan is None or plan == self._plan:
                return
            first = self._plan is None
            self._plan = plan
            current = next((item["description"] for item in plan if item["status"] == "pending"), None)
            completed = sum(item["status"] == "success" for item in plan)
            message = f"Working on: {current}" if current else (
                f"Plan created: {len(plan)} subtasks" if first else f"Plan updated: {completed}/{len(plan)} subtasks done"
            )
            self.emit("agent_update", kind="plan", message=message, plan=plan, current_goal=current)

    def on_chain_error(self, error, *, run_id, **kwargs):
        with self._lock:
            self._nodes.pop(str(run_id), None)

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        name = field(serialized, "name")
        action = action_summary(name, inputs)
        if not action:
            return
        with self._lock:
            key = str(run_id)
            self._tools[key] = (name, action, time.monotonic())
            self.emit("agent_update", kind="action", action_id=key, state="started", **action)

    def on_tool_end(self, output, *, run_id, **kwargs):
        with self._lock:
            saved = self._tools.pop(str(run_id), None)
            if not saved:
                return
            name, action, started = saved
            # SDK tools return Command(update={executor_messages: [ToolMessage]}).
            update = field(output, "update")
            messages = field(update, "executor_messages", []) if update is not None else [output]
            statuses = [field(item, "status") for item in messages if field(item, "status") in {"success", "error"}]
            state = "failed" if "error" in statuses else "completed" if statuses else "returned"
            outcome = "Tool returned without a confirmed outcome."
            if state == "failed":
                outcome = "The action failed. The agent will reassess the screen."
            elif state == "completed":
                outcome = "Action completed; task progress is checked separately by the agent."
            # Input tools echo field contents in their results: never forward those.
            if name not in {"focus_and_input_text", "focus_and_clear_text", "open_link"}:
                result = next((item for item in reversed(messages) if field(item, "status") == ("error" if state == "failed" else "success")), None)
                outcome = summary(field(result, "content")) or outcome
            self.emit("agent_update", kind="action", action_id=str(run_id), state=state,
                      duration_ms=round((time.monotonic() - started) * 1000), outcome=outcome, **action)

    def on_tool_error(self, error, *, run_id, **kwargs):
        with self._lock:
            saved = self._tools.pop(str(run_id), None)
            if saved:
                _, action, started = saved
                self.emit("agent_update", kind="action", action_id=str(run_id), state="failed",
                          duration_ms=round((time.monotonic() - started) * 1000),
                          outcome="The tool raised an error before confirming the action.", **action)

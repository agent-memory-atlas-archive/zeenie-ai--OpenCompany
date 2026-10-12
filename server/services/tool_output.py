"""Bound what one tool result may add to an agent's LLM conversation.

Both agent loops, the in-process ``run_native_agent_loop``
(``services/agent_runtime.py``) and the Temporal ``AgentWorkflow``, append a
tool's serialized result to the conversation, and every later model turn
re-sends it. An external tool can return far more text than a model needs: a
single TikHub call returned about 400,000 characters, and a handful of those
pushed the ``agent.execute_llm_step`` input past Temporal's payload warning
and the saved conversation past the 1 MB seed cap that ``agent.prepare_payload``
enforces (docs-internal/errors.md, entry 28).

:func:`bound_tool_output` keeps the first ``limit`` characters of the copy the
model sees and appends a note saying so. The tool's own result is never
touched; the cut happens where the loop turns that result into a message.

Stdlib and ``constants`` only, so workflow code can import it freely
(importing anything under ``services/llm`` runs that package's ``__init__``,
which reads ``llm_defaults.json``).

Agent-internal channels are exempt (:func:`tool_output_is_capped`): a
delegated agent's answer, a skill load, and Task Manager results. Each is
bounded by other means, and cutting one would hand the model a broken brief.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

from constants import AI_AGENT_TYPES

# Built-in agent tools whose results are never cut. Delegated agents are
# exempt through ``AI_AGENT_TYPES``.
_UNCAPPED_TOOL_TYPES = frozenset(
    {
        "_builtin_skill",
        "_builtin_check_delegated_tasks",
        "taskManager",
        "employeeJobSource",
    }
)

_NOTE_PREFIX = "\n\n[Tool result truncated: showing the first "
_NOTE = (
    _NOTE_PREFIX
    + "{shown:,} of {total:,} characters. Call the tool again with narrower "
    "parameters to see the rest.]"
)
_NOTE_PATTERN = re.compile(
    re.escape(_NOTE_PREFIX) + r"[\d,]+ of ([\d,]+) characters\.[^\]]*\]"
)


def tool_output_is_capped(node_type: Optional[str]) -> bool:
    """Whether a result from a tool of ``node_type`` is bounded.

    Unknown or missing types count as external tools, so the exemptions are
    exactly the listed agent-internal channels.
    """
    node_type = str(node_type or "")
    return node_type not in _UNCAPPED_TOOL_TYPES and node_type not in AI_AGENT_TYPES


def bound_tool_output(text: str, limit: Optional[int]) -> str:
    """Keep the first ``limit`` characters of ``text`` and say what was cut.

    A falsy or negative ``limit`` means no cap. Bounding a result that
    already carries this note keeps the original total in the new note, so a
    later, tighter cut still tells the model how much the tool returned.
    """
    if not limit or limit <= 0:
        return text
    body, total = _split_note(text)
    if len(body) <= limit:
        return text
    return body[:limit] + _NOTE.format(
        shown=limit,
        total=total if total is not None else len(body),
    )


def _split_note(text: str) -> Tuple[str, Optional[int]]:
    """Split a previously bounded result into its kept text and full length."""
    start = text.rfind(_NOTE_PREFIX)
    if start < 0:
        return text, None
    match = _NOTE_PATTERN.fullmatch(text, start)
    if match is None:
        return text, None
    return text[:start], int(match.group(1).replace(",", ""))


def resolve_tool_output_limit(
    user_settings: Optional[Mapping[str, Any]],
    settings: Any,
) -> int:
    """Per-user ``tool_result_max_chars``, else ``Settings.tool_result_max_chars``.

    Same guard as ``agent_recursion_limit``: only a positive integer in the
    user's row overrides the env value. ``0`` means no cap.
    """
    raw = (user_settings or {}).get("tool_result_max_chars")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    try:
        return max(0, int(getattr(settings, "tool_result_max_chars", 0) or 0))
    except (TypeError, ValueError):
        return 0

"""What a hired employee may be given: one rule for the Hire builder and for
anything added later (the Agent Builder, when the owner asks from Talk).

A tool is a node type from the app registry (config/employee_apps.json)
or one of the tools every hire gets (``BASE_TOOLS``). ``check_tool``
decides, in this order:

1. is it one of those at all;
2. the "ask me first" ground rule: a tool that can send or spend is given
   when each call can wait for the owner (its plugin declares an
   ``approval`` spec, services/plugin/approval.py: while the owner asks
   first a call that sends is held for them, Stripe is refused and the
   browser reads only, all decided per call from the live rule); otherwise
   nothing that can send or spend (``allowed_when_asking_first``), unless
   the app declares ``ask_first_params`` that make it safe, in which case
   it is given in that form;
3. the Hire allowlist (``node_allowlist.is_hire_allowed``);
4. its app is connected, when the caller says what is connected (the Hire
   builder does not: a hire may name an app it will connect later, and
   its card says what to connect).

``check_skill`` refuses the names no hired employee can have: ``skill``
(the Skill tool's own entry) and ``*-personality`` skills, which replace
the whole system message and with it the rules prompt.py writes.

A refusal carries a code for callers and a reason in plain words an agent
can pass on to the owner. Never imports ``nodes/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Collection, Dict, Mapping, Optional, Tuple

from services import node_allowlist
from services.employees.apps import AppSpec, ToolTemplate, allowed_when_asking_first, get_apps
from services.employees.genui_catalog import load_genui_catalog
from services.node_registry import get_node_class
from services.plugin.approval import approval_spec
from services.skill_runtime import is_personality_skill

#: The Skill tool's own entry on a Skills node.
SKILL_TOOL_NAME = "skill"
#: That entry as a Skills node starts with it (MasterSkillParams' default
#: ``skills_config``): on, and required.
SKILL_TOOL_ENTRY: Mapping[str, Any] = MappingProxyType({"enabled": True, "instructions": "", "isCustomized": False, "required": True})


@dataclass(frozen=True)
class BaseTool:
    type: str
    label: str
    params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: The ``node_roles`` key Hire records it under.
    role: Optional[str] = None


#: The tools every hire gets, in the order Hire adds them (Memory only when
#: the owner keeps memory across chats; Hire sets the Clock to the owner's
#: time zone). They read, or keep the employee's own checklist, notes and
#: board, so asking first never takes them away.
BASE_TOOLS: Tuple[BaseTool, ...] = (
    BaseTool("duckduckgoSearch", "Web search", MappingProxyType({"max_results": 5})),
    BaseTool("writeTodos", "Checklist", role="todos"),
    BaseTool("currentTimeTool", "Clock", MappingProxyType({"timezone": "UTC"})),
    BaseTool("simpleMemory", "Memory", role="memory"),
    BaseTool("canvas", "Canvas", role="canvas"),
)

_BASE_TOOLS_BY_TYPE: Dict[str, BaseTool] = {tool.type: tool for tool in BASE_TOOLS}

#: What a side effect that asking first rules out puts at stake.
_AT_STAKE = {"send": "can send things on your behalf", "money": "can spend money"}


@dataclass(frozen=True)
class Decision:
    """Whether something may be given to a hired employee. ``code`` and
    ``reason`` say why not (both empty when it may)."""

    allowed: bool
    code: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ToolDecision(Decision):
    node_type: str = ""
    #: The app the tool belongs to; None for one of ``BASE_TOOLS``.
    app: Optional[AppSpec] = None
    #: What to create it with, asking first applied.
    label: str = ""
    params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: Given in its ask-first form (``ask_first_params`` applied).
    read_only: bool = False
    #: The ``node_roles`` key Hire records it under, if any.
    role: Optional[str] = None


def asks_first(employee: Any) -> bool:
    """The employee's "ask me first" ground rule, from its saved row
    (``rules`` a dict) or the hire request being built (``rules`` a
    model). Missing means on."""
    rules = getattr(employee, "rules", None)
    value = rules.get("ask_first") if isinstance(rules, Mapping) else getattr(rules, "ask_first", None)
    return value is not False


def _app_tool(node_type: str, app: Optional[AppSpec]) -> Optional[Tuple[AppSpec, ToolTemplate]]:
    for candidate in (app,) if app is not None else get_apps().values():
        for tool in candidate.tools:
            if tool.type == node_type:
                return candidate, tool
    return None


def _refused(node_type: str, code: str, reason: str, **fields: Any) -> ToolDecision:
    return ToolDecision(allowed=False, code=code, reason=reason, node_type=node_type, **fields)


def check_tool(
    node_type: str,
    *,
    employee: Any,
    connected: Optional[Collection[str]],
    app: Optional[AppSpec] = None,
    allowed: Optional[Callable[[str], bool]] = None,
) -> ToolDecision:
    """May ``employee`` be given a ``node_type`` tool, and in what form.

    ``connected`` is the connected app ids (``Connections.connected_app_ids``),
    or None to skip that check. ``app`` narrows the lookup to one app's
    tools (the builder walking a hire's apps); ``allowed`` replaces the
    Hire allowlist (the builder's injected one)."""
    is_allowed = allowed or node_allowlist.is_hire_allowed
    base = _BASE_TOOLS_BY_TYPE.get(node_type) if app is None else None
    if base is not None:
        if not is_allowed(node_type):
            return _refused(node_type, "not_allowed", f"{base.label} isn't available to hired employees.", label=base.label)
        return ToolDecision(allowed=True, node_type=node_type, label=base.label, params=base.params, role=base.role)

    found = _app_tool(node_type, app)
    if found is None:
        cls = get_node_class(node_type)
        name = getattr(cls, "display_name", "") or node_type
        return _refused(node_type, "not_a_tool", f"{name} isn't something a hired employee can be given.")
    app, tool = found
    label = tool.label or app.name
    params = dict(tool.params)
    read_only = False
    if asks_first(employee) and not allowed_when_asking_first(tool.side_effects) and approval_spec(get_node_class(node_type)) is None:
        if not tool.ask_first_params:
            rule = load_genui_catalog()["ask_first_label"]
            return _refused(
                node_type,
                "asks_first",
                f'{app.name} {_AT_STAKE[tool.side_effects]}, so it stays off while "{rule}" is on.',
                app=app,
                label=label,
            )
        params.update(tool.ask_first_params)
        read_only = True
    if not is_allowed(node_type):
        return _refused(node_type, "not_allowed", f"{app.name} isn't available to hired employees.", app=app, label=label)
    if connected is not None and app.id not in connected:
        return _refused(
            node_type, "not_connected", f"{app.name} isn't connected yet. Connect it in Settings > Connectors first.", app=app, label=label
        )
    return ToolDecision(
        allowed=True, node_type=node_type, app=app, label=label, params=MappingProxyType(params), read_only=read_only, role=tool.role
    )


def check_skill(name: str) -> Decision:
    """May a hired employee be given the skill called ``name``."""
    name = str(name or "").strip()
    if not name:
        return Decision(allowed=False, code="invalid", reason="A skill needs a name.")
    if name == SKILL_TOOL_NAME:
        return Decision(allowed=False, code="reserved", reason='No skill can be called "skill": that is the Skill tool\'s own name.')
    if is_personality_skill(name):
        return Decision(
            allowed=False,
            code="personality",
            reason=f'"{name}" would replace their whole instructions, so a hired employee can\'t be given it.',
        )
    return Decision(allowed=True)


__all__ = [
    "BASE_TOOLS",
    "BaseTool",
    "Decision",
    "SKILL_TOOL_ENTRY",
    "SKILL_TOOL_NAME",
    "ToolDecision",
    "asks_first",
    "check_skill",
    "check_tool",
]

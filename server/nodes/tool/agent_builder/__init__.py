"""Agent Builder: an agent inspects its canvas and adds to it while it runs.

One ToolNode with an ``operation`` discriminator; the LLM sees one tool,
``agent_builder``:

* ``inspect_canvas``: the saved canvas, what is wired to the calling agent,
  and the catalogue of what it may add;
* ``add_tool``: a tool node on the agent's ``input-tools``;
* ``add_skill``: a skill on the agent's Skills node (masterSkill), or on a
  new one;
* ``add_subagent``: a teammate on a team lead's ``input-teammates``;
* ``create_workflow``: disabled (``_CREATE_WORKFLOW_ENABLED``).

Every change goes through ``services.workflow_storage.mutate.apply_graph_additions``,
the server's one path for growing a saved workflow: one transaction,
canonical ids, labels unique against the graph, the handles every
server-side writer uses (services/graph_build.py), real parameter rows, and
an announcement to open editors (``workflow_ops_apply`` with
``persisted: true``). Its ledger is keyed by the tool call, so a retried
call replays the first result instead of adding twice.

The calling agent is the one the runtime names (``invoking_agent_node_id``
on Temporal, ``parent_node_id`` on every path), never guessed from edges:
one Agent Builder node can serve several agents.

A hired employee (the workflow has an employee row) is given things by the
rule Hire applies (services/employees/policy.py): tools from the app
registry and the tools every hire gets, nothing that sends or spends while
the owner asks to be asked first, only apps that are connected and
allowlisted; skills from the owner's library or Settings > Skills'
Discover folder, with their text copied in. What is added goes to the
worker and to the agent the owner talks to. A refusal is one plain
sentence the agent passes on.

When it works: a tool added for the calling agent is bound for the rest of
its run (the agent loop's hot rebind, on by default). A deployed run starts
from the workflow's snapshot, which lacks it until a restart (Apply, for an
employee), so ``add_tool`` for a saved tool this run does not have returns
that node to bind again and saves nothing. A skill merged into an existing
Skills node applies from the next run; a new node or edge waits for the
restart.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services import workflow_ops
from services.graph_build import (
    SKILL_INPUT,
    TOOLS_INPUT,
    Edge,
    GraphAdditions,
    NewNode,
    ParamMerge,
    skill_edge,
    tool_edge,
)
from services.node_registry import get_node_class, registered_node_classes
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode
from services.workflow_naming import next_available_slug


logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_TEAMMATES_INPUT = "input-teammates"
#: A teammate feeds its lead from its top output, as the editor draws it.
_TEAMMATE_OUTPUT = "output-top"
_MASTER_SKILL_TYPE = "masterSkill"
_AGENT_BUILDER_TYPE = "agentBuilder"
_TASK_MANAGER_TYPE = "taskManager"
_CONTEXT_TYPE = "context"
_TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})
_DENIED_TOOL_TYPES = frozenset({_AGENT_BUILDER_TYPE, _MASTER_SKILL_TYPE, _TASK_MANAGER_TYPE})
_KEY_PARAM_FIELDS = ("provider", "model", "operation", "url", "query")
#: Settings > Skills' Discover folder under server/skills/ (the client's
#: ``DISCOVER_SKILL_FOLDER``, features/home/data/skills.ts).
_DISCOVER_SKILL_FOLDER = "employee"
_APPLY_HINT = "It becomes part of all your work when the owner presses Apply on your page."

# Temporary feature flag — set to True to re-enable the create_workflow
# operation. Flipping this constant restores the operation's prior
# behaviour (validation + slug allocation + database.save_workflow).
# The implementation below stays intact so re-enabling is one line.
_CREATE_WORKFLOW_ENABLED = False


# ----------------------------------------------------------------------------
# What an operation works against
# ----------------------------------------------------------------------------


@dataclass
class _Canvas:
    """The saved workflow an operation changes (it may have grown since this
    run started), or this run's own canvas when nothing is saved."""

    workflow_id: Optional[str]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    saved: bool

    def node(self, node_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((node for node in self.nodes if node_id and node.get("id") == node_id), None)

    def label(self, node_id: str) -> str:
        node = self.node(node_id) or {}
        return str((node.get("data") or {}).get("label") or node.get("type") or node_id)

    def position(self, node_id: str) -> Tuple[float, float]:
        position = (self.node(node_id) or {}).get("position") or {}
        return float(position.get("x") or 0), float(position.get("y") or 0)

    def sources(self, target: str, handle: str) -> List[str]:
        """The nodes wired into ``target``'s ``handle``, in edge order."""
        return [
            str(edge["source"])
            for edge in self.edges
            if edge.get("target") == target and edge.get("targetHandle") == handle and edge.get("source")
        ]

    def source_of_type(self, target: str, handle: str, node_type: str) -> Optional[str]:
        for source in self.sources(target, handle):
            if (self.node(source) or {}).get("type") == node_type:
                return source
        return None


@dataclass
class _Employee:
    """A hired employee's parts, from its row's ``node_roles``."""

    row: Any
    #: The worker and the agent the owner talks to, in the saved graph.
    agents: List[str]
    #: The hire's Skills node, when the graph has it.
    skills: Optional[str]


@dataclass(frozen=True)
class _Skill:
    name: str
    description: str
    #: Empty for a built-in skill the runtime reads from its SKILL.md.
    instructions: str


async def _load_canvas(ctx: NodeContext, database: Any) -> _Canvas:
    """The saved graph (read fresh, so operations in one run see each
    other's additions), else the run's own canvas."""
    if ctx.workflow_id:
        try:
            workflow = await database.get_workflow(ctx.workflow_id)
        except Exception:  # noqa: BLE001 — a failed read falls back to the run's canvas
            logger.warning("Agent Builder could not read the workflow", workflow_id=ctx.workflow_id, exc_info=True)
            workflow = None
        if workflow is not None:
            data = workflow.data or {}
            return _Canvas(ctx.workflow_id, list(data.get("nodes") or []), list(data.get("edges") or []), saved=True)
    return _Canvas(ctx.workflow_id, list(ctx.nodes or []), list(ctx.edges or []), saved=False)


async def _employee_of(database: Any, canvas: _Canvas) -> Optional[_Employee]:
    if not canvas.saved:
        return None
    from services.employees import store
    from services.employees.start import AGENT_ROLES

    row = await store.get_by_workflow(database, canvas.workflow_id)
    if row is None:
        return None
    roles = row.node_roles or {}
    agents = [roles[role] for role in AGENT_ROLES if canvas.node(roles.get(role))]
    skills = roles.get("skills") if canvas.node(roles.get("skills")) else None
    return _Employee(row=row, agents=list(dict.fromkeys(agents)), skills=skills)


def _caller(ctx: NodeContext) -> Optional[str]:
    """The agent that called: both runtimes name it (Temporal as
    ``invoking_agent_node_id``, every path as ``parent_node_id``)."""
    raw = ctx.raw or {}
    caller = raw.get("invoking_agent_node_id") or raw.get("parent_node_id")
    return str(caller) if caller else None


def _mutation_id(ctx: NodeContext, params: BaseModel, caller: Optional[str]) -> str:
    """The ledger key of this call's change: the runtime's tool-call id when
    it has one (stable across retries of the same call), scoped to the run;
    otherwise the request itself."""
    raw = ctx.raw or {}
    run = str(ctx.execution_id or "")
    call = str(raw.get("tool_call_id") or "")
    if call:
        return f"agent-builder:{run}:{call}"
    request = json.dumps({"request": params.model_dump(), "caller": caller, "run": run}, sort_keys=True, default=str)
    return f"agent-builder:{hashlib.sha256(request.encode('utf-8')).hexdigest()}"


async def _save(ctx: NodeContext, database: Any, canvas: _Canvas, additions: GraphAdditions, params: BaseModel, caller: Optional[str]) -> Any:
    """Apply ``additions`` (possibly none: the call still claims its ledger
    key, so a retry replays what the first attempt saved). None when the
    workflow is gone; raises ValueError when the graph changed under the
    plan."""
    from services.workflow_storage.mutate import apply_graph_additions

    return await apply_graph_additions(
        database,
        str(canvas.workflow_id),
        additions,
        mutation_id=_mutation_id(ctx, params, caller),
        caller_node_id=caller,
    )


def _unsaved(canvas: _Canvas, targets: Sequence[str]) -> Optional[str]:
    """Why ``targets`` cannot be changed on the saved graph, if they cannot."""
    if not canvas.saved:
        return "Save the workflow first, then ask again."
    if not targets:
        return "Only an agent can ask me to add things."
    if any(canvas.node(target) is None for target in targets):
        return "Save the workflow first: the agent that asked isn't in the saved workflow yet."
    return None


def _refused(operation: str, summary: str) -> "AgentBuilderOutput":
    return AgentBuilderOutput(operation=operation, summary=summary, operations=[])


def _rebind_on(ctx: NodeContext) -> bool:
    """The user's "Auto-Rebind Tools After Canvas Changes" (on by default),
    which the agent loop forwards as ``auto_rebind_tools``."""
    return bool((ctx.raw or {}).get("auto_rebind_tools", True))


def _summary_suffix(ctx: NodeContext) -> str:
    """Whether a new tool is callable in this run (the rebind) or waits."""
    return "Available immediately — call it in your next response." if _rebind_on(ctx) else "Available on your next turn."


def _log_op_entry(op: str, ctx: NodeContext, **fields: Any) -> None:
    """One INFO line per call. The node and workflow ids ride the log context
    BaseNode.execute binds; an empty run canvas here means the runtime did
    not forward it."""
    logger.info(
        "Agent Builder operation",
        operation=op,
        caller=_caller(ctx),
        run_nodes=len(ctx.nodes or []),
        **{key: value for key, value in fields.items() if value not in (None, "")},
    )


# ----------------------------------------------------------------------------
# Catalogues: what the agent may add
# ----------------------------------------------------------------------------


def _allowlist_config() -> Dict[str, Any]:
    """The operator allowlist (server/config/node_allowlist.json), the same
    source the UI palette reads. The service falls back to show-all on a
    missing or malformed file, so this never raises."""
    try:
        from services.node_allowlist import get_node_allowlist_service

        return get_node_allowlist_service().get_config()
    except Exception as exc:  # noqa: BLE001 — defensive: never fail catalogue
        logger.debug("[agentBuilder] node_allowlist read failed: %s", exc)
        return {"disabled_nodes": [], "disabled_groups": [], "disabled_skill_folders": []}


def _is_blocked_by_allowlist(cls: Any, ntype: str, config: Dict[str, Any]) -> bool:
    """``True`` when ``ntype`` or any group in ``cls.group`` is in the
    operator blocklist. Honors both ``disabled_nodes`` (per-type) and
    ``disabled_groups`` (every plugin in the named group)."""
    if ntype in (config.get("disabled_nodes") or ()):
        return True
    disabled_groups = set(config.get("disabled_groups") or ())
    if disabled_groups:
        plugin_groups = set(getattr(cls, "group", ()) or ())
        if plugin_groups & disabled_groups:
            return True
    return False


def _allowed_tool_types() -> set[str]:
    """Tool node types an agent in an editor-built workflow may add: pure
    ToolNodes (``component_kind == 'tool'``) and dual-purpose ActionNodes
    (``usable_as_tool=True``, chat models excluded), minus
    ``_DENIED_TOOL_TYPES`` (no Agent Builder, Skills node or Task Manager)
    and the operator's ``disabled_nodes`` / ``disabled_groups``."""
    config = _allowlist_config()
    out: set[str] = set()
    for ntype, cls in registered_node_classes().items():
        if ntype in _DENIED_TOOL_TYPES:
            continue
        kind = getattr(cls, "component_kind", "")
        is_tool = kind == "tool"
        is_dual_purpose = bool(getattr(cls, "usable_as_tool", False)) and kind != "model"
        if not (is_tool or is_dual_purpose):
            continue
        if _is_blocked_by_allowlist(cls, ntype, config):
            continue
        out.add(ntype)
    return out


def _allowed_subagent_types() -> set[str]:
    """Agent types a team lead may add as teammates, minus the operator's
    ``disabled_nodes`` / ``disabled_groups``."""
    config = _allowlist_config()
    return {
        ntype
        for ntype, cls in registered_node_classes().items()
        if getattr(cls, "component_kind", "") == "agent" and not _is_blocked_by_allowlist(cls, ntype, config)
    }


def _is_team_lead(node_type: str) -> bool:
    return node_type in _TEAM_LEAD_TYPES


def _description(cls: Any) -> str:
    return getattr(cls, "tool_description", "") or getattr(cls, "description", "") or ""


def _catalogue_tools() -> List[Dict[str, Any]]:
    registry = registered_node_classes()
    catalogue: List[Dict[str, Any]] = []
    for ntype in sorted(_allowed_tool_types()):
        cls = registry.get(ntype)
        if cls is None:
            continue
        catalogue.append({"type": ntype, "display_name": getattr(cls, "display_name", "") or ntype, "description": _description(cls)})
    return catalogue


def _catalogue_agents() -> List[Dict[str, Any]]:
    registry = registered_node_classes()
    catalogue: List[Dict[str, Any]] = []
    for ntype in sorted(_allowed_subagent_types()):
        cls = registry.get(ntype)
        if cls is None:
            continue
        catalogue.append(
            {"type": ntype, "display_name": getattr(cls, "display_name", "") or ntype, "description": getattr(cls, "description", "") or ""}
        )
    return catalogue


def _offered_builtins(*, employee: bool) -> Dict[str, Any]:
    """Built-in skills an agent may add, by frontmatter name, from the
    SkillLoader registry (populated at startup). A hired employee gets only
    Settings > Skills' Discover folder; any other agent every skill outside
    the operator's ``disabled_skill_folders`` (matched on any ancestor of
    its SKILL.md directory)."""
    from services.skill_loader import get_skill_loader

    blocked = set(_allowlist_config().get("disabled_skill_folders") or [])
    offered: Dict[str, Any] = {}
    for name, meta in sorted(get_skill_loader()._registry.items()):
        if meta.path is None:
            continue
        if employee:
            wanted = meta.path.parent.name == _DISCOVER_SKILL_FOLDER
        else:
            wanted = not {parent.name for parent in meta.path.parents} & blocked
        if wanted:
            offered[name] = meta
    return offered


async def _library(database: Any) -> List[Dict[str, Any]]:
    """Settings > Skills: every skill in the owner's library that has text
    (on or off for new hires: that switch is about new hires only)."""
    rows = await database.get_all_user_skills(active_only=False)
    return [row for row in rows if str(row.get("name") or "").strip() and str(row.get("instructions") or "").strip()]


async def _catalogue_skills(database: Any, *, employee: bool) -> List[Dict[str, Any]]:
    """The owner's library, then the offered built-ins it does not shadow
    (the order ``_find_skill`` resolves a name in). A hired employee's list
    leaves out the names policy refuses."""
    library = await _library(database)
    names = {str(row["name"]) for row in library}
    catalogue = [{"name": str(row["name"]), "description": str(row.get("description") or "")} for row in library]
    catalogue += [
        {"name": name, "description": meta.description or ""} for name, meta in _offered_builtins(employee=employee).items() if name not in names
    ]
    if employee:
        from services.employees.policy import check_skill

        catalogue = [entry for entry in catalogue if check_skill(entry["name"]).allowed]
    return catalogue


async def _connected_apps() -> List[str]:
    """The app ids whose credentials are connected (config/employee_apps.json)."""
    from services.employees.connections import Connections
    from services.plugin.deps import get_auth_service

    return await Connections(get_auth_service()).connected_app_ids()


async def _owner_timezone(database: Any) -> str:
    from services.employees.context import SETTINGS_USER_ID

    settings = await database.get_user_settings(SETTINGS_USER_ID) or {}
    return str(settings.get("profile_timezone") or "UTC")


async def _employee_tools(employee: _Employee) -> List[Dict[str, Any]]:
    """What policy lets this employee be given: the tools every hire gets,
    then the registry's app tools, each in the form it would be added."""
    from services.employees.apps import get_apps
    from services.employees.policy import BASE_TOOLS, check_tool

    connected = set(await _connected_apps())
    candidates = [(base.type, None) for base in BASE_TOOLS] + [(tool.type, app) for app in get_apps().values() for tool in app.tools]
    catalogue: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for node_type, app in candidates:
        if node_type in seen:
            continue
        decision = check_tool(node_type, employee=employee.row, connected=None, app=app)
        if not decision.allowed:
            continue
        seen.add(node_type)
        entry: Dict[str, Any] = {"type": node_type, "display_name": decision.label, "description": _description(get_node_class(node_type))}
        if app is not None:
            entry["app"] = app.name
            entry["connected"] = app.id in connected
        if decision.read_only:
            entry["read_only"] = True
        catalogue.append(entry)
    return catalogue


async def _find_skill(database: Any, name: str, *, employee: bool) -> Optional[_Skill]:
    """A skill by its frontmatter name: the owner's library first, with its
    text (the runtime's fallback for an entry without text reads files
    only), then the offered built-ins. A built-in keeps no text, so edits to
    its SKILL.md apply, except for a hired employee, whose copy is fixed at
    the time it is added, as Hire fixes a hire's library skills."""
    row = await database.get_user_skill(name)
    if row and str(row.get("instructions") or "").strip():
        return _Skill(name=name, description=str(row.get("description") or ""), instructions=str(row["instructions"]))
    meta = _offered_builtins(employee=employee).get(name)
    if meta is None:
        return None
    if not employee:
        return _Skill(name=name, description=meta.description or "", instructions="")
    from services.skill_loader import get_skill_loader

    skill = get_skill_loader().load_skill(name)
    if skill is None or not skill.instructions.strip():
        return None
    return _Skill(name=name, description=meta.description or "", instructions=skill.instructions)


def _key_params(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: parameters[key] for key in _KEY_PARAM_FIELDS if key in parameters}


def _run_has_tool(ctx: NodeContext, node_id: str, caller: str) -> bool:
    """Whether this run's canvas wires ``node_id`` to ``caller`` as a tool,
    so the agent has it bound already."""
    return any(
        edge.get("source") == node_id and edge.get("target") == caller and edge.get("targetHandle") == TOOLS_INPUT for edge in ctx.edges or []
    )


def _added_nodes(result: Any) -> set[str]:
    return {str(op.get("minted_id")) for op in result.operations if op.get("type") == "add_node"}


def _tool_summary(ctx: NodeContext, *, employee: bool, label: str, node_id: str, changed: bool, bind: bool) -> str:
    """What ``add_tool`` did, for the agent. An employee's is plain, since
    the agent passes it on to the owner."""
    if not employee:
        if changed:
            return f"Added '{label}' as a tool (node id={node_id}). {_summary_suffix(ctx)}"
        if bind:
            return f"Tool '{label}' is wired to you (node id={node_id}); this run started without it. {_summary_suffix(ctx)}"
        return f"Tool '{label}' is already wired to you (node id={node_id}). Reusing existing instance."
    now = "You can use it now in this conversation." if _rebind_on(ctx) else None
    if changed:
        return " ".join(part for part in (f"Added {label}.", now, _APPLY_HINT) if part)
    if bind:
        return f"You have {label}. {now or _APPLY_HINT}"
    return f"You already have {label}. Use it directly."


# ----------------------------------------------------------------------------
# Params + Output schemas
# ----------------------------------------------------------------------------


class AgentBuilderParams(BaseModel):
    """Multi-op schema. The LLM picks ``operation``, then fills the
    fields whose ``displayOptions`` enable them for that op.
    """

    operation: Literal[
        "inspect_canvas",
        "add_tool",
        "add_skill",
        "add_subagent",
        "create_workflow",
    ] = Field(
        default="inspect_canvas",
        description=(
            "Which canvas-mutation to perform. Always call "
            "'inspect_canvas' first to see what's already wired and what "
            "you may add."
        ),
    )

    # add_tool
    node_type: str = Field(
        default="",
        description="For add_tool: the tool's node type, from inspect_canvas available_tools (e.g. 'httpRequest').",
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_tool"]}}},
    )

    # add_skill
    skill_name: str = Field(
        default="",
        description="For add_skill: the skill's name, from inspect_canvas available_skills (e.g. 'book-appointments').",
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_skill"]}}},
    )

    # add_subagent
    agent_type: str = Field(
        default="",
        description=(
            "For add_subagent: agent node type to spawn (e.g. "
            "'coding_agent', 'web_agent'). Must be component_kind='agent' "
            "and not a team-lead. Caller must itself be a team-lead "
            "(orchestrator_agent / ai_employee)."
        ),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_subagent"]}}},
    )

    # create_workflow
    workflow_name: str = Field(
        default="",
        description="For create_workflow: display name (non-empty).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )
    workflow_description: str = Field(
        default="",
        description="For create_workflow: optional one-line description.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class AgentBuilderOutput(BaseModel):
    operation: Optional[str] = None
    summary: Optional[str] = None
    #: For ``add_tool`` / ``add_subagent``: the ops this call saved (as
    #: announced to editors), plus a saved tool the calling agent's run
    #: lacks. The agent loop binds every tool ``add_node`` here for the rest
    #: of the run. Empty for ``add_skill``.
    operations: Optional[List[Dict[str, Any]]] = None
    # inspect_canvas extras
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    you: Optional[Dict[str, Any]] = None
    #: A hired employee's rules: {asks_first, agents}.
    employee: Optional[Dict[str, Any]] = None
    # What the agent may add, each entry with a name and a description.
    available_tools: Optional[List[Dict[str, Any]]] = None
    available_agents: Optional[List[Dict[str, Any]]] = None
    available_skills: Optional[List[Dict[str, Any]]] = None
    # create_workflow extras
    workflow_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------
# Node
# ----------------------------------------------------------------------------


class AgentBuilderNode(ToolNode):
    type = _AGENT_BUILDER_TYPE
    display_name = "Agent Builder"
    subtitle = "Runtime canvas-mutation tool"
    group = ("tool", "ai")
    description = (
        "Lets the agent it is wired to inspect the workflow canvas and add "
        "to it while it runs: tools, skills, and (for team leads) teammates. "
        "Changes are saved and show on the canvas at once. Wire to an AI "
        "agent's input-tools handle."
    )
    component_kind = "tool"
    # The per-tool activity gets the run's canvas (ctx.nodes / ctx.edges)
    # only for nodes that ask: add_tool compares it with the saved graph to
    # tell whether a saved tool is already bound in this run.
    needs_canvas = True
    tool_name = "agent_builder"
    tool_description = (
        "Inspect and grow the workflow canvas while you run. ALWAYS call "
        "inspect_canvas FIRST: it lists what you may add (available_tools, "
        "available_agents, available_skills) and the current canvas. "
        "add_tool wires a tool from available_tools to you (a tool you "
        "already have but lack in this run is bound again); add_skill adds "
        "a skill from available_skills; add_subagent adds a teammate (team "
        "leads only). create_workflow is disabled. When a change is "
        "refused, the summary says why in one plain sentence: pass it on."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {"hideRunButton": True}
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = AgentBuilderParams
    Output = AgentBuilderOutput

    # ---- inspect_canvas (read-only) ---------------------------------------

    @Operation("inspect_canvas")
    async def inspect_canvas(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database
        from services.workflow_context_migration import load_node_parameters

        _log_op_entry("inspect_canvas", ctx)
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)
        parameters = await load_node_parameters(database, canvas.nodes)
        type_by_id = {node.get("id"): node.get("type") for node in canvas.nodes}

        node_summaries = [
            {
                "id": node.get("id"),
                "type": node.get("type"),
                "label": (node.get("data") or {}).get("label") or node.get("type"),
                "key_params": _key_params(parameters.get(str(node.get("id")), {})),
            }
            for node in canvas.nodes
        ]
        edge_summaries = [
            {
                "source": edge.get("source"),
                "target": edge.get("target"),
                "source_handle": edge.get("sourceHandle"),
                "target_handle": edge.get("targetHandle"),
            }
            for edge in canvas.edges
        ]
        you = None
        tools: List[Dict[str, Any]] = []
        if caller:
            incoming = [
                {"source_id": edge.get("source"), "source_type": type_by_id.get(edge.get("source")), "target_handle": edge.get("targetHandle")}
                for edge in canvas.edges
                if edge.get("target") == caller
            ]
            outgoing = [
                {"target_id": edge.get("target"), "target_type": type_by_id.get(edge.get("target")), "source_handle": edge.get("sourceHandle")}
                for edge in canvas.edges
                if edge.get("source") == caller
            ]
            you = {"node_id": caller, "incoming": incoming, "outgoing": outgoing}
            tools = [entry for entry in incoming if entry["target_handle"] == TOOLS_INPUT]

        available_skills = await _catalogue_skills(database, employee=employee is not None)
        if employee is None:
            available_tools, available_agents = _catalogue_tools(), _catalogue_agents()
            rules = None
        else:
            from services.employees.policy import asks_first

            # A hired employee adds within policy, and never teammates.
            available_tools, available_agents = await _employee_tools(employee), []
            rules = {"asks_first": asks_first(employee.row), "agents": employee.agents}

        parts = [f"{len(canvas.nodes)} nodes"]
        if tools:
            types = ", ".join(sorted({entry["source_type"] or "?" for entry in tools}))
            parts.append(f"{len(tools)} tool(s) wired to you ({types})")
        parts.append(f"{len(available_tools)} tool / {len(available_agents)} agent / {len(available_skills)} skill types you may add")
        return AgentBuilderOutput(
            operation="inspect_canvas",
            summary=", ".join(parts) + ".",
            nodes=node_summaries,
            edges=edge_summaries,
            you=you,
            employee=rules,
            available_tools=available_tools,
            available_agents=available_agents,
            available_skills=available_skills,
        )

    # ---- add_tool ---------------------------------------------------------

    @Operation("add_tool")
    async def add_tool(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database

        _log_op_entry("add_tool", ctx, node_type=params.node_type)
        node_type = (params.node_type or "").strip()
        if not node_type:
            return _refused("add_tool", "add_tool: node_type is required.")
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)

        if employee is None:
            allowed = _allowed_tool_types()
            if node_type not in allowed:
                return _refused(
                    "add_tool",
                    f"add_tool: '{node_type}' is not an allowed tool type. Allowed types: {', '.join(sorted(allowed))}. "
                    "Call inspect_canvas for the full catalogue with descriptions.",
                )
            label = getattr(get_node_class(node_type), "display_name", "") or node_type
            tool_params: Dict[str, Any] = {}
            targets = [caller] if caller else []
        else:
            from services.employees.policy import check_tool

            if get_node_class(node_type) is None:
                # Not a node type at all (a display name, say): a hint for
                # the agent, not a refusal to pass on.
                return _refused("add_tool", f"add_tool: '{node_type}' is not a node type. Use a type from inspect_canvas available_tools.")
            decision = check_tool(node_type, employee=employee.row, connected=await _connected_apps())
            if not decision.allowed:
                return _refused("add_tool", decision.reason)
            label, tool_params = decision.label, dict(decision.params)
            if decision.app is None and "timezone" in tool_params:
                tool_params["timezone"] = await _owner_timezone(database)
            targets = employee.agents or ([caller] if caller else [])
        problem = _unsaved(canvas, targets)
        if problem:
            return _refused("add_tool", problem)

        # A target that has a tool of this type keeps it (a second would give
        # it two tools of one name); the rest get the caller's, else any
        # target's, else a new one.
        have = {target: canvas.source_of_type(target, TOOLS_INPUT, node_type) for target in targets}
        existing = have.get(caller) or next((node for node in have.values() if node), None)
        if existing is None:
            x, y = canvas.position(targets[0])
            spread = 170 * len(canvas.sources(targets[0], TOOLS_INPUT))
            additions = GraphAdditions(
                nodes=(NewNode("tool", node_type, label, tool_params, position=(x - 240 + spread, y + 240)),),
                edges=tuple(tool_edge("tool", target) for target in targets),
            )
        else:
            additions = GraphAdditions(edges=tuple(tool_edge(existing, target) for target in targets if have[target] is None))
        try:
            result = await _save(ctx, database, canvas, additions, params, caller)
        except ValueError:
            logger.warning("Agent Builder change no longer fits the workflow", workflow_id=canvas.workflow_id, exc_info=True)
            return _refused("add_tool", "The workflow changed while I was adding to it. Try again.")
        if result is None:
            return _refused("add_tool", "Save the workflow first, then ask again.")

        node_id = str(result.node_ids.get("tool") or existing)
        label = result.labels.get("tool") or canvas.label(node_id)
        operations = [dict(op) for op in result.operations]
        changed = bool(operations)
        # A saved tool this run started without (it was added after the
        # run's snapshot) is handed back for the agent loop to bind.
        bind = caller in targets and node_id not in _added_nodes(result) and not _run_has_tool(ctx, node_id, str(caller))
        if bind:
            saved = await database.get_node_parameters(node_id) or {}
            operations.insert(0, workflow_ops.add_node(node_id, node_type, saved, label=label, minted_id=node_id))
        return AgentBuilderOutput(
            operation="add_tool",
            summary=_tool_summary(ctx, employee=employee is not None, label=label, node_id=node_id, changed=changed, bind=bind),
            operations=operations,
        )

    # ---- add_skill --------------------------------------------------------

    @Operation("add_skill")
    async def add_skill(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database

        _log_op_entry("add_skill", ctx, skill_name=params.skill_name)
        name = (params.skill_name or "").strip()
        if not name:
            return _refused("add_skill", "add_skill: skill_name is required.")
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)
        if employee is not None:
            from services.employees.policy import check_skill

            decision = check_skill(name)
            if not decision.allowed:
                return _refused("add_skill", decision.reason)
        skill = await _find_skill(database, name, employee=employee is not None)
        if skill is None:
            if employee is not None:
                return _refused("add_skill", f"There's no skill called '{name}' in the owner's library or in Discover.")
            return _refused("add_skill", f"add_skill: no skill named '{name}'. Call inspect_canvas for the available skills.")
        targets = (employee.agents if employee is not None else []) or ([caller] if caller else [])
        problem = _unsaved(canvas, targets)
        if problem:
            return _refused("add_skill", problem)

        entry = {"enabled": True, "instructions": skill.instructions, "isCustomized": False, "description": skill.description}
        # Each target's Skills node gets the skill; a target without one is
        # wired to the shared node (never a second one: two Skills nodes on
        # one agent collide on the Skill tool's own entry), else to a new one.
        skills_of = {target: canvas.source_of_type(target, SKILL_INPUT, _MASTER_SKILL_TYPE) for target in targets}
        bare = [target for target in targets if skills_of[target] is None]
        shared = (employee.skills if employee is not None else None) or next((node for node in skills_of.values() if node), None)
        holders = list(dict.fromkeys([node for node in skills_of.values() if node] + ([shared] if shared and bare else [])))
        merges = []
        for holder in holders:
            row = await database.get_node_parameters(holder) or {}
            if not ((row.get("skills_config") or {}).get(name) or {}).get("enabled"):
                merges.append(ParamMerge(holder, {"skills_config": {name: entry}}))
        nodes: Tuple[NewNode, ...] = ()
        if bare and shared is None:
            from services.employees.policy import SKILL_TOOL_ENTRY, SKILL_TOOL_NAME

            x, y = canvas.position(bare[0])
            config = {SKILL_TOOL_NAME: dict(SKILL_TOOL_ENTRY), name: entry}
            nodes = (
                NewNode(
                    "skills",
                    _MASTER_SKILL_TYPE,
                    "Skills" if employee is not None else "Master Skill",
                    {"skill_folder": "assistant", "skills_config": config},
                    position=(x - 420, y + 240),
                ),
            )
        edges = tuple(skill_edge("skills" if nodes else str(shared), target) for target in bare)
        try:
            result = await _save(ctx, database, canvas, GraphAdditions(nodes=nodes, edges=edges, merges=tuple(merges)), params, caller)
        except ValueError:
            logger.warning("Agent Builder change no longer fits the workflow", workflow_id=canvas.workflow_id, exc_info=True)
            return _refused("add_skill", "The workflow changed while I was adding to it. Try again.")
        if result is None:
            return _refused("add_skill", "Save the workflow first, then ask again.")

        # No operations back to the agent: a Skills node is nothing to bind,
        # and its merged row (every skill's text) would only grow the stored
        # conversation. Editors got the batch from the save.
        rewired = any(op.get("type") in ("add_node", "add_edge") for op in result.operations)
        holder = result.node_ids.get("skills") or shared or (holders[0] if holders else "")
        if employee is not None:
            if rewired:
                summary = f"Learned '{name}'. {_APPLY_HINT}"
            elif result.operations:
                summary = f"Learned '{name}'. It applies from the next message."
            else:
                summary = f"You already have the skill '{name}'."
        elif rewired:
            summary = (
                f"Added '{name}' to a Master Skill wired to you (node id={holder}). "
                "It applies from your next run (a running deployment needs a restart first)."
            )
        elif result.operations:
            summary = f"Enabled '{name}' on your Master Skill (node id={holder}). It applies from your next run."
        else:
            summary = f"Skill '{name}' is already enabled on your Master Skill (node id={holder}). No change needed."
        return AgentBuilderOutput(operation="add_skill", summary=summary, operations=[])

    # ---- add_subagent -----------------------------------------------------

    @Operation("add_subagent")
    async def add_subagent(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database

        _log_op_entry("add_subagent", ctx, agent_type=params.agent_type)
        agent_type = (params.agent_type or "").strip()
        if not agent_type:
            return _refused("add_subagent", "add_subagent: agent_type is required.")
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        if await _employee_of(database, canvas) is not None:
            return _refused("add_subagent", "A hired employee can't bring in helper agents. The owner can set that up in Dev mode.")
        caller_type = str((canvas.node(caller) or {}).get("type") or "")
        if not _is_team_lead(caller_type):
            leads = ", ".join(sorted(_TEAM_LEAD_TYPES))
            return _refused(
                "add_subagent", f"add_subagent: only team-lead agents ({leads}) can spawn delegates. This agent is '{caller_type}'."
            )
        allowed = _allowed_subagent_types()
        if agent_type not in allowed:
            return _refused(
                "add_subagent",
                f"add_subagent: '{agent_type}' is not an allowed agent type. Allowed types: {', '.join(sorted(allowed))}. "
                "Call inspect_canvas for the full catalogue with descriptions.",
            )
        if _is_team_lead(agent_type):
            return _refused("add_subagent", f"add_subagent: cannot spawn another team-lead ('{agent_type}'); pick a specialized agent instead.")
        problem = _unsaved(canvas, [str(caller)])
        if problem:
            return _refused("add_subagent", problem)
        # Custom aiAgent teammates are repeatable (their delegation identity
        # is per node); every other type has one type-wide delegate name.
        existing = None if agent_type == "aiAgent" else canvas.source_of_type(str(caller), _TEAMMATES_INPUT, agent_type)
        x, y = canvas.position(str(caller))
        label = getattr(get_node_class(agent_type), "display_name", "") or agent_type
        additions = GraphAdditions(
            nodes=(
                NewNode("agent", agent_type, label, position=(x + 300, y + 200)),
                NewNode("context", _CONTEXT_TYPE, "Context", position=(x + 300, y + 20), context_of="agent"),
            ),
            edges=(Edge("agent", _TEAMMATE_OUTPUT, str(caller), _TEAMMATES_INPUT),),
        )
        try:
            result = await _save(ctx, database, canvas, GraphAdditions() if existing else additions, params, caller)
        except ValueError:
            logger.warning("Agent Builder change no longer fits the workflow", workflow_id=canvas.workflow_id, exc_info=True)
            return _refused("add_subagent", "The workflow changed while I was adding to it. Try again.")
        if result is None:
            return _refused("add_subagent", "Save the workflow first, then ask again.")
        if not result.node_ids.get("agent"):
            return _refused("add_subagent", f"Teammate '{agent_type}' is already wired to you (node id={existing}). Reusing existing instance.")
        label = result.labels.get("agent") or label
        return AgentBuilderOutput(
            operation="add_subagent",
            summary=f"Added '{label}' as a teammate. {_summary_suffix(ctx)} (configure provider/model first).",
            operations=[dict(op) for op in result.operations],
        )

    # ---- create_workflow --------------------------------------------------

    @Operation("create_workflow")
    async def create_workflow(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("create_workflow", ctx, workflow_name=params.workflow_name)
        # Temporary disable — flip ``_CREATE_WORKFLOW_ENABLED`` at the
        # module top to restore. The body below is intact so the
        # feature can be re-enabled without rewriting validation +
        # persistence logic.
        if not _CREATE_WORKFLOW_ENABLED:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary=(
                    "create_workflow is temporarily disabled. Mutate the "
                    "current workflow instead — add_tool / add_skill / "
                    "add_subagent are still available."
                ),
            )
        name = (params.workflow_name or "").strip()
        if not name:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary="create_workflow: workflow_name is required.",
            )

        from services.plugin.deps import get_database

        database = get_database()
        workflow_id = await database.allocate_workflow_id()
        slug = await next_available_slug(name, database)
        start_node_id = f"{workflow_id}:start:1"
        description = (params.workflow_description or "").strip()
        workflow_data = {
            "id": workflow_id,
            "name": name,
            "slug": slug,
            "description": description,
            "nodes": [
                {
                    "id": start_node_id,
                    "type": "start",
                    "position": {"x": 200, "y": 200},
                    "data": {"label": "Start"},
                }
            ],
            "edges": [],
            "nodeParameters": {},
        }

        ok = await database.save_workflow(
            workflow_id,
            name,
            slug,
            workflow_data,
            description=description or None,
        )
        if not ok:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary=f"create_workflow: failed to persist '{name}'.",
            )
        return AgentBuilderOutput(
            operation="create_workflow",
            summary=(f"Created workflow '{name}' (slug: {slug}). " "User can switch to it from the toast notification."),
            workflow_id=workflow_id,
        )

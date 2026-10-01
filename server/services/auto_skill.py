"""Auto-add-skill policy.

Decides what should happen when an edge is connected (or disconnected)
between a tool node and an AI agent's ``input-tools`` handle. Owns the
domain rules so the frontend stays a thin dispatcher: it sends the
edge details + current Master Skill state, and gets back a workflow-
operations batch (see ``services.workflow_ops``) to apply.

Why backend: the rules read ``visuals.json`` for the tool->skill
reverse map, the plugin registry for agent classification, and the
canonical ``SkillConfig`` shape that ``handlers/ai.py``
``_collect_agent_connections`` consumes at execution time. Putting
those rules anywhere but the backend would scatter the source of
truth across two languages.

Output contract: standard workflow-ops batch
``{"operations": [...]}``. See
``docs-internal/workflow_ops_protocol.md``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

from nodes._visuals import get_skill
from services.node_registry import get_node_class
from services import workflow_ops


_TOOL_HANDLE = "input-tools"
_SKILL_HANDLE = "input-skill"
_SKILL_OUTPUT_HANDLE = "output-tool"
_AGENT_KIND = "agent"
_MASTER_SKILL_TYPE = "masterSkill"
_MASTER_SKILL_LABEL = "Master Skill"
_MASTER_SKILL_OFFSET = {"x": -60.0, "y": 220.0}


class SkillConfig(TypedDict):
    enabled: bool
    instructions: str
    isCustomized: bool


SkillsConfig = Dict[str, SkillConfig]


def _is_agent_node(node_type: str) -> bool:
    """True iff the node's plugin class is registered as ``component_kind = 'agent'``."""
    cls = get_node_class(node_type)
    if cls is None:
        return False
    return getattr(cls, "component_kind", "") == _AGENT_KIND


def _toggle_skill(
    config: Optional[SkillsConfig],
    skill_name: str,
    enabled: bool,
) -> SkillsConfig:
    """Return a new skills_config with ``skill_name`` toggled.

    Preserves any existing ``instructions`` / ``isCustomized`` so a
    user-customised skill keeps its content when toggled. New entries
    default to empty instructions + ``isCustomized=False``; the
    backend ai handler then loads canonical SKILL.md content at
    execution time (see ``handlers/ai.py``
    ``_collect_agent_connections``).
    """
    base: SkillsConfig = dict(config or {})
    existing = base.get(skill_name, {})
    base[skill_name] = {
        "enabled": enabled,
        "instructions": existing.get("instructions", ""),
        "isCustomized": existing.get("isCustomized", False),
    }
    return base


def evaluate(
    *,
    action: Literal["connect", "disconnect"],
    source_type: str,
    target_type: str,
    target_handle: str,
    target_node_id: Optional[str] = None,
    master_skill_id: Optional[str] = None,
    master_skill_config: Optional[SkillsConfig] = None,
    master_skill_parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Any]]:
    """Decide what (if anything) to do for an edge connect/disconnect.

    Returns a workflow-ops batch ``{"operations": [...]}``.

    The three possible outcomes:

    * Empty batch -- the event is irrelevant (not a tool node, not the
      tools handle, not an agent target).
    * One ``set_node_parameters`` op -- a Master Skill is already
      wired; toggle the matching skill in its ``skills_config``. The op
      carries the Master Skill's whole saved row
      (``master_skill_parameters``) with only ``skills_config`` changed,
      because applying it replaces the row: an op holding
      ``skills_config`` alone would wipe ``skill_folder`` and every other
      setting. The saved row's ``skills_config`` wins over the
      client-sent ``master_skill_config`` when both are present.
    * One ``add_node`` + one ``add_edge`` op -- no Master Skill exists
      yet; spawn one, wire it into the agent's ``input-skill``, and
      seed it with the matching skill enabled.
    """
    if target_handle != _TOOL_HANDLE:
        return workflow_ops.empty()
    if not _is_agent_node(target_type):
        return workflow_ops.empty()
    skill = get_skill(source_type)
    if not skill:
        return workflow_ops.empty()

    enabled = action == "connect"

    if master_skill_id:
        row = dict(master_skill_parameters or {})
        current = row["skills_config"] if "skills_config" in row else master_skill_config
        new_config = _toggle_skill(current, skill, enabled)
        return {
            "operations": [
                workflow_ops.set_node_parameters(
                    master_skill_id,
                    {**row, "skills_config": new_config},
                ),
            ],
        }

    # No Master Skill wired yet. Auto-creation only on connect; a
    # disconnect with no MS in scope is a no-op.
    if not enabled or not target_node_id:
        return workflow_ops.empty()

    new_config = _toggle_skill(None, skill, True)
    client_ref = "auto_master_skill"
    return {
        "operations": [
            workflow_ops.add_node(
                client_ref,
                _MASTER_SKILL_TYPE,
                {"skills_config": new_config},
                label=_MASTER_SKILL_LABEL,
                position=workflow_ops.anchored(
                    target_node_id,
                    offset_x=_MASTER_SKILL_OFFSET["x"],
                    offset_y=_MASTER_SKILL_OFFSET["y"],
                ),
            ),
            workflow_ops.add_edge(
                {"client_ref": client_ref},
                target_node_id,
                source_handle=_SKILL_OUTPUT_HANDLE,
                target_handle=_SKILL_HANDLE,
            ),
        ],
    }

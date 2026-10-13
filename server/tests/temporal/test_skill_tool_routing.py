"""The Skill tool's activity runs where it is registered.

``agent.skill.invoke`` is a framework activity on the agent's own queue
(``collect_agent_activities``). The Skill tool has no plugin class, so its
frozen policy once recorded BaseNode's pool queue, ``machina-default``, whose
worker registers only plugin activities: every Skill call of a deployed run
failed with NotFoundError on all its attempts.
"""

import nodes  # noqa: F401 -- populate plugin registry
from services.node_registry import get_node_class
from services.temporal.agent_activities import _tool_activity_policy, collect_agent_activities
from services.temporal.agent_workflow import _frozen_tool_activity_options

DEPLOYED = {"execution_control_version": 1, "temporal_worker_pool_enabled": True}


def test_the_skill_tool_runs_on_the_agents_own_queue():
    options = _frozen_tool_activity_options(_tool_activity_policy(None), DEPLOYED)
    # No queue: Temporal schedules it on the workflow's own queue.
    assert options["task_queue"] is None
    names = {getattr(fn, "__temporal_activity_definition").name for fn in collect_agent_activities()}
    assert "agent.skill.invoke" in names


def test_a_plugin_tool_keeps_its_declared_queue():
    cls = get_node_class("taskManager")
    assert _frozen_tool_activity_options(_tool_activity_policy(cls), DEPLOYED)["task_queue"] == cls.task_queue

"""Shared pre-dispatch logic for every agent plugin.

Every agent (ai_agent, chat_agent, the 13 SpecializedAgentBase plugins: 11 specialized plus the 2 team leads)
shares the same 3-step preamble before calling its specific AIService
method:

1. :func:`services.plugin.edge_walker.collect_agent_connections` —
   gather memory / skills / tools / input / task data.
2. Task-context injection when a taskTrigger firing carries a
   completed / errored task. Tools are deliberately KEPT: the injected
   guidance tells a team lead to ``list_tasks`` / ``accept_task`` /
   ``reassign_task``, which requires the Task Manager tool. An earlier
   version stripped every tool here "so the agent reports instead of
   re-delegating" — that made the lead unable to accept or reassign
   anything, which read as the lead forgetting its plan.
3. Auto-prompt fallback when the ``prompt`` field is empty and a
   connected upstream node produced output.

For team-lead agents (``orchestrator_agent`` / ``ai_employee``),
teammate agents connected via ``input-teammates`` become delegation
tools (appended to ``tool_data``).

:func:`prepare_agent_call` returns the fully-prepared kwargs to pass
to ``ai_service.execute_agent(...)`` or ``ai_service.execute_chat_agent(...)``.
"""

from __future__ import annotations

from typing import Any, Dict

from core.logging import get_logger
from services.plugin.edge_walker import (
    collect_agent_connections,
    collect_teammate_connections,
    format_task_context,
)

logger = get_logger(__name__)

# Team-lead agent types where teammates become delegation tools.
TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})


async def prepare_agent_call(
    *,
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any],
    database: Any,
    log_prefix: str = "[Agent]",
) -> Dict[str, Any]:
    """Run the 3-step pre-dispatch flow and return a kwargs dict ready
    to splat into ``AIService.execute_agent`` or ``execute_chat_agent``.
    """
    # api_key is NOT a declared Params field on aiAgent/chatAgent/
    # specialized agents — credentials live in the credentials DB and
    # node_executor._inject_api_keys puts the resolved key into the
    # *raw* parameters dict before Pydantic validation strips it. Recover
    # it from context so ai_service.execute_[chat_]agent receives the
    # key it reads via ``flattened.get('api_key')``.
    raw_params = context.get("_raw_parameters") or {}
    if "api_key" in raw_params and "api_key" not in parameters:
        parameters = {**parameters, "api_key": raw_params["api_key"]}

    context.update({"node_id": node_id, "node_type": node_type})
    from services.employees.team_runtime import attach_employee_job, employee_job_parameters, employee_lead_prompt, employee_review_input
    await attach_employee_job(database, context, employee_review_input(context))
    connection_database = database
    if context.get("employee_job_id"):
        context["parameter_snapshot"] = await employee_job_parameters(database, context)
        from services.agent_bindings import ParameterSnapshotDatabase
        connection_database = ParameterSnapshotDatabase(database, context["parameter_snapshot"])
        if not context.get("parent_node_id") and node_id in context["parameter_snapshot"]:
            # NodeExecutor may have resolved a review prompt before its task
            # identified the job. Rebuild it against this job's frozen source.
            from services.parameter_resolver import ParameterResolver
            async def captured_output(session_id, source_id, output_name):
                return None
            resolver = ParameterResolver(connection_database, captured_output)
            frozen = await resolver.resolve(context["parameter_snapshot"][node_id], node_id,
                context.get("nodes") or [], context.get("edges") or [], context.get("session_id", "default"),
                run_outputs=context.get("outputs") or {})
            parameters = {**parameters, **frozen}
        parameters = {**parameters, "prompt": await employee_lead_prompt(database, context, parameters.get("prompt", ""))}

    memory_data, skill_data, tool_data, input_data, task_data = await collect_agent_connections(
        node_id,
        context,
        connection_database,
        log_prefix=log_prefix,
    )

    # Step 1: task-context injection. Tools are kept — the injected
    # guidance requires the Task Manager tool to act on the completion.
    if task_data:
        await attach_employee_job(database, context, task_data)
        from services.plugin.edge_walker import bind_employee_job_source
        await bind_employee_job_source(node_id, context, database, tool_data)
        task_context = format_task_context(task_data)
        original_prompt = parameters.get("prompt", "")
        parameters = {**parameters, "prompt": f"{task_context}\n\n{original_prompt}"}
        logger.info(
            f"{log_prefix} Task context injected for task_id={task_data.get('task_id')}",
        )

    # Step 2: auto-prompt fallback.
    if not parameters.get("prompt") and input_data:
        prompt = (
            (input_data.get("message") if isinstance(input_data, dict) else None)
            or (input_data.get("text") if isinstance(input_data, dict) else None)
            or (input_data.get("content") if isinstance(input_data, dict) else None)
            or str(input_data)
        )
        parameters = {**parameters, "prompt": prompt}
        shown = prompt[:100] if isinstance(prompt, str) and len(prompt) > 100 else prompt
        logger.info(f"{log_prefix} Auto-using input as prompt: {shown}...")

    # Step 3: team-lead delegation-tool injection.
    if node_type in TEAM_LEAD_TYPES:
        teammates = await collect_teammate_connections(node_id, context, connection_database)
        if teammates:
            tool_data = tool_data or []
            for tm in teammates:
                tool_data.append(
                    {
                        "node_id": tm["node_id"],
                        "node_type": tm["node_type"],
                        "label": tm["label"],
                        "parameters": tm.get("parameters", {}),
                        "child_tools": tm.get("child_tools", []),
                        "child_skills": tm.get("child_skills", []),
                        "capabilities": tm.get("capabilities", ""),
                        "delegate_tool_name": tm["delegate_tool_name"],
                    }
                )
            logger.info(f"[Teams] Added {len(teammates)} teammates as delegation tools")

    from services.status_broadcaster import get_status_broadcaster

    return {
        "parameters": parameters,
        "memory_data": memory_data,
        "skill_data": skill_data if skill_data else None,
        "tool_data": tool_data if tool_data else None,
        "broadcaster": get_status_broadcaster(),
        "workflow_id": context.get("workflow_id"),
        "context": context,
        "database": database,
    }

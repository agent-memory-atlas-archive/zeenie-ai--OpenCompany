"""Read-only, paginated original email access scoped by trusted runtime IDs."""
from __future__ import annotations

from typing import Any, Literal

from sqlmodel import select


async def authorize_employee_job_source(database: Any, context: dict):
    """Return the authorized job; reject missing, cross-job or stale task scope.

    Model arguments are deliberately absent. The caller ID comes from the
    common tool pipeline, while job/task IDs come from server admission.
    """
    from models.employees import EmployeeJob
    from models.database import TeamMember, TeamTask
    job_id = context.get("employee_job_id")
    caller = str(context.get("parent_node_id") or context.get("agent_node_id") or context.get("node_id") or "")
    workflow_id = str(context.get("workflow_id") or "")
    if not job_id or not caller or not workflow_id:
        raise PermissionError("Original email access requires an admitted employee job")
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, str(job_id))
        if job is None or job.workflow_id != workflow_id:
            raise PermissionError("The employee job does not belong to this workflow")
        plan = (job.source or {}).get("team_plan") or {}
        captured = {str(member.get("node_id") or "") for member in plan.get("members", [])}
        if plan.get("lead_node_id") != job.lead_node_id:
            raise PermissionError("The employee job has no captured team authority")
        if caller == job.lead_node_id:
            return job
        task_id = context.get("team_task_id")
        if not job.team_id or caller not in captured or not task_id:
            raise PermissionError("Original email access requires a captured teammate assignment")
        member = (await session.execute(select(TeamMember).where(TeamMember.team_id == job.team_id,
                        TeamMember.agent_node_id == caller))).scalar_one_or_none()
        task = await session.get(TeamTask, str(task_id))
        if (member is None or member.role != "teammate" or member.status == "offline"
            or task is None or task.team_id != job.team_id or task.workflow_id != workflow_id
            or task.execution_id not in {job.id, job.origin_execution_id} or task.created_by not in {job.lead_node_id, *captured}
            or task.assigned_to != caller or task.cancellation_requested or task.status == "cancelled"):
            raise PermissionError("The teammate is not assigned to this employee job")
        return job


async def read_employee_job_source(database: Any, context: dict, *,
        content: Literal["body_text", "original_body", "original_mission"], offset: int = 0, limit: int = 10000) -> dict:
    if content not in {"body_text", "original_body", "original_mission"}:
        raise ValueError("Unknown employee source content")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative character offset")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000 characters")
    job = await authorize_employee_job_source(database, context)
    source = job.source or {}
    metadata = source.get("email_input") or {}
    source_id = metadata.get("source_node_id")
    outputs = source.get("outputs") or {}
    if not source_id:
        ids = [node.get("id") for node in source.get("nodes", [])
               if node.get("type") == "googleGmailReceive" and node.get("id") in outputs]
        if len(ids) != 1:
            raise ValueError("This job does not have an unambiguous captured Gmail source")
        source_id = ids[0]
    email = outputs.get(source_id)
    if not isinstance(email, dict):
        raise ValueError("The captured Gmail source is unavailable")
    if content == "original_mission":
        text = str(metadata.get("original_mission", job.mission))
    elif content == "original_body":
        text = str(email.get("body") or "")
    else:
        from services.employees.email_input import readable_email_body
        text = metadata.get("body_text")
        if not isinstance(text, str):
            text = readable_email_body(email)
    # Requests beyond EOF return an empty page and a stable EOF cursor.
    start = min(offset, len(text))
    end = min(start + limit, len(text))
    return {"content": content, "text": text[start:end], "offset": start,
            "next_offset": end, "total_chars": len(text), "complete": end == len(text),
            **{key: email.get(key) for key in ("message_id", "thread_id", "from", "to", "subject")}}

"""Authoritative admission for direct Workspace tasks. Never accepts client graphs."""

from __future__ import annotations
import hashlib
import json
from typing import Any
from uuid import UUID


def invocation_id(principal: str, workflow_id: str, node_id: str, submission_id: str) -> str:
    submission_id = str(UUID(submission_id))
    identity = json.dumps([principal, workflow_id, node_id, submission_id], separators=(",", ":"))
    return "node-invoke-" + hashlib.sha256(identity.encode()).hexdigest()


def temporal_client() -> Any:
    from core.container import container
    from services.plugin import NodeUserError

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        raise NodeUserError("Workflow engine is not ready. Try again after startup completes.")
    return wrapper.client


async def submit(principal: str, workflow_id: str, node_id: str, prompt: str, submission_id: str) -> dict:
    from core.config import Settings
    from services.authz.workflow_node import resolve_workflow_node
    from services.node_registry import get_node_class
    from services.plugin import NodeUserError
    from services.plugin.deps import get_database
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    saved, graph, node = await resolve_workflow_node(principal, workflow_id, node_id)
    cls = get_node_class(node["type"])
    if cls is None or not getattr(cls, "workspace_task", False):
        raise NodeUserError("This node does not accept direct Workspace tasks")
    if (node.get("data") or {}).get("disabled"):
        raise NodeUserError("Enable the node before submitting a task")
    if not prompt.strip() or len(prompt) > 20000:
        raise NodeUserError("Task must contain between 1 and 20000 characters")
    run_id = invocation_id(principal, workflow_id, node_id, submission_id)
    fingerprint = hashlib.sha256(prompt.encode()).hexdigest()
    params = {**(await get_database().get_node_parameters(node_id) or {}), "prompt": prompt}
    settings = Settings()
    context = {
        "node_id": node_id,
        "node_type": node["type"],
        "node_data": params,
        "workflow_id": workflow_id,
        "workflow_slug": getattr(saved, "name", "workflow"),
        "execution_id": run_id,
        "session_id": run_id,
        "user_id": principal,
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
        "generation": 0,
        "graphVersion": graph.get("graphVersion", 1),
        "inputs": {},
    }
    payload = {
        "principal": principal,
        "workflow_id": workflow_id,
        "node_id": node_id,
        "fingerprint": fingerprint,
        "activity": f"node.{cls.type}.v{cls.version}",
        "task_queue": cls.task_queue if settings.temporal_worker_pool_enabled else settings.temporal_task_queue,
        "timeout_s": int(cls.start_to_close_timeout.total_seconds()),
        "context": context,
    }
    client = temporal_client()
    try:
        await client.start_workflow(
            NodeInvocationWorkflow.run,
            payload,
            id=run_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        existing = await client.get_workflow_handle(run_id).query(NodeInvocationWorkflow.describe)
        if existing["fingerprint"] != fingerprint:
            raise NodeUserError("Submission ID was already used for a different task") from None
    return {"run_id": run_id, "submission_id": submission_id, "status": "accepted"}


async def status(principal: str, workflow_id: str, node_id: str, submission_id: str, *, cancel: bool = False) -> dict:
    from services.authz.workflow_node import resolve_workflow_node
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from services.plugin import NodeUserError
    from temporalio.service import RPCError, RPCStatusCode

    await resolve_workflow_node(principal, workflow_id, node_id)
    handle = temporal_client().get_workflow_handle(invocation_id(principal, workflow_id, node_id, submission_id))
    try:
        if cancel:
            await handle.cancel()
        result = await handle.query(NodeInvocationWorkflow.describe)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise NodeUserError("This Workspace task was not found") from None
        raise NodeUserError("Workflow engine is temporarily unavailable") from None
    if result.get("status") in ("completed", "failed"):
        try:
            result["result"] = await handle.result()
        except Exception:
            result["status"] = "failed"
    return result

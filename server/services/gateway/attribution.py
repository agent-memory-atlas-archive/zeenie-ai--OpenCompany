"""What a call the OpenCompany Gateway serves says about itself.

Three LiteLLM headers put each call on its spend row: the session (the chat
run, else the root execution, else the execution, else the Temporal
workflow), a few low-cardinality tags, and every id as metadata. The ids come
from the log context (``core.logging.log_context``), bound by
``BaseNode.execute``, the Temporal activity interceptor and the agent
activities, so whatever a log line carries, the spend row carries. Only the
allowlisted ids below are sent; nothing else from the log context leaves the
install.

Inside a Temporal activity a call also opts in to the gateway's response
cache under a namespace that stays the same across that activity's retries,
so a retried call replays at $0 instead of being billed twice.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

import structlog

from core.encryption import fingerprint_credential

#: First present id names the session.
SESSION_KEYS = ("chat_run_id", "root_execution_id", "execution_id", "temporal_workflow_id")
#: Ids sent as spend-log metadata. Never extend this with free text.
METADATA_KEYS = (
    "chat_run_id",
    "root_execution_id",
    "execution_id",
    "workflow_id",
    "node_id",
    "node_type",
    "employee_job_id",
    "user_id",
    "temporal_workflow_id",
    "temporal_run_id",
    "temporal_activity_id",
    "temporal_attempt",
)
#: How long a replayable response stays cached (LiteLLM cache_params ttl).
REPLAY_TTL_SECONDS = 172_800


def _ids() -> Dict[str, str]:
    bound = structlog.contextvars.get_contextvars()
    return {key: str(bound[key]) for key in METADATA_KEYS if bound.get(key) not in (None, "")}


def request_headers() -> Dict[str, str]:
    """The attribution headers for the current log context."""
    ids = _ids()
    headers: Dict[str, str] = {}
    session = next((ids[key] for key in SESSION_KEYS if key in ids), None)
    if session:
        headers["x-litellm-session-id"] = session
    source = "chat" if "chat_run_id" in ids else ("workflow" if "workflow_id" in ids else "direct")
    tags = [f"source:{source}"]
    if "workflow_id" in ids:
        tags.append(f"workflow:{ids['workflow_id']}")
    if "user_id" in ids:
        tags.append(f"user:{ids['user_id']}")
    headers["x-litellm-tags"] = ",".join(tags)
    if ids:
        headers["x-litellm-spend-logs-metadata"] = json.dumps(ids, sort_keys=True, separators=(",", ":"))
    return headers


def replay_cache(api_key: str) -> Optional[Dict[str, Any]]:
    """The replay-cache request body inside a Temporal activity, else ``None``.

    The namespace is the gateway key's fingerprint plus the Temporal workflow,
    run and activity ids: an activity's retries share it, and a re-run of the
    same workflow id (a trigger re-firing a failed event) gets a new run id,
    so it never replays another run's answer.
    """
    bound = structlog.contextvars.get_contextvars()
    scope = [bound.get(key) for key in ("temporal_workflow_id", "temporal_run_id", "temporal_activity_id")]
    if not all(scope):
        return None
    digest = hashlib.sha256("\x1f".join(str(part) for part in scope).encode("utf-8")).hexdigest()[:32]
    namespace = f"oc1-{fingerprint_credential(api_key)[:16]}-{digest}"
    return {"cache": {"use-cache": True, "namespace": namespace, "ttl": REPLAY_TTL_SECONDS}}

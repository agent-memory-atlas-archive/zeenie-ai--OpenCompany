"""Safe error metadata shared with the isolated engine (standard library only)."""

import traceback
from pathlib import Path


def describe_error(exc: BaseException, stage: str) -> dict:
    kind = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        status = None
    provider_status = getattr(exc, "status", None)
    if provider_status not in {"INVALID_ARGUMENT", "NOT_FOUND", "UNAUTHENTICATED", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "FAILED_PRECONDITION"}:
        provider_status = None
    code, message = "engine_failed", f"Android engine failed during {stage} ({kind}). See Android events in Terminal."
    if kind == "HTTPError":
        code, message = "device_connection_failed", "Android device control changed or disconnected. Check the phone connection in Workspace."
    elif status == 401 or kind == "AuthenticationError":
        code, message = "model_authentication_failed", "The model provider rejected its credentials. Reconnect the provider in Settings."
    elif status == 403:
        code, message = "model_permission_denied", "The model provider denied access. Check that this API key/project can use the selected model."
    elif status == 404:
        code, message = "model_not_found", "The selected model was not found by the provider. Choose an available model in the global selector or phone settings."
    elif status == 429 or kind == "RateLimitError":
        code, message = "model_rate_limited", "The model provider's quota or rate limit was reached. Check billing/quota or try again later."
    elif status == 400 and any(term in str(exc).lower() for term in ("api key not valid", "api_key_invalid", "api_key_expired")):
        code, message = "model_authentication_failed", "The model provider rejected its API key. Reconnect the provider in Settings."
    elif status == 400:
        code, message = "model_request_rejected", "The model provider rejected the request. Check that the selected model supports images and tool/structured responses."
    elif status and status >= 500:
        code, message = "model_service_unavailable", "The model provider is unavailable. Try again later."
    elif kind in {"TimeoutError", "APITimeoutError", "ReadTimeout"}:
        code, message = "task_timeout", "The Android task or model request timed out. Check connectivity and the task time budget."
    elif kind == "GraphRecursionError":
        code, message = "step_budget_exhausted", "The Android task exhausted its step budget."
    elif kind in {"APIConnectionError", "ConnectError"}:
        code, message = "model_connection_failed", "Could not reach the model provider. Check network and proxy settings."
    # Never serialize str(exc), response bodies, request headers or locals:
    # provider exceptions can contain API keys, prompts and screenshots.
    return {"error": message + (f" (HTTP {status})" if status else ""), "code": code,
            "error_type": kind, "stage": stage, "http_status": status, "provider_status": provider_status,
            "trace": [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in traceback.extract_tb(exc.__traceback__)[-8:]]}

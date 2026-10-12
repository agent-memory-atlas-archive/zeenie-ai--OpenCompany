"""What the user is told when a call the OpenCompany Gateway served fails.

The category texts name the vendor and point at its billing or key ("OpenAI
blocked this request because a spending limit ... was exhausted"), which is
the wrong account for a call billed to an OpenCompany allowance and made
with the gateway key. ChatUnifier passes every failure of a gateway call
through :func:`explain`; the category, and so retry and pause behaviour,
stays as classified. Embeddings, speech and vision, which call the gateway
from their own clients, word their failures with :func:`describe`.
"""

from __future__ import annotations

from typing import Mapping, Optional

import httpx

from services.llm.protocol import LLMError, LLMErrorCategory, error_type

_ALLOWANCE = "Your OpenCompany allowance for this month is used up."
_UNPRICED = "This model isn't available on the OpenCompany Gateway yet."
_KEY_REJECTED = "The OpenCompany Gateway rejected the gateway key."
_DENIED = "The OpenCompany Gateway denied this request."
_RATE_LIMITED = "The OpenCompany Gateway is rate-limiting requests. Retry after a short delay."
_UNREACHABLE = "Could not reach the OpenCompany Gateway."
_UNAVAILABLE = "The OpenCompany Gateway is temporarily unavailable."
_RECONNECT = "Reconnect the Gateway connector in Settings > Connectors"
_OWN_KEY = "add your own key for this provider in Settings > Connectors"
_RAISE = f"Ask your OpenCompany admin to raise it, or {_OWN_KEY}"
_OTHER_MODEL = f"Choose another model, or {_OWN_KEY}."


def explain(error: LLMError, exc: Optional[BaseException]) -> LLMError:
    """Set gateway wording on ``error`` (in place) and return it."""
    kind = error_type(exc) if exc is not None else None
    if error.category is LLMErrorCategory.BILLING:
        error.public_message = _ALLOWANCE
        error.public_hint = f"{_RAISE}, then resume."
    elif kind == "model_cost_map_missing":
        error.public_message = _UNPRICED
        error.public_hint = _OTHER_MODEL
    elif error.category is LLMErrorCategory.AUTHENTICATION:
        error.public_message = _KEY_REJECTED
        error.public_hint = f"{_RECONNECT}, then resume."
    elif error.category is LLMErrorCategory.PERMISSION:
        error.public_message = _DENIED
        error.public_hint = f"{_RECONNECT}, then resume."
    elif error.category is LLMErrorCategory.RATE_LIMIT:
        error.public_message = _RATE_LIMITED
    elif error.category in (LLMErrorCategory.CONNECTION, LLMErrorCategory.TIMEOUT):
        error.public_message = _UNREACHABLE
    elif error.category is LLMErrorCategory.SERVER:
        error.public_message = _UNAVAILABLE
    return error


def describe(exc: BaseException) -> Optional[str]:
    """The user-facing text for a failed gateway call made outside
    ChatUnifier, or ``None`` to keep the caller's own wording.

    Reads the error type LiteLLM puts in the body (``budget_exceeded``,
    ``model_cost_map_missing``) from an SDK error or an ``httpx`` one, then
    the HTTP status."""
    kind = _body_type(exc)
    status = _status(exc)
    if kind == "budget_exceeded":
        return f"{_ALLOWANCE} {_RAISE}."
    if kind == "model_cost_map_missing":
        return f"{_UNPRICED} {_OTHER_MODEL}"
    if status == 401:
        return f"{_KEY_REJECTED} {_RECONNECT}."
    if status == 403:
        return f"{_DENIED} {_RECONNECT}."
    if status == 429:
        return _RATE_LIMITED
    if status is not None and status >= 500:
        return _UNAVAILABLE
    if status is None and _transport_failure(exc):
        return _UNREACHABLE
    return None


def _body_type(exc: BaseException) -> Optional[str]:
    kind = error_type(exc)
    if kind:
        return kind
    try:
        body = exc.response.json()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no response, or not JSON
        return None
    error = body.get("error") if isinstance(body, Mapping) else None
    value = error.get("type") if isinstance(error, Mapping) else None
    return str(value) if value else None


def _status(exc: BaseException) -> Optional[int]:
    response = getattr(exc, "response", None)
    # status_code: OpenAI and Anthropic SDK errors; response: httpx errors;
    # code: google-genai errors.
    for value in (getattr(exc, "status_code", None), getattr(response, "status_code", None), getattr(exc, "code", None)):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _transport_failure(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TransportError, OSError, TimeoutError)) or type(exc).__name__ in (
        "APIConnectionError",
        "APITimeoutError",
    )

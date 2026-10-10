"""What the user is told when a call the OpenCompany Gateway served fails.

The category texts name the vendor and point at its billing or key ("OpenAI
blocked this request because a spending limit ... was exhausted"), which is
the wrong account for a call billed to an OpenCompany allowance and made
with the gateway key. ChatUnifier passes every failure of a gateway call
through :func:`explain`; the category, and so retry and pause behaviour,
stays as classified.
"""

from __future__ import annotations

from typing import Optional

from services.llm.protocol import LLMError, LLMErrorCategory, error_type

_RECONNECT = "Reconnect the OpenCompany Gateway in Settings > Connectors, then resume."
_OWN_KEY = "add your own key for this provider in Settings > Connectors"


def explain(error: LLMError, exc: Optional[BaseException]) -> LLMError:
    """Set gateway wording on ``error`` (in place) and return it."""
    kind = error_type(exc) if exc is not None else None
    if error.category is LLMErrorCategory.BILLING:
        error.public_message = "Your OpenCompany allowance for this month is used up."
        error.public_hint = f"Ask your OpenCompany admin to raise it, or {_OWN_KEY}, then resume."
    elif kind == "model_cost_map_missing":
        error.public_message = "This model isn't available on the OpenCompany Gateway yet."
        error.public_hint = f"Choose another model, or {_OWN_KEY}."
    elif error.category is LLMErrorCategory.AUTHENTICATION:
        error.public_message = "The OpenCompany Gateway rejected the gateway key."
        error.public_hint = _RECONNECT
    elif error.category is LLMErrorCategory.PERMISSION:
        error.public_message = "The OpenCompany Gateway denied this request."
        error.public_hint = _RECONNECT
    elif error.category is LLMErrorCategory.RATE_LIMIT:
        error.public_message = "The OpenCompany Gateway is rate-limiting requests. Retry after a short delay."
    elif error.category in (LLMErrorCategory.CONNECTION, LLMErrorCategory.TIMEOUT):
        error.public_message = "Could not reach the OpenCompany Gateway."
    elif error.category is LLMErrorCategory.SERVER:
        error.public_message = "The OpenCompany Gateway is temporarily unavailable."
    return error

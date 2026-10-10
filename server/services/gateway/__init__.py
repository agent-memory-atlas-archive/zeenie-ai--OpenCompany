"""OpenCompany Gateway: LLM calls on a hosted, capped allowance.

The gateway itself is the stock LiteLLM Proxy in ``gateway/``
(docs-internal/llm_gateway.md). This package is OpenCompany's side:

- ``routing``: which calls go to the gateway (own keys first) and where.
- ``attribution``: the headers that put each call on its spend row.
- ``errors``: what the user is told when a gateway call fails.
- ``credential`` / ``handlers``: the Connectors card for the gateway key.

Side-effect import (server/main.py) registers the credential and the
``gateway_status`` handler.
"""

from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from . import credential as _credential  # noqa: F401  (registers OpenCompanyGatewayCredential)
from .handlers import WS_HANDLERS as _WS_HANDLERS

_register_ws_handlers(_WS_HANDLERS)

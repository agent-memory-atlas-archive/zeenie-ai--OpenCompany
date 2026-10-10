"""WebSocket handlers for the OpenCompany Gateway's Connectors card.

``gateway_status`` is the card's ``ws.status`` hook
(server/config/credential_providers.json): the allowance the stored gateway
key draws on, as one ``summary`` line the card shows.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from core.logging import get_logger
from services.ws_handler_registry import ws_handler

from .allowance import parse_user_info, user_info
from .routing import GATEWAY_CREDENTIAL_ID

logger = get_logger(__name__)


@ws_handler()
async def handle_gateway_status(data: Dict[str, Any], websocket: Any) -> Dict[str, Any]:
    from core.container import container

    api_key = await container.auth_service().get_api_key(GATEWAY_CREDENTIAL_ID)
    if not api_key:
        return {"connected": False}
    try:
        response = await user_info(container.settings().gateway_url, api_key)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Gateway allowance read failed", error=type(exc).__name__)
        return {"connected": True, "summary": "Couldn't read the allowance from the OpenCompany Gateway."}
    allowance = parse_user_info(response.json())
    return {
        "connected": True,
        "summary": allowance.summary,
        "account_label": allowance.account,
        "spend": allowance.spend,
        "max_budget": allowance.max_budget,
        "budget_reset_at": allowance.reset_at.isoformat() if allowance.reset_at else None,
    }


WS_HANDLERS = {
    "gateway_status": handle_gateway_status,
}

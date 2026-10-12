"""The gateway key as a Connectors credential.

A standard API-key connector: the key is stored install-wide like every
other key (``Credential.validate`` in services/plugin/credential.py). The
probe reads the key's allowance from the gateway, which also tells a
Default key (needed to show the allowance) from an LLM-API one.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.plugin.credential import ApiKeyCredential, ProbeResult

from .allowance import parse_user_info, user_info
from .routing import GATEWAY_CREDENTIAL_ID


class OpenCompanyGatewayCredential(ApiKeyCredential):
    id = GATEWAY_CREDENTIAL_ID
    display_name = "OpenCompany Gateway"
    category = "AI"
    key_location = "bearer"
    docs_url = "https://docs.litellm.ai/docs/proxy/virtual_keys"

    @classmethod
    async def catalogue_extras(cls, *, principal: Optional[str] = None) -> Optional[Dict[str, Any]]:
        # No 1Password source: the router reads the key on every LLM call
        # (services/gateway/routing.py), which a 1Password lookup can't
        # serve, so a bound key would show as connected and never be used.
        return {"sourceSupported": False}

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        from core.container import container

        response = await user_info(container.settings().gateway_url, api_key)
        if response.status_code == 403:
            return ProbeResult(
                valid=False,
                message=(
                    "This key can't read its allowance. In the OpenCompany Gateway console, "
                    "create a key of type Default and paste that one."
                ),
            )
        # 401 and 5xx: Credential.validate words them (classify_credential_error).
        response.raise_for_status()
        return ProbeResult(valid=True, message=f"Connected: {parse_user_info(response.json()).summary}.")

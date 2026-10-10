"""Which LLM calls go through the OpenCompany Gateway, and where to.

Own keys first: a provider this install has a key for is called directly, as
always. Otherwise, when the gateway is enabled and connected and serves the
provider, ChatUnifier sends the call to the gateway with the install's
gateway key. The key never leaves this module and ChatUnifier: it is not
injected into node parameters, Temporal payloads or requests to a vendor.

Which providers the gateway serves, and how it names their models, is data:
the ``"gateway"`` block of each served provider in
``server/config/llm_defaults.json``. OpenAI-compatible providers use the
gateway's ``/v1`` routes with a LiteLLM provider prefix on the model name
(``groq/...``); Anthropic and Gemini use the gateway's native routes with
bare model ids. The same route serves a provider's embeddings, speech and
vision calls, which go out from their own SDK clients
(:func:`key_or_route`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Tuple

from services.llm.config import split_provider_ref

#: Catalogue entry and credential id of the gateway key (Connectors).
GATEWAY_CREDENTIAL_ID = "opencompany_gateway"


@dataclass(frozen=True)
class GatewayRoute:
    """Where one LLM call goes when the gateway serves it."""

    api_key: str
    base_url: str
    model_prefix: str


class GatewayRouter:
    """Decides, per provider, whether the gateway serves a call."""

    def __init__(self, *, settings: Any, auth_service: Any, defaults: Mapping[str, Any]) -> None:
        self._settings = settings
        self._auth = auth_service
        self._defaults = defaults

    def enabled(self) -> bool:
        # ``is True``: under test the container's Settings is a MagicMock,
        # whose attributes are all truthy. Distributed mode keeps credentials
        # in 1Password only, which the gateway key doesn't support yet.
        return (
            getattr(self._settings, "gateway_enabled", False) is True
            and getattr(self._settings, "distributed_mode", False) is not True
        )

    def _block(self, provider: str) -> Optional[Mapping[str, Any]]:
        providers = self._defaults.get("providers") or {}
        config = providers.get(split_provider_ref(provider)[0]) or {}
        block = config.get("gateway") if isinstance(config, Mapping) else None
        return block if isinstance(block, Mapping) else None

    def served_providers(self) -> List[str]:
        """Provider ids the gateway serves (a ``"gateway"`` block in llm_defaults.json)."""
        providers = self._defaults.get("providers") or {}
        return [name for name in providers if self._block(name) is not None]

    async def route(self, provider: str) -> Optional[GatewayRoute]:
        """The route for a call to ``provider`` with no key of the install's own."""
        if not self.enabled():
            return None
        block = self._block(provider)
        if block is None:
            return None
        api_key = await self._auth.get_api_key(GATEWAY_CREDENTIAL_ID)
        if not api_key:
            return None
        base_url = str(self._settings.gateway_url).rstrip("/") + str(block.get("base_path") or "")
        return GatewayRoute(api_key=api_key, base_url=base_url, model_prefix=str(block.get("model_prefix") or ""))

    async def covers(self, provider: str) -> bool:
        """Whether a call to ``provider`` can run on the gateway: what a "no key
        configured" check asks before refusing."""
        return await self.route(provider) is not None


async def gateway_route(provider: str) -> Optional[GatewayRoute]:
    """:meth:`GatewayRouter.route` on the container's router, for call sites
    outside the DI graph. Under test the container is a MagicMock (whose
    router would claim to cover everything) or a stub without a router, so
    only a real :class:`GatewayRouter` is asked."""
    from core.container import container

    factory = getattr(container, "gateway_router", None)
    router = factory() if callable(factory) else None
    return await router.route(provider) if isinstance(router, GatewayRouter) else None


async def gateway_covers(provider: str) -> bool:
    """:meth:`GatewayRouter.covers` outside the DI graph (see :func:`gateway_route`)."""
    return await gateway_route(provider) is not None


async def key_or_route(ctx: Any, credential_id: str, provider: str) -> Tuple[str, Optional[GatewayRoute]]:
    """A plugin node's key: the install's own for ``credential_id``, else the
    gateway's for ``provider``, with its route (``None`` for an own key).

    The own key is read through ``ctx.connection``, so with neither key the
    node still raises the annotated missing-key error that points the
    Credentials modal at the provider."""
    try:
        async with ctx.connection(credential_id) as conn:
            secrets = await conn.credentials()
    except PermissionError as exc:
        route = await gateway_route(provider) if getattr(exc, "reason", None) == "missing" else None
        if route is None:
            raise
        return route.api_key, route
    return str(secrets.get("api_key") or ""), None

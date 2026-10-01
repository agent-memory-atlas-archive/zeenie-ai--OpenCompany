"""Plugins for the 'payments' palette group — Stripe.

Self-contained Wave 12 plugin. All boilerplate (lifecycle WS handlers,
status refresh, signature verification, CLI subprocess) lives in
``services.events``. This package contributes only the Stripe-specific
shapes: command builder, secret-capture regex, the event shape and its
CloudEvents type, the event-type and livemode filter, the daemon start on
deploy, and the credential class.
"""

from __future__ import annotations

from services.deployment.canary_registry import register_canary_trigger_type
from services.events import make_status_refresh, register_webhook_source
from services.node_output_schemas import register_output_schema
from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import register_ws_handlers

from ._credentials import StripeCredential
from ._events import STRIPE_EVENT_RECEIVED_TYPE
from ._handlers import WS_HANDLERS
from ._source import (
    StripeListenSource,
    StripeWebhookSource,
    get_listen_source,
    get_webhook_source,
)

from .stripe_action import StripeActionNode, StripeActionOutput
from .stripe_receive import StripeReceiveNode, StripeReceiveOutput


register_ws_handlers(WS_HANDLERS)
register_webhook_source(get_webhook_source())
# Opt the trigger into the Temporal listener path. The second argument must
# equal the type on every envelope the source emits: it becomes the EventType
# the Visibility query matches, and a mismatch is silent.
register_canary_trigger_type(StripeReceiveNode.type, STRIPE_EVENT_RECEIVED_TYPE)
register_service_refresh(
    make_status_refresh(
        get_listen_source(),
        status_key="stripe",
        broadcast_type="stripe_status",
    )
)
register_output_schema("stripeReceive", StripeReceiveOutput)
register_output_schema("stripeAction", StripeActionOutput)


__all__ = [
    "StripeCredential",
    "StripeListenSource",
    "StripeWebhookSource",
    "WS_HANDLERS",
    "get_listen_source",
    "get_webhook_source",
]

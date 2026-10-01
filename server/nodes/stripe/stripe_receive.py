"""Stripe Receive — fires when StripeWebhookSource accepts a verified
forwarded event."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.events import BaseTriggerParams, WebhookTriggerNode, WorkflowEvent
from services.events.envelope import event_type_matches

from ._credentials import StripeCredential
from ._events import STRIPE_EVENT_RECEIVED_TYPE
from ._source import StripeWebhookSource

logger = get_logger(__name__)


_PREFIX = "stripe."

# asyncio keeps only weak references to tasks, so a fire-and-forget daemon
# start needs a strong one until it finishes.
_background_tasks: Set[asyncio.Task] = set()


class StripeReceiveParams(BaseTriggerParams):
    livemode_filter: Literal["all", "test", "live"] = Field(
        default="all",
        description="Filter by livemode flag on the event.",
    )


class StripeReceiveOutput(BaseModel):
    event_id: Optional[str] = None
    event_type: Optional[str] = None
    created: Optional[int] = None
    livemode: Optional[bool] = None
    api_version: Optional[str] = None
    request_id: Optional[str] = None
    account: Optional[str] = None
    data: Optional[dict] = None

    model_config = ConfigDict(extra="allow")


async def _start_listen_daemon() -> Optional[str]:
    """Start ``stripe listen`` unless it is already running.

    Returns an error message, or ``None`` once the daemon runs. Stripe events
    reach ``/webhook/stripe`` only while it does.
    """
    from ._source import get_listen_source

    source = get_listen_source()
    if source._started:
        return None
    if not await source.has_credential():
        return "Stripe not connected. Log in with Stripe in Credentials."
    result = await source.start()
    if not (result or {}).get("success", False):
        error = (result or {}).get("error") or "unknown error"
        return f"Stripe daemon failed to start: {error}"
    return None


async def _start_listen_daemon_for_deployment(node_id: str, workflow_id: str) -> None:
    try:
        error = await _start_listen_daemon()
    except Exception:
        logger.warning(
            "[Stripe] could not start the listen daemon for a deployed stripeReceive",
            node_id=node_id,
            workflow_id=workflow_id,
            exc_info=True,
        )
        return
    if error:
        # Armed anyway: logging in with Stripe starts the daemon, and events
        # flow from then on.
        logger.warning(
            "[Stripe] deployed stripeReceive is waiting: %s",
            error,
            node_id=node_id,
            workflow_id=workflow_id,
        )


class StripeReceiveNode(WebhookTriggerNode):
    type = "stripeReceive"
    display_name = "Stripe Receive"
    subtitle = "Webhook Event"
    group = ("payments", "trigger")
    description = "Trigger workflow when Stripe webhook event arrives"
    component_kind = "trigger"
    handles = ({"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},)
    credentials = (StripeCredential,)
    webhook_source = StripeWebhookSource
    # The canvas Run's waiter key, and the type the source dispatches. Left
    # unset, the base class would derive the source's own type
    # ("stripe.webhook"), which no envelope carries, so no Run ever resolved.
    event_type = STRIPE_EVENT_RECEIVED_TYPE
    event_type_prefix = _PREFIX
    Params = StripeReceiveParams
    Output = StripeReceiveOutput

    def build_filter(self, params: StripeReceiveParams) -> Callable[[Any], bool]:
        """Match on the shaped event the source emits.

        Both callers hand the predicate the envelope's ``data``: the event
        waiter on a canvas Run (and the Temporal-off deploy path), and
        ``evaluate_trigger_filter_activity`` on a deployed Temporal listener.
        The base filter rebuilds an envelope from what it receives, which
        raised on every Stripe event; deployed, that failed open and ignored
        both filters. Every Stripe event shares one CloudEvents type, so the
        Stripe type is read from ``data["event_type"]``. A pattern may keep the
        ``stripe.`` prefix the node used to require.
        """
        pattern = (params.event_type_filter or "all").strip()
        if pattern.startswith(_PREFIX):
            pattern = pattern[len(_PREFIX) :]
        livemode = params.livemode_filter

        def matches(event: Any) -> bool:
            data = event.data if isinstance(event, WorkflowEvent) else event
            if not isinstance(data, dict):
                return False
            if not event_type_matches(str(data.get("event_type") or ""), pattern):
                return False
            if livemode != "all" and bool(data.get("livemode")) is not (livemode == "live"):
                return False
            return True

        return matches

    async def _check_precondition(self) -> Optional[str]:
        # A canvas Run is a demand signal, like deploying: start the listen
        # daemon here instead of relying on a credential-triggered auto-start
        # at boot (the status refresh is a passive probe).
        return await _start_listen_daemon()

    @classmethod
    async def prepare_deployment(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        parameters: Dict[str, Any],
    ) -> None:
        """Start ``stripe listen`` for a deployed trigger.

        Nothing else starts the daemon after a backend restart, so without
        this a deployed trigger would stop receiving events at the first
        restart. Run in the background: a first-use CLI download must not hold
        up Start or the boot re-arm. When Stripe is not logged in the trigger
        stays armed, and logging in starts the daemon.
        """
        task = asyncio.create_task(_start_listen_daemon_for_deployment(node_id, workflow_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    def shape_output(self, event: WorkflowEvent) -> Dict:
        """Unwrap the shaped event, which is what the deployed path emits.

        The source already shaped it (``shape_stripe_event``), and a deployed
        trigger hands downstream nodes ``event.data`` verbatim, so unwrapping
        here keeps Run and deploy on the same fields.
        """
        return event.data if isinstance(event.data, dict) else {}

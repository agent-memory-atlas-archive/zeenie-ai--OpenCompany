"""The monthly allowance a gateway key draws on, read from the gateway.

LiteLLM's ``GET /user/info`` with the key itself (no ``user_id``) returns
its owner's spend, limit and reset time. Only Default-type keys may call it;
LLM-API keys get 401/403 (gateway/tests pins both).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

import httpx

TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Allowance:
    spend: float
    max_budget: Optional[float]
    reset_at: Optional[datetime]
    account: Optional[str]

    @property
    def summary(self) -> str:
        """``$12.40 of $50.00 used · resets Nov 1``."""
        used = f"${self.spend:,.2f}"
        text = f"{used} of ${self.max_budget:,.2f} used" if self.max_budget is not None else f"{used} used"
        if self.reset_at is not None:
            text += f" · resets {self.reset_at.strftime('%b')} {self.reset_at.day}"
        return text


def _reset_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_user_info(body: Mapping[str, Any]) -> Allowance:
    info = body.get("user_info") if isinstance(body.get("user_info"), Mapping) else {}
    max_budget = info.get("max_budget")
    return Allowance(
        spend=float(info.get("spend") or 0.0),
        max_budget=float(max_budget) if max_budget is not None else None,
        reset_at=_reset_time(info.get("budget_reset_at")),
        account=info.get("user_email") or body.get("user_id") or info.get("user_id"),
    )


async def user_info(gateway_url: str, api_key: str) -> httpx.Response:
    """``GET /user/info`` with ``api_key``; the caller reads the status."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        return await client.get(
            f"{gateway_url.rstrip('/')}/user/info",
            headers={"Authorization": f"Bearer {api_key}"},
        )

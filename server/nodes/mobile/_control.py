"""Device-wide arbitration, independent of transport and automation engine."""

from __future__ import annotations

import asyncio
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class MobileError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Lease:
    owner: str
    epoch: int


class DeviceControl:
    """Revocation happens before draining. Cancelled callers cannot release an active write."""

    def __init__(self) -> None:
        # A delayed browser request from an earlier server lifetime must not
        # regain a valid epoch after restart. Stay within JS safe integers.
        self.epoch = secrets.randbits(48)
        self.owner: str | None = None
        self.state = "idle"
        self.lock = asyncio.Lock()
        self.results: OrderedDict[tuple[int, str], Any] = OrderedDict()

    def snapshot(self) -> dict:
        return {"controller": self.owner, "epoch": self.epoch, "control_state": self.state}

    async def claim(self, owner: str, *, takeover: bool = False) -> Lease:
        if self.state == "recovering":
            raise MobileError("recovering", "Restart the device connection before taking control.")
        if self.owner == owner:
            return Lease(owner, self.epoch)
        if self.owner is not None and not takeover:
            raise MobileError("device_busy", "The shared device is in use.")
        self.epoch += 1
        epoch = self.epoch
        self.owner = None
        self.state = "draining"
        async with self.lock:
            if epoch != self.epoch:
                raise MobileError("superseded", "Another control request superseded this request.")
            if self.state == "recovering":
                raise MobileError("recovering", "A device action has an uncertain outcome.")
            self.owner, self.state = owner, "human" if owner.startswith("viewer:") else "agent"
            return Lease(owner, epoch)

    def revoke(self, owner: str | None = None) -> None:
        if owner is None or self.owner == owner:
            self.epoch += 1
            self.owner = None
            if self.state != "recovering":
                self.state = "idle"

    async def perform(self, lease: Lease, operation_id: str, action: Callable[[], Awaitable[Any]], *, cache_result: bool = True) -> Any:
        if not operation_id or len(operation_id) > 128:
            raise MobileError("invalid_operation", "A bounded operation ID is required.")
        async with self.lock:
            if self.state == "recovering":
                raise MobileError("recovering", "Reconnect the device before continuing.")
            if lease.owner != self.owner or lease.epoch != self.epoch:
                raise MobileError("stale_lease", "Device control changed. Refresh before acting.")
            key = (lease.epoch, operation_id)
            if cache_result and key in self.results:
                return self.results[key]
            task = asyncio.create_task(action())
            cancelled = False
            try:
                # The lock outlives caller cancellation, including HTTP disconnects.
                while True:
                    try:
                        result = await asyncio.shield(task)
                        break
                    except asyncio.CancelledError:
                        cancelled = True
                        if task.done():
                            result = task.result()
                            break
            except MobileError as exc:
                if exc.code not in {"invalid_action", "screen_changed", "unsupported_action"}:
                    self.state = "recovering"
                    self.revoke()
                raise
            except BaseException:
                self.state = "recovering"
                self.revoke()
                raise
            if cache_result:
                self.results[key] = result
            while len(self.results) > 256:
                self.results.popitem(last=False)
            if cancelled:
                raise asyncio.CancelledError()
            return result

"""Behavioral checks for the shared device's ownership boundary."""

import asyncio

import pytest

from nodes.mobile._control import DeviceControl, MobileError


async def test_takeover_fences_queued_actions_before_draining():
    control = DeviceControl()
    lease = await control.claim("run:first")
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def action():
        entered.set()
        await finish.wait()
        calls.append("first")
        return "done"

    first = asyncio.create_task(control.perform(lease, "first", action))
    await entered.wait()
    queued = asyncio.create_task(control.perform(lease, "second", action))
    await asyncio.sleep(0)
    takeover = asyncio.create_task(control.claim("viewer:human", takeover=True))
    await asyncio.sleep(0)
    assert control.owner is None
    assert not takeover.done()
    finish.set()
    assert await first == "done"
    with pytest.raises(MobileError, match="control changed"):
        await queued
    human = await takeover
    assert human.owner == "viewer:human"
    assert calls == ["first"]


async def test_retries_deduplicate_only_inside_the_same_lease():
    control = DeviceControl()
    lease = await control.claim("run:first")
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        return {"count": calls}

    assert await control.perform(lease, "id", action) == {"count": 1}
    assert await control.perform(lease, "id", action) == {"count": 1}
    control.revoke()
    replacement = await control.claim("run:first")
    assert await control.perform(replacement, "id", action) == {"count": 2}


async def test_cancelled_caller_keeps_lock_and_success_receipt():
    control = DeviceControl()
    lease = await control.claim("run:first")
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return "committed"

    caller = asyncio.create_task(control.perform(lease, "mutation", action))
    await entered.wait()
    caller.cancel()
    await asyncio.sleep(0)
    assert control.lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert await control.perform(lease, "mutation", action) == "committed"
    assert calls == 1


async def test_uncertain_failure_after_cancellation_fences_the_owner():
    control = DeviceControl()
    lease = await control.claim("run:first")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def broken():
        entered.set()
        await finish.wait()
        raise OSError("transport lost")

    caller = asyncio.create_task(control.perform(lease, "mutation", broken))
    await entered.wait()
    caller.cancel()
    await asyncio.sleep(0)
    finish.set()
    # Either cancellation or the late transport failure may be surfaced;
    # ownership must be fenced in both cases.
    with pytest.raises((asyncio.CancelledError, OSError)):
        await caller
    assert control.state == "recovering"
    assert control.owner is None
    with pytest.raises(MobileError):
        await control.claim("viewer:human", takeover=True)


async def test_failure_fences_lease_and_requires_recovery():
    control = DeviceControl()
    lease = await control.claim("run:first")

    async def broken():
        raise OSError("unknown device outcome")

    with pytest.raises(OSError):
        await control.perform(lease, "mutation", broken)
    assert control.state == "recovering"
    assert control.owner is None

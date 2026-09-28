"""Challenge pauses survive automatic control transitions, without Chrome."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from nodes.browser import _session
from nodes.browser._session import BrowserSession, ControlState, ProfileController, SessionKey
from services.plugin.base import NodeUserError


@pytest.fixture
def browser():
    controller = ProfileController("profile", "Work")
    controller._broadcast = AsyncMock()
    session = BrowserSession(SessionKey("owner", "workflow", "node"), "profile")
    return controller, session


async def assert_agent_paused(controller, session):
    admitted = False

    async def try_action():
        nonlocal admitted
        async with controller.agent_op(session):
            admitted = True

    with pytest.raises(NodeUserError, match="paused for a challenge"):
        await asyncio.wait_for(try_action(), 1)
    assert not admitted


async def pause(controller, session, *, timeout=60):
    await controller.pause_for_challenge(session, message="Please complete the challenge.", timeout=timeout)


async def test_detection_can_pause_inside_agent_lock_and_remains_visible_after_deadline(browser):
    controller, session = browser
    async with controller.agent_op(session):
        await asyncio.wait_for(pause(controller, session, timeout=0), 1)
        assert controller.state == ControlState.AWAITING_USER
    request = controller.pending
    await controller.tick()
    assert request.future.result()["status"] == "timeout"
    assert controller.challenge_required
    assert controller.state == ControlState.AWAITING_USER
    assert controller.snapshot()["request"]["reason"] == "captcha"
    assert controller.snapshot()["request"]["message"] == "Please complete the challenge."
    await assert_agent_paused(controller, session)


async def test_expired_challenge_wait_can_be_recreated_without_unlocking(browser):
    controller, session = browser
    await pause(controller, session, timeout=0)
    result = await controller.request_user(session, reason="other", message="Another request", timeout=60, wait=0)
    assert result["status"] == "timeout"
    expired = controller.pending
    result = await controller.request_user(session, reason="login", message="Replace the reason", timeout=60, wait=0)
    assert result["status"] == "still_waiting"
    assert controller.pending is not expired
    assert controller.pending.reason == "captcha"
    assert controller.pending.message == "Please complete the challenge."
    await assert_agent_paused(controller, session)


@pytest.mark.parametrize("outcome", ["declined", "timeout", "control_lost"])
async def test_automatic_or_declined_handback_keeps_pause_and_settles_inputs(browser, outcome):
    controller, session = browser
    await pause(controller, session)
    assert (await controller.take_over("viewer"))[0]
    barrier = controller.live_control_barrier = AsyncMock()
    assert await controller.hand_back("viewer", outcome=outcome)
    barrier.assert_awaited_once_with("viewer")
    assert controller.controller_viewer is None
    assert controller.pending.future.result()["status"] == outcome
    assert controller.snapshot()["request"]["reason"] == "captcha"
    await assert_agent_paused(controller, session)


async def test_viewer_disconnect_does_not_resume_challenge(browser, monkeypatch):
    controller, session = browser
    monkeypatch.setattr(_session, "VIEWER_DROP_GRACE_SECONDS", 0)
    await pause(controller, session)
    controller.viewer_attached("viewer")
    assert (await controller.take_over("viewer"))[0]
    controller.viewer_detached("viewer")
    await asyncio.wait_for(controller._viewer_drop_tasks["viewer"], 1)
    assert controller.state == ControlState.AWAITING_USER
    assert controller.pending.future.result()["status"] == "viewer_disconnected"
    await assert_agent_paused(controller, session)


async def test_idle_handback_does_not_resume_challenge(browser):
    controller, session = browser
    await pause(controller, session)
    assert (await controller.take_over("viewer"))[0]
    controller.user_input_deadline = time.monotonic() - 1
    await controller.tick()
    assert controller.state == ControlState.AWAITING_USER
    assert controller.pending.future.result()["status"] == "idle_timeout"
    await assert_agent_paused(controller, session)


async def test_explicit_controlling_owner_handback_clears_challenge_after_barrier(browser):
    controller, session = browser
    await pause(controller, session)
    assert (await controller.take_over("viewer"))[0]
    settled = asyncio.Event()
    entered = asyncio.Event()

    async def barrier(viewer):
        assert viewer == "viewer"
        entered.set()
        await settled.wait()

    controller.live_control_barrier = barrier
    request = controller.pending
    release = asyncio.create_task(controller.hand_back("viewer", note="Done"))
    await asyncio.wait_for(entered.wait(), 1)
    assert controller.challenge_required and controller.state == ControlState.USER
    settled.set()
    assert await asyncio.wait_for(release, 1)
    assert not controller.challenge_required
    assert controller.pending is None
    assert request.future.result() == {"status": "handed_back", "note": "Done"}
    async with controller.agent_op(session):
        assert controller.state == ControlState.AGENT


async def test_wrong_viewer_or_anonymous_handback_cannot_clear_challenge(browser):
    controller, session = browser
    await pause(controller, session)
    assert (await controller.take_over("viewer"))[0]
    assert not await controller.hand_back("other-viewer")
    assert await controller.hand_back(None)
    assert controller.challenge_required
    await assert_agent_paused(controller, session)


async def test_releasing_workflow_lease_preserves_challenge_for_next_session(browser):
    controller, session = browser
    await pause(controller, session)
    controller.live_control_barrier = AsyncMock()
    await controller.release_lease(session.session_id)
    assert controller.lease_session is None
    assert controller.state == ControlState.AWAITING_USER
    assert controller.pending.future.result()["status"] == "cancelled"
    assert (await controller.take_over("viewer")) == (False, "no_session")
    next_session = BrowserSession(SessionKey("owner", "other-workflow", "node"), "profile")
    await assert_agent_paused(controller, next_session)
    assert controller.pending.session_id == next_session.session_id
    assert (await controller.take_over("viewer"))[0]
    assert await controller.hand_back("viewer")
    assert not controller.challenge_required


async def test_ordinary_request_timeout_and_explicit_handback_keep_existing_behavior(browser):
    controller, session = browser
    result = await controller.request_user(session, reason="login", message="Sign in", timeout=0, wait=0)
    assert result["status"] == "timeout"
    assert controller.state == ControlState.IDLE and controller.pending is None
    assert not controller.challenge_required and not controller.needs_observation

    result = await controller.request_user(session, reason="login", message="Sign in", timeout=60, wait=0)
    assert result["status"] == "still_waiting"
    request = controller.pending
    assert (await controller.take_over("viewer"))[0]
    assert await controller.hand_back("viewer", note="Signed in")
    assert request.future.result() == {"status": "handed_back", "note": "Signed in"}
    assert controller.state == ControlState.IDLE and controller.pending is None

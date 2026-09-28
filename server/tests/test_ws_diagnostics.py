"""Failed upgrades and dropped connections leave useful, bounded diagnostics."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocketDisconnect


class _Socket:
    def __init__(self, *, path="/ws/status", headers=None, cookies=None):
        self.scope = {"path": path, "query_string": b"token=query-secret"}
        self.headers = headers or {"host": "localhost:5678"}
        self.cookies = cookies or {}
        self.close = AsyncMock()
        self.state = SimpleNamespace()


@pytest.mark.parametrize(
    ("origin", "token", "payload", "category", "close_code"),
    [
        ("http://foreign.example/?token=origin-secret", "cookie-secret", {"sub": "owner"}, "origin_not_allowed", 4003),
        (None, None, None, "missing_session", 4001),
        (None, "cookie-secret", None, "invalid_session", 4001),
        (None, "cookie-secret", {"sub": ""}, "invalid_session_subject", 4001),
    ],
)
async def test_browser_rejection_explains_http_denial_without_request_secrets(
    monkeypatch, origin, token, payload, category, close_code
):
    import services.authz.ws_session as session

    logger = Mock()
    monkeypatch.setattr(session, "logger", logger)
    headers = {"host": "localhost:5678", "authorization": "Bearer authorization-secret"}
    if origin:
        headers["origin"] = origin
    socket = _Socket(
        path="/ws/status?token=path-query-secret",
        headers=headers,
        cookies={"opencompany_token": token} if token else {},
    )
    settings = SimpleNamespace(cors_origins=[], vite_auth_enabled="true", jwt_cookie_name="opencompany_token")
    auth = Mock()
    auth.verify_token.return_value = payload

    assert await session.authenticate_ws(socket, settings=settings, user_auth_service=lambda: auth) is None

    logger.warning.assert_called_once_with(
        "[WebSocket] Handshake rejected",
        path="/ws/status",
        reason=category,
        close_code=close_code,
        http_status=403,
    )
    assert socket.close.await_args.kwargs["code"] == close_code
    assert "secret" not in repr(logger.mock_calls)


@pytest.mark.parametrize("origin", [None, "http://foreign.example/?token=origin-secret"])
async def test_internal_rejection_uses_the_same_safe_diagnostics(monkeypatch, origin):
    import services.authz.ws_session as session

    logger = Mock()
    monkeypatch.setattr(session, "logger", logger)
    headers = {"host": "localhost:5678", "x-opencompany-internal-token": "worker-secret"}
    if origin:
        headers["origin"] = origin
    socket = _Socket(path="/ws/internal", headers=headers)

    assert not await session.admit_internal_ws(socket, settings=SimpleNamespace(cors_origins=[], secret_key="server-secret"))

    logger.warning.assert_called_once_with(
        "[WebSocket] Handshake rejected",
        path="/ws/internal",
        reason="origin_not_allowed" if origin else "invalid_worker_token",
        close_code=4003 if origin else 4001,
        http_status=403,
    )
    assert "secret" not in repr(logger.mock_calls)


async def test_rejection_prefers_route_template_and_bounds_fallback_path(monkeypatch):
    import services.authz.ws_session as session

    logger = Mock()
    monkeypatch.setattr(session, "logger", logger)
    settings = SimpleNamespace(cors_origins=[], vite_auth_enabled="true", jwt_cookie_name="opencompany_token")
    socket = _Socket(path="/ws/browser/private-path-secret")
    socket.scope["route"] = SimpleNamespace(path="/ws/browser/{session_id}")

    await session.authenticate_ws(socket, settings=settings, user_auth_service=lambda: None)

    assert logger.warning.call_args.kwargs["path"] == "/ws/browser/{session_id}"
    assert "secret" not in repr(logger.mock_calls)
    socket.scope = {"path": "/ws/\r\n" + "a" * 1000 + "?token=query-secret"}

    await session.authenticate_ws(socket, settings=settings, user_auth_service=lambda: None)

    path = logger.warning.call_args.kwargs["path"]
    assert len(path) == 160
    assert path.isprintable()


@pytest.mark.parametrize("auth_enabled", ["true", "false"])
async def test_accepted_browser_handshake_does_not_emit_a_rejection(monkeypatch, auth_enabled):
    import services.authz.ws_session as session

    logger = Mock()
    monkeypatch.setattr(session, "logger", logger)
    socket = _Socket(cookies={"opencompany_token": "cookie-secret"})
    settings = SimpleNamespace(cors_origins=[], vite_auth_enabled=auth_enabled, jwt_cookie_name="opencompany_token")
    auth = Mock()
    auth.verify_token.return_value = {"sub": "owner"}

    assert await session.authenticate_ws(socket, settings=settings, user_auth_service=lambda: auth) == "owner"

    logger.warning.assert_not_called()
    socket.close.assert_not_awaited()


@pytest.mark.parametrize(
    ("code", "reason", "level"),
    [(1000, "tab closed", "debug"), (1001, "server restarting", "debug"), (1006, "", "warning"), (1011, "keepalive timeout", "warning")],
)
async def test_status_disconnect_reports_close_details_and_cleans_up(monkeypatch, code, reason, level):
    import routers.websocket as router

    socket = _Socket()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect(code=code, reason=reason))
    logger = Mock()
    broadcaster = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr(router, "logger", logger)
    monkeypatch.setattr(router, "authenticate_ws", AsyncMock(return_value="owner"))
    monkeypatch.setattr(router, "get_status_broadcaster", lambda: broadcaster)

    await router.websocket_status_endpoint(socket)

    getattr(logger, level).assert_called_once_with(
        "[WebSocket] Client disconnected", path="/ws/status", close_code=code, reason=reason
    )
    broadcaster.disconnect.assert_awaited_once_with(socket)
    assert socket not in router._handler_tasks


async def test_status_disconnect_bounds_and_control_cleans_peer_reason(monkeypatch):
    import routers.websocket as router

    socket = _Socket()
    socket.receive_json = AsyncMock(side_effect=WebSocketDisconnect(code=1011, reason="line\n" + "x" * 1000))
    logger = Mock()
    monkeypatch.setattr(router, "logger", logger)
    monkeypatch.setattr(router, "authenticate_ws", AsyncMock(return_value="owner"))
    monkeypatch.setattr(router, "get_status_broadcaster", lambda: SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock()))

    await router.websocket_status_endpoint(socket)

    reason = logger.warning.call_args.kwargs["reason"]
    assert reason.startswith("line?")
    assert len(reason) == 123


@pytest.mark.parametrize("cancel_during", ["accept", "initial_snapshot"])
async def test_cancelled_connection_setup_does_not_leave_a_registered_socket(monkeypatch, cancel_during):
    import routers.websocket as router
    from services.status_broadcaster import StatusBroadcaster

    broadcaster = StatusBroadcaster()
    socket = _Socket()
    setup_blocked = asyncio.Event()

    async def block_setup(*_args):
        setup_blocked.set()
        await asyncio.Event().wait()

    socket.accept = AsyncMock(side_effect=block_setup if cancel_during == "accept" else None)
    socket.send_json = AsyncMock(side_effect=block_setup)
    monkeypatch.setattr(router, "authenticate_ws", AsyncMock(return_value="owner"))
    monkeypatch.setattr(router, "get_status_broadcaster", lambda: broadcaster)
    task = asyncio.create_task(router.websocket_status_endpoint(socket))
    await asyncio.wait_for(setup_blocked.wait(), timeout=1)
    assert (socket in broadcaster._connections) is (cancel_during == "initial_snapshot")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert socket not in broadcaster._connections
    assert socket not in router._handler_tasks

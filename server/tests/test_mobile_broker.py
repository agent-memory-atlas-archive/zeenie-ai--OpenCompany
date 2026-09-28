"""Exercise the actual loopback HTTP broker with a fake device driver."""

import json
from unittest.mock import AsyncMock

import aiohttp

from nodes.mobile._runtime import MobileRuntime


async def test_broker_expired_stale_and_revoked_capabilities(monkeypatch):
    runtime = MobileRuntime()
    driver = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(runtime, "driver_call", driver)
    lease = await runtime.control.claim("run:task")
    runtime.capabilities["private-capability"] = ("task", lease)
    url = await runtime.ensure_broker()
    assert url.startswith("http://127.0.0.1:")
    assert await runtime.ensure_broker() == url
    try:
        async with aiohttp.ClientSession() as client:

            async def post(capability, operation="observe", epoch=lease.epoch, operation_id="one"):
                async with client.post(
                    url,
                    headers={"Authorization": "Bearer " + capability},
                    json={"epoch": epoch, "operation": operation, "parameters": {}, "operation_id": operation_id},
                ) as reply:
                    return reply.status, await reply.json()

            assert (await post("expired"))[0] == 403
            assert (await post("private-capability", epoch=lease.epoch - 1))[0] == 409
            driver.assert_not_awaited()
            assert (await post("private-capability"))[0] == 200
            assert runtime.control.results == {}  # never retain screenshot history
            assert (await post("private-capability", "key", operation_id="two"))[0] == 200
            assert driver.await_count == 2
            runtime.control.revoke()
            for operation in ("observe", "key"):
                code, body = await post("private-capability", operation, operation_id="revoked-" + operation)
                assert code == 409
                assert body["success"] is False
            assert driver.await_count == 2
    finally:
        await runtime.broker_runner.cleanup()


def test_public_snapshot_excludes_private_capabilities_and_task_prompt():
    runtime = MobileRuntime()
    runtime.capabilities["secret-capability-value"] = ("run", None)
    runtime.active = {"run_id": "run", "status": "running", "prompt": "private task text"}
    runtime.queue = [{"run_id": "next", "prompt": "queued private text"}]
    snapshot = json.dumps(runtime.snapshot())
    assert "secret-capability-value" not in snapshot
    assert "private task text" not in snapshot
    assert "queued private text" not in snapshot


def test_driver_environment_is_allowlisted_and_disables_tracing(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
        "AWS_ACCESS_KEY_ID",
        "UNRELATED_SETTING",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
    ):
        monkeypatch.setenv(key, "must-not-inherit")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("TEMP", "permitted-temp-directory")
    env = MobileRuntime().driver_env()
    assert "must-not-inherit" not in env.values()
    assert env["TEMP"] == "permitted-temp-directory"
    assert env["MOBILE_USE_TELEMETRY_ENABLED"] == "false"
    assert env["LANGSMITH_TRACING"] == "false"
    assert env["LANGCHAIN_TRACING_V2"] == "false"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"

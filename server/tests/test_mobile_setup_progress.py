"""Setup survives page reloads and reports backend interruption explicitly."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from nodes.mobile import _runtime as module


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "mobile_root", lambda: tmp_path)
    return tmp_path


async def test_success_has_durable_progress_and_reuses_state(monkeypatch, isolated):
    async def engine(*, progress):
        progress("Downloading engine: 50%")

    monkeypatch.setattr(module, "install_engine", engine)
    monkeypatch.setattr(module, "create_device", AsyncMock())
    runtime = module.MobileRuntime()
    runtime.setup(True)
    assert runtime.snapshot()["setup"] == "installing_engine"
    await runtime.setup_task
    restored = module.MobileRuntime().snapshot()
    assert restored["setup"] == "ready"
    assert restored["setup_progress"]["finished_at"] >= restored["setup_progress"]["started_at"]
    assert any("50%" in event["message"] for event in restored["setup_progress"]["events"])


async def test_empty_timeout_still_has_visible_error(monkeypatch, isolated):
    monkeypatch.setattr(module, "install_engine", AsyncMock(side_effect=TimeoutError()))
    runtime = module.MobileRuntime()
    runtime.setup(True)
    await runtime.setup_task
    assert runtime.snapshot()["setup"] == "error"
    assert "TimeoutError" in runtime.snapshot()["setup_error"]


async def test_shutdown_records_interruption(monkeypatch, isolated):
    started = asyncio.Event()

    async def engine(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "install_engine", engine)
    runtime = module.MobileRuntime()
    runtime.setup(True)
    await started.wait()
    await runtime.shutdown()
    restored = module.MobileRuntime().snapshot()
    assert restored["setup"] == "interrupted"
    assert "Retry setup" in restored["setup_error"]


def test_abrupt_exit_detected_on_next_status(isolated):
    (isolated / "setup-status.json").write_text(json.dumps({
        "state": "installing_device", "error": "", "progress": {"started_at": 1, "events": []},
    }))
    status = module.MobileRuntime().snapshot()
    assert status["setup"] == "interrupted"
    assert "backend restart" in status["setup_error"]
    assert json.loads((isolated / "setup-status.json").read_text())["state"] == "interrupted"


def test_history_is_bounded(isolated):
    runtime = module.MobileRuntime()
    for index in range(100):
        runtime._setup_update(f"Progress {index}")
    assert len(runtime.setup_progress["events"]) == 80
    assert runtime.setup_progress["events"][-1]["message"] == "Progress 99"

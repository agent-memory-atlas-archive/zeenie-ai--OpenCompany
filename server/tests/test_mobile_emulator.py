"""Emulator ownership, recovery, and startup failure regression coverage."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodes.mobile import _emulator as emulator
from nodes.mobile import _runtime as runtime
from nodes.mobile._control import MobileError


def test_device_lock_excludes_second_backend_and_releases(tmp_path):
    first = emulator.DeviceLock(tmp_path)
    try:
        with pytest.raises(MobileError, match="Another OpenCompany"):
            emulator.DeviceLock(tmp_path)
    finally:
        first.close()
    emulator.DeviceLock(tmp_path).close()


def test_boot_error_ignores_previous_attempts(tmp_path):
    log = tmp_path / "emulator.log"
    old = b"FATAL Running multiple emulators with the same AVD\n"
    log.write_bytes(old + b"new attempt failed\n")
    assert emulator.boot_error(log, 0, 1).code == "device_in_use"
    assert emulator.boot_error(log, len(old), 7).code == "boot_failed"
    assert "7" in str(emulator.boot_error(log, len(old), 7))


@pytest.mark.parametrize("same_path,same_sdk,expected", [(True, True, True), (False, True, False), (True, False, False)])
async def test_recovery_requires_avd_path_and_sdk_process(monkeypatch, tmp_path, same_path, same_sdk, expected):
    avd = tmp_path / "avd" / "OpenCompany.avd"
    sdk = tmp_path / "sdk" / "emulator"
    executable = sdk / "emulator.exe"
    proc = SimpleNamespace(pid=123, info={
        "exe": str((sdk if same_sdk else tmp_path / "other") / "qemu-system-x86_64.exe"),
        "cmdline": ["qemu", "-avd", "OpenCompany", "-port", "5560"],
    })
    calls = AsyncMock(side_effect=["List of devices attached\nemulator-5560\tdevice\n", str(avd if same_path else tmp_path / "other.avd") + "\nOK\n"])
    monkeypatch.setattr(emulator, "command", calls)
    monkeypatch.setattr(emulator.psutil, "process_iter", lambda *_: [proc])
    result = await emulator.recover_emulator(Path("adb"), executable, avd)
    assert bool(result) is expected
    if result:
        assert result[1] == "emulator-5560"


def test_missing_pid_never_targets_backend_children(monkeypatch):
    monkeypatch.setattr(emulator.psutil, "Process", lambda *_: pytest.fail("must not inspect current process"))
    assert emulator.owned_children(None) == []


@pytest.fixture
def phone(monkeypatch, tmp_path):
    value = runtime.MobileRuntime()
    monkeypatch.setattr(runtime, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(runtime, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "sdk_tool", lambda name: tmp_path / name)
    monkeypatch.setattr(runtime, "engine_ready", lambda: True)
    (tmp_path / "resource.json").write_text("{}")
    return value


async def test_reconnect_does_not_launch_duplicate(phone, monkeypatch):
    proc = SimpleNamespace(returncode=None)
    monkeypatch.setattr(runtime, "recover_emulator", AsyncMock(return_value=(proc, "emulator-5560")))
    spawn = AsyncMock(side_effect=AssertionError("must reuse existing phone"))
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", spawn)

    async def driver():
        phone.driver = SimpleNamespace(returncode=None)
        phone.geometry = {"width": 1080, "height": 2400}

    monkeypatch.setattr(phone, "_start_driver", driver)
    try:
        assert (await phone.start())["running"] is True
        assert phone.serial == "emulator-5560"
        spawn.assert_not_awaited()
    finally:
        phone.device_lock.close()


async def test_live_emulator_with_dead_driver_reconnects(phone, monkeypatch):
    phone.process = SimpleNamespace(returncode=None)
    phone.driver = SimpleNamespace(returncode=1)
    phone.geometry = {"width": 1}
    start = AsyncMock()
    monkeypatch.setattr(phone, "_start_driver", start)
    await phone.start()
    start.assert_awaited_once()
    assert phone.geometry is None


async def test_spawn_failure_releases_lock_and_retains_error(phone, monkeypatch):
    monkeypatch.setattr(runtime, "recover_emulator", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(OSError):
        await phone.start()
    assert phone.device_lock is None
    assert phone.process is None
    assert phone.snapshot()["start_error"]


async def test_stop_kills_owned_children_after_launcher_exit(phone, monkeypatch):
    phone.process = SimpleNamespace(returncode=0, pid=456)
    children = [object()]
    monkeypatch.setattr(runtime, "owned_children", lambda pid: children if pid == 456 else [])
    stop = AsyncMock()
    monkeypatch.setattr(runtime, "stop_children", stop)
    await phone.stop()
    stop.assert_awaited_once_with(children)
    assert phone.process is None


async def test_boot_probe_timeout_retries_instead_of_killing_phone(phone, monkeypatch):
    monkeypatch.setattr(runtime, "recover_emulator", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime, "command", AsyncMock(side_effect=[TimeoutError(), "1"]))
    proc = SimpleNamespace(returncode=None)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(phone, "_start_driver", AsyncMock())
    try:
        await phone.start()
        assert runtime.command.await_count == 2
        assert phone.process is proc
    finally:
        phone.device_lock.close()


async def test_status_returns_disconnection_state_instead_of_http_error(phone, monkeypatch):
    from nodes.mobile._router import status
    phone.serial = "emulator-5560"
    monkeypatch.setattr(runtime, "get_runtime", lambda: phone)
    monkeypatch.setattr(phone, "driver_call", AsyncMock(side_effect=MobileError("device_offline", "offline")))
    result = await status(principal="owner")
    assert result["running"] is False
    assert "reconnect" in result["start_error"]

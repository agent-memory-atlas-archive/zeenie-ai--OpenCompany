"""``core.desktop`` — the desktop-host contract's parent watchdogs.

A GUI shell can vanish without running its own shutdown code, so the
backend must notice on its own and exit through the normal uvicorn path
(so the lifespan reaps Temporal / node / edgymeow). Two detectors:

- PID poll with a create-time guard (PID reuse must not look like "alive").
- stdin EOF (the shell keeps the pipe open; EOF is a fast, exact signal).

``request_shutdown`` is the single funnel: it raises the signal uvicorn
already handles and arms a hard deadline. Tests patch the raise so the
pytest process never actually receives SIGINT.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.unit


def _load_desktop():
    spec = importlib.util.spec_from_file_location("core.desktop", SERVER_DIR / "core" / "desktop.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["core.desktop"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def desktop(monkeypatch):
    mod = _load_desktop()
    for var in (mod.ENV_FLAG, mod.ENV_PARENT_PID, mod.ENV_TOKEN, mod.ENV_STDIN_WATCHDOG):
        monkeypatch.delenv(var, raising=False)
    # Never let a test really signal the pytest process or arm the deadline exit.
    raised: list[int] = []
    monkeypatch.setattr(mod.signal, "raise_signal", lambda sig: raised.append(sig))
    monkeypatch.setattr(mod, "_deadline_thread", lambda reason: None)
    mod._shutdown_requested = False
    mod._raised = raised  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def sleeper():
    """A real child process to act as the 'parent' we watch."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


class TestMode:
    def test_inert_unless_flag_is_exactly_one(self, desktop, monkeypatch):
        assert desktop.is_desktop_mode() is False
        monkeypatch.setenv(desktop.ENV_FLAG, "true")
        assert desktop.is_desktop_mode() is False
        monkeypatch.setenv(desktop.ENV_FLAG, "1")
        assert desktop.is_desktop_mode() is True

    def test_start_desktop_mode_is_a_no_op_outside_desktop_mode(self, desktop):
        loop = asyncio.new_event_loop()
        try:
            armed = desktop.start_desktop_mode(loop)
        finally:
            loop.close()
        assert armed == {"job_object": False, "parent_watchdog": False, "stdin_watchdog": False}

    def test_parent_pid_parsing(self, desktop, monkeypatch):
        assert desktop.parent_pid() is None
        monkeypatch.setenv(desktop.ENV_PARENT_PID, "abc")
        assert desktop.parent_pid() is None
        monkeypatch.setenv(desktop.ENV_PARENT_PID, "0")
        assert desktop.parent_pid() is None
        monkeypatch.setenv(desktop.ENV_PARENT_PID, "4242")
        assert desktop.parent_pid() == 4242


class TestRequestShutdown:
    def test_raises_the_signal_uvicorn_handles_once(self, desktop):
        desktop.request_shutdown("test")
        desktop.request_shutdown("test again")
        expected = signal.SIGINT if sys.platform == "win32" else signal.SIGTERM
        assert desktop._raised == [expected]
        assert desktop.shutdown_requested() is True


class TestParentAlive:
    def test_live_process_is_alive(self, desktop, sleeper):
        import psutil

        ct = psutil.Process(sleeper.pid).create_time()
        assert desktop.parent_alive(sleeper.pid, ct) is True

    def test_dead_process_is_dead(self, desktop, sleeper):
        import psutil

        ct = psutil.Process(sleeper.pid).create_time()
        sleeper.kill()
        sleeper.wait()
        # psutil may still report a zombie briefly on POSIX; both branches are "dead".
        deadline = time.time() + 5
        while time.time() < deadline and desktop.parent_alive(sleeper.pid, ct):
            time.sleep(0.05)
        assert desktop.parent_alive(sleeper.pid, ct) is False

    def test_pid_reuse_guard(self, desktop, sleeper):
        """Same PID, different create time => a different process => dead."""
        import psutil

        ct = psutil.Process(sleeper.pid).create_time()
        assert desktop.parent_alive(sleeper.pid, ct - 100.0) is False


class TestWatchers:
    @pytest.mark.asyncio
    async def test_watch_parent_requests_shutdown_when_parent_dies(self, desktop, sleeper):
        task = asyncio.create_task(desktop.watch_parent(sleeper.pid, interval=0.1))
        await asyncio.sleep(0.3)
        assert not desktop.shutdown_requested(), "must not fire while the parent is alive"
        sleeper.kill()
        sleeper.wait()
        await asyncio.wait_for(task, timeout=5)
        assert desktop.shutdown_requested() is True

    @pytest.mark.asyncio
    async def test_stdin_eof_requests_shutdown(self, desktop):
        """A real pipe: the shell holds the write end; closing it is EOF."""
        r, w = os.pipe()
        loop = asyncio.get_running_loop()
        thread = desktop.start_stdin_watchdog(loop, fd=r)
        assert thread is not None
        await asyncio.sleep(0.3)
        assert not desktop.shutdown_requested(), "must not fire while the pipe is open"
        os.write(w, b"noise the shell should never send")  # data is drained, not EOF
        await asyncio.sleep(0.3)
        assert not desktop.shutdown_requested()
        os.close(w)
        deadline = time.time() + 5
        while time.time() < deadline and not desktop.shutdown_requested():
            await asyncio.sleep(0.05)
        assert desktop.shutdown_requested() is True
        thread.join(timeout=5)
        assert not thread.is_alive(), "watchdog thread must exit so interpreter finalisation is safe"
        os.close(r)

    def test_stdin_watchdog_thread_exits_when_shutdown_is_requested_elsewhere(self, desktop):
        """The thread must never stay parked in a blocking read: a daemon
        thread inside ReadFile at finalisation crashed the exiting process
        (0xC0000005) on every non-stdin shutdown."""
        r, w = os.pipe()
        try:
            stopped = {"flag": False}
            result = {}

            def run():
                result["eof"] = desktop.wait_for_stdin_eof(r, poll=0.05, stop=lambda: stopped["flag"])

            import threading

            t = threading.Thread(target=run, daemon=True)
            t.start()
            time.sleep(0.2)
            assert t.is_alive()
            stopped["flag"] = True
            t.join(timeout=5)
            assert not t.is_alive()
            assert result["eof"] is False
        finally:
            os.close(w)
            os.close(r)

    def test_stdin_watchdog_refuses_a_non_pipe(self, desktop, tmp_path):
        f = open(tmp_path / "not-a-pipe", "wb+")
        try:
            loop = asyncio.new_event_loop()
            try:
                assert desktop.start_stdin_watchdog(loop, fd=f.fileno()) is None
            finally:
                loop.close()
        finally:
            f.close()

    @pytest.mark.asyncio
    async def test_start_desktop_mode_arms_what_the_env_asks_for(self, desktop, monkeypatch, sleeper):
        monkeypatch.setenv(desktop.ENV_FLAG, "1")
        monkeypatch.setenv(desktop.ENV_PARENT_PID, str(sleeper.pid))
        monkeypatch.setenv(desktop.ENV_TOKEN, "t")
        monkeypatch.setattr(desktop, "install_job_object", lambda: True)
        armed = desktop.start_desktop_mode(asyncio.get_running_loop())
        assert armed == {"job_object": True, "parent_watchdog": True, "stdin_watchdog": False}
        # Let the created task start, then cancel it so the loop closes cleanly.
        await asyncio.sleep(0)
        for t in asyncio.all_tasks():
            if t.get_name() == "desktop-parent-watchdog":
                t.cancel()


def test_install_job_object_is_a_no_op_off_windows(desktop):
    if sys.platform == "win32":
        pytest.skip("covered by tests/test_job_object.py")
    assert desktop.install_job_object() is False


def test_shutdown_signal_choice(desktop):
    expected = signal.SIGINT if sys.platform == "win32" else signal.SIGTERM
    assert desktop._shutdown_signal() == expected
    assert os.name  # keep the import used on every platform

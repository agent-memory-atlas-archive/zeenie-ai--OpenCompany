"""Unit tests for ``cli.ports``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import psutil
import pytest

from cli import ports


def test_kill_pid_no_such_process_returns_false():
    with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(123)):
        assert ports.kill_pid(123) is False


def test_kill_pid_terminates_then_waits():
    proc = MagicMock()
    with patch.object(psutil, "Process", return_value=proc), patch.object(ports.sys, "platform", "linux"):
        assert ports.kill_pid(123) is True
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()


def test_kill_pid_force_kills_on_timeout():
    proc = MagicMock()
    proc.wait.side_effect = psutil.TimeoutExpired(seconds=1)
    with patch.object(psutil, "Process", return_value=proc), patch.object(ports.sys, "platform", "linux"):
        assert ports.kill_pid(123) is True
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_windows_kill_pid_delivers_graceful_signal_before_waiting():
    proc = MagicMock()
    with (
        patch.object(psutil, "Process", return_value=proc),
        patch.object(ports.sys, "platform", "win32"),
        patch.object(ports.signal, "CTRL_BREAK_EVENT", 1, create=True),
        patch.object(ports.os, "kill") as signal_process,
    ):
        assert ports.kill_pid(123, graceful_timeout=125) is True
    signal_process.assert_called_once_with(123, 1)
    proc.wait.assert_called_once_with(timeout=125)
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


def test_windows_kill_pid_falls_back_if_control_signal_unavailable():
    proc = MagicMock()
    with (
        patch.object(psutil, "Process", return_value=proc),
        patch.object(ports.sys, "platform", "win32"),
        patch.object(ports.signal, "CTRL_BREAK_EVENT", 1, create=True),
        patch.object(ports.os, "kill", side_effect=OSError("no process group")),
    ):
        assert ports.kill_pid(123) is True
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=3.0)


@pytest.mark.parametrize("is_backend,expected", [(True, 125.0), (False, 3.0)])
def test_port_cleanup_only_grants_long_grace_to_known_backend(is_backend, expected):
    with (
        patch.object(ports, "find_pids_by_port", side_effect=[{123}, set()]),
        patch.object(ports.os, "getpid", return_value=456),
        patch.object(ports, "_is_backend_process", return_value=is_backend),
        patch.object(ports, "kill_pid", return_value=True) as kill_pid,
        patch.object(ports.time, "sleep"),
    ):
        result = ports.kill_port(5678, backend_graceful_timeout=125.0)
    kill_pid.assert_called_once_with(123, graceful_timeout=expected)
    assert result.port_free
    assert result.killed_pids == [123]


@pytest.mark.parametrize("app,same_checkout,expected", [("main:app", True, True), ("other:app", True, False), ("main:app", False, False)])
def test_backend_identification_requires_app_and_checkout(tmp_path, app, same_checkout, expected):
    proc = MagicMock()
    proc.cmdline.return_value = ["python", "-m", "uvicorn", app, "--port", "5678"]
    proc.cwd.return_value = str(tmp_path / ("server" if same_checkout else "other/server"))
    with patch.object(psutil, "Process", return_value=proc):
        assert ports._is_backend_process(123, str(tmp_path)) is expected


def test_find_pids_by_port_counts_only_listeners():
    """A dead server's half-closed sockets must not make a bindable port
    look occupied (the 'Port still in use' false positive on stop)."""
    from types import SimpleNamespace as NS

    conns = [
        NS(laddr=NS(port=5678), pid=111, status=psutil.CONN_LISTEN),
        NS(laddr=NS(port=5678), pid=222, status=psutil.CONN_CLOSE_WAIT),
        NS(laddr=NS(port=5678), pid=333, status=psutil.CONN_ESTABLISHED),
        NS(laddr=NS(port=9999), pid=444, status=psutil.CONN_LISTEN),
    ]
    with patch.object(psutil, "net_connections", return_value=conns):
        assert ports.find_pids_by_port(5678) == {111}


def test_kill_port_excludes_self():
    """The function must never kill its own PID."""
    import os

    my_pid = os.getpid()
    with patch.object(ports, "find_pids_by_port", side_effect=[{my_pid}, set()]):
        result = ports.kill_port(9999)
    assert result.killed_pids == []
    assert result.port_free is True


def test_orphan_reaper_kills_only_this_checkouts_processes():
    """A substring match on the root also caught sibling folders sharing the
    prefix and the worktrees nested under ``.claude/worktrees/`` -- so a
    ``company stop`` in the main checkout killed dev servers and test runs in
    every worktree."""
    from types import SimpleNamespace as NS

    root = "D:\\startup\\projects\\opencompany"
    procs = [
        NS(pid=11, info={"name": "python.exe", "cmdline": [f"{root}\\server\\.venv\\Scripts\\python.exe", "-m", "uvicorn"]}),
        NS(pid=12, info={"name": "bun.exe", "cmdline": ["bun", f"{root}/server/nodejs/dist/index.js"]}),
        NS(pid=21, info={"name": "python.exe", "cmdline": [f"{root}-worktrees\\feature\\server\\.venv\\Scripts\\python.exe"]}),
        NS(
            pid=22,
            info={
                "name": "python.exe",
                "cmdline": [f"{root}\\.claude\\worktrees\\native-browser\\server\\.venv\\Scripts\\python.exe", "-m", "pytest"],
            },
        ),
        NS(pid=23, info={"name": "python.exe", "cmdline": ["python", "-c", "print('opencompanyx')"]}),
        NS(pid=24, info={"name": "chrome.exe", "cmdline": [f"{root}\\server\\whatever"]}),
    ]
    killed = []
    with (
        patch.object(psutil, "process_iter", return_value=procs),
        patch.object(ports, "_ancestor_pids", return_value=set()),
        patch.object(ports, "kill_pid", side_effect=lambda pid, **_kw: killed.append(pid) or True),
    ):
        assert ports.kill_orphaned_opencompany_processes(root) == [11, 12]
    assert killed == [11, 12]


def test_checkout_match_needs_a_path_boundary():
    root = "d:/startup/projects/opencompany"
    assert ports._names_this_checkout(f"python {root}/server/main.py", root)
    assert ports._names_this_checkout(f"python -m cli dev --root {root}", root)
    assert not ports._names_this_checkout(f"python {root}-worktrees/a/main.py", root)
    assert not ports._names_this_checkout(f"python {root}/.claude/worktrees/a/server/main.py", root)
    # Both a worktree path and a main-checkout path: the main one counts.
    assert ports._names_this_checkout(f"python {root}/.claude/worktrees/a/x.py {root}/server/y.py", root)

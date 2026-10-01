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
        patch.object(ports, "send_ctrl_break", return_value=True) as send_break,
        patch.object(ports.os, "kill") as os_kill,
    ):
        assert ports.kill_pid(123, graceful_timeout=125) is True
    send_break.assert_called_once_with(123)
    os_kill.assert_not_called()
    proc.wait.assert_called_once_with(timeout=125)
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


def test_windows_kill_pid_terminates_what_the_signal_cannot_reach():
    """A service started in another terminal cannot get the event. Sending it
    with ``os.kill`` crashed ``company stop``: on Python 3.12.8 the refused
    event became a hard kill plus a pending ``OSError``, which surfaced as a
    ``SystemError`` inside psutil's ``Process.wait``."""
    proc = MagicMock()
    with (
        patch.object(psutil, "Process", return_value=proc),
        patch.object(ports.sys, "platform", "win32"),
        patch.object(ports, "send_ctrl_break", return_value=False),
        patch.object(ports.os, "kill") as os_kill,
    ):
        assert ports.kill_pid(123) is True
    os_kill.assert_not_called()
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


def _process(pid, name, cmdline, cwd):
    from types import SimpleNamespace as NS

    return NS(pid=pid, info={"name": name, "cmdline": cmdline}, cwd=lambda: cwd, kill=MagicMock())


def test_orphan_reaper_kills_only_this_checkouts_processes():
    """A substring match on the root also caught sibling folders sharing the
    prefix and the worktrees nested in the checkout, so a ``company stop`` in
    the main checkout killed dev servers and test runs in every worktree. A
    worktree's test run that borrows the main checkout's Python names a path
    here too; its working directory is what says whose run it is."""
    root = "D:\\startup\\projects\\opencompany"
    worktree = f"{root}\\.claude\\worktrees\\native-browser"
    main_python = f"{root}\\server\\.venv\\Scripts\\python.exe"
    procs = [
        _process(11, "python.exe", [main_python, "-m", "uvicorn"], f"{root}\\server"),
        _process(12, "bun.exe", ["bun", f"{root}/server/nodejs/dist/index.js"], f"{root}\\server\\nodejs"),
        _process(21, "python.exe", [f"{root}-worktrees\\feature\\server\\.venv\\Scripts\\python.exe"], f"{root}-worktrees\\feature"),
        _process(22, "python.exe", [f"{worktree}\\server\\.venv\\Scripts\\python.exe", "-m", "pytest"], f"{worktree}\\server"),
        _process(23, "python.exe", ["python", "-c", "print('opencompanyx')"], root),
        _process(24, "chrome.exe", [f"{root}\\server\\whatever"], root),
        _process(25, "python.exe", [main_python, "-m", "pytest", "tests/"], f"{worktree}\\server"),
    ]
    checkout = ports._Tree(ports._normalized(root), (ports._normalized(worktree),))
    killed = []
    with (
        patch.object(psutil, "process_iter", return_value=procs),
        patch.object(ports, "_working_tree", return_value=checkout),
        patch.object(ports, "_ancestor_pids", return_value=set()),
        patch.object(ports, "kill_pid", side_effect=lambda pid, **_kw: killed.append(pid) or True),
    ):
        assert ports.kill_orphaned_opencompany_processes(root) == [11, 12]
    assert killed == [11, 12]


def test_a_checkout_is_named_only_at_a_path_boundary_outside_its_worktrees():
    root = "d:/startup/projects/opencompany"
    checkout = ports._Tree(root, (f"{root}/.claude/worktrees/a", "d:/elsewhere/b"))
    assert checkout.named_in(f"python {root}/server/main.py")
    assert checkout.named_in(f"python -m cli dev --root {root}")
    assert not checkout.named_in(f"python {root}-worktrees/a/main.py")
    assert not checkout.named_in(f"python {root}/.claude/worktrees/a/server/main.py")
    # A worktree path and a checkout path: the checkout one counts.
    assert checkout.named_in(f"python {root}/.claude/worktrees/a/x.py {root}/server/y.py")
    # Git decides what is a separate checkout, not the folder it sits in.
    assert checkout.named_in(f"python {root}/.claude/worktrees/not-a-worktree/x.py")


def test_the_deepest_working_tree_owns_a_directory():
    root = "d:/repo"
    nested = f"{root}/.claude/worktrees/a"
    checkout = ports._Tree(root, (nested, "d:/beside"))
    assert not checkout.belongs_elsewhere(f"{root}/server")
    assert checkout.belongs_elsewhere(f"{nested}/server")
    assert checkout.belongs_elsewhere("d:/beside/client")
    assert not checkout.belongs_elsewhere("c:/users/me")
    # Seen from the nested worktree, the enclosing checkout is the other tree.
    worktree = ports._Tree(nested, (root,))
    assert worktree.belongs_elsewhere(f"{root}/server")
    assert not worktree.belongs_elsewhere(f"{nested}/server")


def test_working_trees_come_from_git(tmp_path):
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    repo = tmp_path.resolve() / "repo"
    repo.mkdir()

    def run(*args):
        subprocess.run([git, "-C", str(repo), *args], check=True, capture_output=True)

    run("init", "-q")
    run("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q", "--allow-empty", "-m", "start")
    nested = repo / ".claude" / "worktrees" / "a"
    beside = tmp_path.resolve() / "beside"
    run("worktree", "add", "-q", "--detach", str(nested))
    run("worktree", "add", "-q", "--detach", str(beside))

    checkout = ports._working_tree(repo)
    assert checkout.root == ports._normalized(repo)
    assert set(checkout.others) == {ports._normalized(nested), ports._normalized(beside)}
    assert ports._normalized(repo) in ports._working_tree(nested).others
    # A directory inside a working tree, but not one itself, has no others.
    (repo / "sub").mkdir()
    assert ports._working_tree(repo / "sub").others == ()


def test_working_trees_without_git_or_a_repository(tmp_path):
    with patch.object(ports.shutil, "which", return_value=None):
        assert ports._working_tree(tmp_path).others == ()
    assert ports._working_tree(tmp_path).others == ()


def test_temporal_kill_stays_inside_the_data_directory(tmp_path):
    """``company stop`` killed every process on the machine with "temporal"
    in its name or command line: other checkouts' Temporal servers and test
    runs of ``tests/temporal``."""
    data = tmp_path.resolve() / ".opencompany"
    worktree_data = tmp_path.resolve() / ".claude" / "worktrees" / "a" / ".opencompany"
    temporal = ["server", "start-dev", "--db-filename"]
    procs = [
        _process(31, "temporal.exe", [str(data / "packages" / "temporal" / "temporal.exe"), *temporal, str(data / "temporal.db")], str(data)),
        _process(32, "temporal.exe", [str(worktree_data / "packages" / "temporal" / "temporal.exe"), *temporal], str(worktree_data)),
        _process(33, "python.exe", ["python", "-m", "pytest", "tests/temporal"], str(tmp_path)),
        _process(34, "temporal.exe", [str(tmp_path.resolve() / ".opencompany-old" / "temporal.exe")], str(tmp_path)),
    ]
    with patch.object(psutil, "process_iter", return_value=procs), patch.object(ports, "_ancestor_pids", return_value=set()):
        assert ports.kill_by_pattern("temporal", within=data) == [31]
    procs[0].kill.assert_called_once()

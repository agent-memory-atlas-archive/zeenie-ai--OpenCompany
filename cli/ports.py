"""Cross-platform port + process killing.

Lifts the helpers from ``scripts/port_kill.py`` so the CLI uses the
same battle-tested ``psutil`` paths that are already in production.
"""

from __future__ import annotations

import os
import shutil
import sys
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from cli.tree import send_ctrl_break


# Post-kill grace before re-checking the port. Windows can lag a few
# hundred ms releasing the listener socket after the bound process
# dies — without this, ``kill_port`` reports the port still in use
# even though the kill succeeded.
_POST_KILL_RECHECK_DELAY = 0.5


@dataclass
class KillResult:
    port: int
    killed_pids: list[int]
    port_free: bool


def _ancestor_pids() -> set[int]:
    """Return PIDs of the current process + every ancestor up the tree.

    Used by the pattern-matching kill functions below so we never
    terminate our own parent / grandparent. When invoked through the
    global bin shim the chain is ``powershell -> bun.exe (bin/cli.js)
    -> python.exe (-m cli ...)`` -- if ``kill_orphaned_opencompany_processes``
    matches ``bun.exe`` (its cmdline carries the install path),
    killing it tears down stdio mid-execution and the Python child
    exits with whatever garbage code Windows assigns to an
    abruptly-orphaned process (observed: 58). Walk up the tree once
    and exclude every ancestor PID.

    Bounded loop (20 hops) defends against process-tree cycles
    (theoretically impossible but cheap insurance).
    """
    pids: set[int] = set()
    try:
        cur: psutil.Process | None = psutil.Process(os.getpid())
        for _ in range(20):
            if cur is None:
                break
            pids.add(cur.pid)
            try:
                cur = cur.parent()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pids.add(os.getpid())
    return pids


def find_pids_by_port(port: int) -> set[int]:
    """Find PIDs with a LISTENING socket on ``port`` via psutil's native APIs.

    Only listeners block a fresh ``bind()``. Half-closed connections left
    by a browser tab after the server dies (``CLOSE_WAIT`` on a dead PID)
    must not count, or ``company stop`` reports a bindable port as in use.
    """
    pids: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.laddr
                and conn.laddr.port == port
                and conn.pid
                and conn.status == psutil.CONN_LISTEN
            ):
                pids.add(conn.pid)
    except psutil.AccessDenied:
        # macOS: net_connections() requires root, fall back to lsof.
        if sys.platform == "darwin":
            try:
                output = subprocess.check_output(
                    ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                for line in output.strip().splitlines():
                    try:
                        pids.add(int(line.strip()))
                    except ValueError:
                        pass
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
    except OSError:
        pass
    return pids


def kill_pid(pid: int, *, graceful_timeout: float = 3.0) -> bool:
    """Terminate ``pid`` gracefully, then force-kill on timeout.

    Windows: send ``CTRL_BREAK_EVENT`` first so daemons spawned with
    ``CREATE_NEW_PROCESS_GROUP`` (the supervisor's children — see
    ``cli/tree.py:new_session_kwargs``) get a real shutdown signal and
    can release listener sockets cleanly. The event only reaches
    processes attached to this console, so anything else (a service
    started in another terminal, an orphan from a previous session) goes
    straight to ``proc.terminate()`` (= ``TerminateProcess``) --
    equivalent to SIGKILL, leaves the OS holding sockets briefly. The
    event is sent by ``cli/tree.py:send_ctrl_break``, never ``os.kill``;
    its docstring says why. Same pattern as ``cli/supervisor.py:_stop_proc``.

    POSIX: plain ``proc.terminate()`` (SIGTERM).
    """
    try:
        proc = psutil.Process(pid)
        if sys.platform != "win32" or not send_ctrl_break(pid):
            proc.terminate()
        try:
            proc.wait(timeout=graceful_timeout)
        except psutil.TimeoutExpired:
            proc.kill()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _is_backend_process(pid: int, root_dir: str | None = None) -> bool:
    """Identify this checkout's uvicorn before granting its longer drain.

    A reserved port can belong to an unrelated program. Match both the app
    invocation and working directory so its generic cleanup grace remains
    short. Unknown/inaccessible processes retain the generic timeout too.
    """
    from cli.platform_ import server_dir

    try:
        proc = psutil.Process(pid)
        argv = proc.cmdline()
        return (
            any(argv[i : i + 3] == ["-m", "uvicorn", "main:app"] for i in range(len(argv)))
            and Path(proc.cwd()).resolve() == server_dir(Path(root_dir) if root_dir else None).resolve()
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def kill_port(port: int, *, backend_graceful_timeout: float | None = None) -> KillResult:
    """Kill anything listening on ``port`` and report whether the port is free.

    Post-kill recheck sleeps ``_POST_KILL_RECHECK_DELAY`` because Windows
    can lag a few hundred ms releasing the listener socket after the
    bound process dies — without this, ``temporal server start-dev``'s
    UI port frequently re-reports as in-use immediately after the
    gRPC port kill, even though the kill on temporal.exe (one process
    binds both ports — per docs.temporal.io/cli/server) succeeded.
    """
    my_pid = os.getpid()
    killed: list[int] = []
    for pid in find_pids_by_port(port):
        if pid == my_pid:
            continue
        grace = 3.0
        if backend_graceful_timeout is not None and _is_backend_process(pid):
            grace = backend_graceful_timeout
        if kill_pid(pid, graceful_timeout=grace):
            killed.append(pid)
    if killed:
        time.sleep(_POST_KILL_RECHECK_DELAY)
    port_free = not find_pids_by_port(port)
    return KillResult(port=port, killed_pids=killed, port_free=port_free)


def _normalized(path: str | os.PathLike[str]) -> str:
    """A path as command lines are compared here: lower case, forward slashes,
    no trailing slash."""
    return os.fspath(path).lower().replace("\\", "/").rstrip("/")


def _command_line(proc: psutil.Process) -> str:
    """``proc``'s command line from ``process_iter``'s cache, normalized like a path."""
    return " ".join(proc.info.get("cmdline") or []).lower().replace("\\", "/")


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _path_ends_at(text: str, end: int) -> bool:
    """Whether a path in ``text`` can end at ``end``: at a separator, at the
    end of an argument, or at the end of the text."""
    return text[end : end + 1] in ("", "/", " ", '"', "'")


@dataclass(frozen=True)
class _Tree:
    """A directory and, when it is a git working tree, the repository's others.

    Paths are :func:`_normalized`. ``others`` comes from git (see
    :func:`_working_tree`): a worktree nested inside ``root``, or beside it,
    is a separate checkout even when its files share ``root``'s prefix.
    """

    root: str
    others: tuple[str, ...] = ()

    def named_in(self, cmd: str) -> bool:
        """Whether a normalized command line names a path in this tree.

        The root has to end at a path boundary, so a sibling folder that only
        shares the prefix (``opencompany-worktrees/...``) does not count, and
        a path inside a worktree nested here belongs to that worktree.
        """
        if not self.root:
            return False
        nested = [tree for tree in self.others if _within(tree, self.root)]
        start = 0
        while (index := cmd.find(self.root, start)) >= 0:
            start = index + 1
            if not _path_ends_at(cmd, index + len(self.root)):
                continue
            if not any(cmd.startswith(tree, index) and _path_ends_at(cmd, index + len(tree)) for tree in nested):
                return True
        return False

    def belongs_elsewhere(self, path: str) -> bool:
        """Whether a normalized ``path`` lies in another of the repository's
        working trees. The deepest working tree containing a path owns it."""
        owners = [tree for tree in (self.root, *self.others) if _within(path, tree)]
        return bool(owners) and max(owners, key=len) != self.root


def _working_tree(root_dir: str | os.PathLike[str]) -> _Tree:
    """``root_dir`` with the repository's other working trees, as git lists them.

    Git, not a folder-naming convention, says which directories are separate
    checkouts: ``git worktree add`` can put one anywhere. There are none
    without git, outside a repository, or when ``root_dir`` is not itself a
    working tree (an installed package unpacked inside some other repository).
    """
    root_path = Path(root_dir).resolve()
    root = _normalized(root_path)
    git = shutil.which("git")
    if git is None:
        return _Tree(root)
    try:
        listing = subprocess.run(
            [git, "-C", str(root_path), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return _Tree(root)
    trees = [_normalized(field.removeprefix("worktree ")) for field in listing.split("\0") if field.startswith("worktree ")]
    if root not in trees:
        return _Tree(root)
    return _Tree(root, tuple(tree for tree in trees if tree != root))


def _works_in_another_tree(proc: psutil.Process, checkout: _Tree) -> bool:
    """Whether ``proc``'s working directory lies in another working tree.

    A test run in a worktree that borrows this checkout's Python names the
    interpreter's path here; its working directory says whose run it is. An
    unreadable working directory leaves the command line to decide.
    """
    try:
        cwd = proc.cwd()
    except psutil.Error:
        return False
    return checkout.belongs_elsewhere(_normalized(cwd))


def kill_by_pattern(pattern: str, *, within: str | os.PathLike[str]) -> list[int]:
    """Kill processes whose name or command line contains ``pattern`` and
    whose command line names a path inside the directory ``within``.

    ``within`` is what keeps the kill to this installation. ``company stop``
    passes its data directory, where the Temporal binary and database live,
    so another checkout's Temporal server, and a test run whose arguments
    merely mention ``temporal``, are left alone.
    """
    pattern_lower = pattern.lower()
    scope = _Tree(_normalized(Path(within).resolve()))
    safe_pids = _ancestor_pids()
    killed: list[int] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = _command_line(proc)
            if pattern_lower not in name and pattern_lower not in cmd:
                continue
            if not scope.named_in(cmd):
                continue
            if proc.pid in safe_pids:
                continue
            proc.kill()
            killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


def kill_orphaned_opencompany_processes(
    root_dir: str, *, exclude_substring: str | None = None,
    backend_graceful_timeout: float | None = None,
) -> list[int]:
    """Kill stray python/bun/node processes that belong to the checkout at ``root_dir``.

    A process belongs to it when its command line names a path in the
    checkout (:meth:`_Tree.named_in`) and it is not working in another of
    the repository's working trees (:func:`_works_in_another_tree`).
    """
    checkout = _working_tree(root_dir)
    # bun runs the JS executor sidecar and the company shim; node is kept
    # so a sidecar left over from a pre-bun install is still reaped.
    target_names = {"python", "python3", "python.exe", "bun", "bun.exe", "node", "node.exe"}
    safe_pids = _ancestor_pids()
    killed: list[int] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name not in target_names:
                continue
            cmd = _command_line(proc)
            if not checkout.named_in(cmd):
                continue
            if exclude_substring and exclude_substring.lower() in cmd:
                continue
            if proc.pid in safe_pids:
                continue
            if _works_in_another_tree(proc, checkout):
                continue
            grace = 2.0
            if backend_graceful_timeout is not None and _is_backend_process(proc.pid, root_dir):
                grace = backend_graceful_timeout
            if kill_pid(proc.pid, graceful_timeout=grace):
                killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


# Deprecated import alias for third-party scripts that imported the old helper.
kill_orphaned_machina_processes = kill_orphaned_opencompany_processes

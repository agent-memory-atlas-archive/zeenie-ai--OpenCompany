"""Desktop-host contract: how a GUI shell owns this backend process.

The desktop app (an Electron shell; any host works the same way) spawns
``python -m uvicorn main:app`` directly from the provisioned venv and needs
three things the CLI supervisor used to provide:

1. **Parent-death detection.** A GUI host can crash or be force-quit
   without ever reaching its own shutdown code. ``OPENCOMPANY_PARENT_PID``
   is polled (with a create-time guard against PID reuse), and when
   ``OPENCOMPANY_DESKTOP_STDIN=1`` a thread also blocks on ``stdin`` — the
   shell keeps the pipe open, so EOF means the parent is gone and is
   noticed within milliseconds rather than a poll interval.
2. **Graceful stop from a host that cannot send CTRL_BREAK.** Node's
   ``process.kill`` cannot deliver ``CTRL_BREAK_EVENT`` on Windows, so
   ``POST /api/desktop/shutdown`` (``routers/desktop.py``, token-gated by
   ``OPENCOMPANY_DESKTOP_TOKEN``) calls :func:`request_shutdown`, which
   raises the signal uvicorn already handles. The normal lifespan teardown
   then runs: shutdown hooks, process service, every registered supervisor
   (Temporal dev server, JS executor sidecar on bun, WhatsApp bridge) via
   ``terminate_then_kill``.
3. **No orphans when the backend dies hard.** On Windows the backend puts
   *itself* into a Job Object with ``KILL_ON_JOB_CLOSE``; children inherit
   membership, so if this process is terminated by any means the kernel
   kills Temporal / the bun sidecar / edgymeow with it. Done with ``ctypes`` because
   the server venv deliberately has no ``pywin32`` (that is a CLI-side
   dependency).

Everything here is inert unless ``OPENCOMPANY_DESKTOP=1``. The lifespan
calls :func:`start_desktop_mode` once; the shutdown path is shared with
the watchdogs so there is exactly one way this process asks itself to
exit. A hard deadline (:data:`SHUTDOWN_DEADLINE_SECONDS`) backs the
graceful path: if the lifespan has not finished by then, the process
tree is killed and we ``os._exit``.

Locked by ``tests/test_desktop_watchdog.py``,
``tests/test_desktop_shutdown_endpoint.py`` and (Windows only)
``tests/test_job_object.py``. Contract doc:
``docs-internal/desktop_host_contract.md``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time
from typing import Callable, Optional

from core.logging import get_logger

logger = get_logger(__name__)

ENV_FLAG = "OPENCOMPANY_DESKTOP"
ENV_PARENT_PID = "OPENCOMPANY_PARENT_PID"
ENV_TOKEN = "OPENCOMPANY_DESKTOP_TOKEN"
ENV_STDIN_WATCHDOG = "OPENCOMPANY_DESKTOP_STDIN"

# Parent liveness poll cadence. The stdin watchdog is the fast path; this
# is the backstop for a host that did not hand us a stdin pipe.
PARENT_POLL_SECONDS = 2.0

# Time the graceful lifespan teardown gets before the deadline thread
# tree-kills our children and exits. It covers the allowance the CLI gives
# the backend (``backend_shutdown_grace_seconds`` in ``cli/_common.py``) at
# the ``TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS=10`` the shell sets: 5 s of
# connection drain, 10 s for each of the three Temporal stops (worker pool,
# manager worker, dev server) and 30 s for the rest. The shell waits longer
# before its own tree-kill, so the backend finishes or kills its own tree
# first; ``cli/tests/test_backend_shutdown.py`` locks that order.
SHUTDOWN_DEADLINE_SECONDS = 65.0

_shutdown_requested = False
_job_handle: Optional[int] = None


def is_desktop_mode() -> bool:
    return os.environ.get(ENV_FLAG, "").strip() == "1"


def desktop_token() -> str:
    """The per-launch shared secret the shell must present to ``/api/desktop/*``."""
    return os.environ.get(ENV_TOKEN, "").strip()


def parent_pid() -> Optional[int]:
    raw = os.environ.get(ENV_PARENT_PID, "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def _shutdown_signal() -> int:
    # uvicorn handles SIGINT + SIGTERM everywhere and SIGBREAK on Windows
    # (``uvicorn.server.HANDLED_SIGNALS``). SIGTERM does not exist as a
    # deliverable signal on Windows, so raise SIGINT there.
    return signal.SIGINT if sys.platform == "win32" else signal.SIGTERM


def _deadline_thread(reason: str) -> None:
    time.sleep(SHUTDOWN_DEADLINE_SECONDS)
    # Still alive: the graceful path wedged. Take the children with us so
    # the shell never has to hunt for orphans.
    try:
        from services._supervisor.util import kill_tree

        kill_tree(os.getpid())
    except Exception:  # noqa: BLE001 — best effort on the way out
        pass
    os._exit(1)


def request_shutdown(reason: str) -> None:
    """Ask this process to exit the way Ctrl+C would.

    Idempotent. Must be called from the main thread (the asyncio loop
    thread) so the Python-level signal handler runs immediately; thread
    callers go through ``loop.call_soon_threadsafe``.
    """
    global _shutdown_requested
    if _shutdown_requested:
        return
    _shutdown_requested = True
    logger.warning("Desktop host: shutting down (%s)", reason)
    threading.Thread(
        target=_deadline_thread,
        args=(reason,),
        name="desktop-shutdown-deadline",
        daemon=True,
    ).start()
    try:
        signal.raise_signal(_shutdown_signal())
    except Exception as exc:  # noqa: BLE001 — fall back to a hard exit
        logger.error("Desktop host: raise_signal failed (%s); exiting hard", exc)
        os._exit(1)


def shutdown_requested() -> bool:
    return _shutdown_requested


# ---------------------------------------------------------------------------
# Parent watchdogs
# ---------------------------------------------------------------------------


def _parent_create_time(pid: int) -> Optional[float]:
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001 — NoSuchProcess / AccessDenied / no psutil
        return None


def parent_alive(pid: int, create_time: Optional[float]) -> bool:
    """True while ``pid`` exists AND is the same process we started under.

    The create-time comparison is the PID-reuse guard: on a busy machine a
    dead parent's PID can be handed to an unrelated process within the
    poll interval.
    """
    try:
        import psutil

        if not psutil.pid_exists(pid):
            return False
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        if create_time is not None:
            now = proc.create_time()
            if abs(now - create_time) > 1.0:
                return False
        return True
    except Exception:  # noqa: BLE001 — treat any failure to inspect as dead
        return False


async def watch_parent(pid: int, *, interval: float = PARENT_POLL_SECONDS) -> None:
    """Poll the host process; request shutdown the moment it disappears."""
    create_time = _parent_create_time(pid)
    logger.info("Desktop host: watching parent pid %s", pid)
    while True:
        await asyncio.sleep(interval)
        if sys.platform != "win32" and os.getppid() != pid and os.getppid() == 1:
            request_shutdown("parent process exited (reparented to init)")
            return
        if not parent_alive(pid, create_time):
            request_shutdown(f"parent process {pid} exited")
            return


STDIN_POLL_SECONDS = 0.5


def _stdin_is_pipe(fd: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFileType.restype = wintypes.DWORD
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        FILE_TYPE_PIPE = 0x0003
        return kernel32.GetFileType(wintypes.HANDLE(msvcrt.get_osfhandle(fd))) == FILE_TYPE_PIPE
    import stat

    try:
        return stat.S_ISFIFO(os.fstat(fd).st_mode) or stat.S_ISSOCK(os.fstat(fd).st_mode)
    except OSError:
        return False


def wait_for_stdin_eof(fd: int, *, poll: float = STDIN_POLL_SECONDS, stop: Callable[[], bool] | None = None) -> bool:
    """Block until ``fd`` (a pipe) reaches EOF or ``stop()`` turns true.

    Deliberately NOT a blocking ``read()``: a daemon thread parked inside a
    Windows ``ReadFile`` while the interpreter finalises crashes the exiting
    process with an access violation (observed as exit code 0xC0000005 on
    every shutdown that did not come from stdin itself). Polling with
    ``PeekNamedPipe`` / ``select`` lets the thread notice the shutdown flag
    and return before finalisation. Returns True on EOF, False if stopped.
    """
    stop = stop or shutdown_requested
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.PeekNamedPipe.restype = wintypes.BOOL
        kernel32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
        ]
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        available = wintypes.DWORD(0)
        while not stop():
            ok = kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None)
            if not ok:
                # ERROR_BROKEN_PIPE (109) once the writer is gone; any other
                # failure also means we can no longer see the parent.
                return True
            if available.value:
                try:
                    if not os.read(fd, available.value):
                        return True
                except OSError:
                    return True
            time.sleep(poll)
        return False
    import select

    while not stop():
        try:
            readable, _, _ = select.select([fd], [], [], poll)
        except (OSError, ValueError):
            return True
        if readable:
            try:
                if not os.read(fd, 4096):
                    return True
            except OSError:
                return True
    return False


def _stdin_watchdog(loop: asyncio.AbstractEventLoop, fd: int) -> None:
    if wait_for_stdin_eof(fd):
        loop.call_soon_threadsafe(request_shutdown, "stdin closed by parent")


def start_stdin_watchdog(loop: asyncio.AbstractEventLoop, fd: Optional[int] = None) -> Optional[threading.Thread]:
    """Watch stdin (must be a pipe the shell holds open) for EOF.

    Returns None without starting anything when stdin is not a pipe — a
    terminal or ``/dev/null`` would otherwise read as an instant "parent
    gone" (console handles fail ``PeekNamedPipe`` immediately).
    """
    if fd is None:
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            logger.warning("Desktop host: stdin has no file descriptor; stdin watchdog not started")
            return None
    if not _stdin_is_pipe(fd):
        logger.warning("Desktop host: stdin is not a pipe; stdin watchdog not started")
        return None
    thread = threading.Thread(target=_stdin_watchdog, args=(loop, fd), name="desktop-stdin-watchdog", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Windows Job Object (children die with us)
# ---------------------------------------------------------------------------


def install_job_object() -> bool:
    """Put the current process into a kill-on-close Job Object (Windows).

    Returns True when this process is now in a job that will terminate its
    descendants when the last handle closes (i.e. when we die). No-op and
    False on other platforms; False with a logged warning if the OS
    refuses (for example when the CLI supervisor already placed us in a
    non-nestable job on a pre-Windows-8 kernel).
    """
    global _job_handle
    if sys.platform != "win32":
        return False
    if _job_handle is not None:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

        # Keep the handle for the process lifetime: closing it is what
        # triggers KILL_ON_JOB_CLOSE, and the kernel closes it for us on exit.
        _job_handle = int(handle)
        logger.info("Desktop host: process enrolled in kill-on-close Job Object")
        return True
    except Exception as exc:  # noqa: BLE001 — degrade to the tree-kill fallbacks
        logger.warning("Desktop host: Job Object unavailable (%s); relying on graceful teardown + shell tree-kill", exc)
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start_desktop_mode(loop: asyncio.AbstractEventLoop) -> dict[str, bool]:
    """Arm every desktop-mode mechanism. Call once from the lifespan.

    Returns which mechanisms were armed, for the startup log.
    """
    armed = {"job_object": False, "parent_watchdog": False, "stdin_watchdog": False}
    if not is_desktop_mode():
        return armed
    armed["job_object"] = install_job_object()
    pid = parent_pid()
    if pid is not None:
        loop.create_task(watch_parent(pid), name="desktop-parent-watchdog")
        armed["parent_watchdog"] = True
    if os.environ.get(ENV_STDIN_WATCHDOG, "").strip() == "1":
        armed["stdin_watchdog"] = start_stdin_watchdog(loop) is not None
    if not desktop_token():
        logger.warning("Desktop host: %s is unset; /api/desktop/shutdown will refuse every request", ENV_TOKEN)
    logger.info("Desktop host mode armed", **armed)
    return armed


__all__ = [
    "ENV_FLAG",
    "ENV_PARENT_PID",
    "ENV_TOKEN",
    "ENV_STDIN_WATCHDOG",
    "SHUTDOWN_DEADLINE_SECONDS",
    "is_desktop_mode",
    "desktop_token",
    "parent_pid",
    "parent_alive",
    "watch_parent",
    "wait_for_stdin_eof",
    "start_stdin_watchdog",
    "install_job_object",
    "request_shutdown",
    "shutdown_requested",
    "start_desktop_mode",
]

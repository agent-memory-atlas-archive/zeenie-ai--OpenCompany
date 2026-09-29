"""Cross-platform helpers for supervised binaries.

Stdlib + psutil only — no exotic abstractions. These are extracted from
the patterns already in production use across browser_service.py,
process_service.py, and the WhatsApp runtime, so behaviour is unchanged
when subclasses adopt the supervisor base.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Callable, Optional

import anyio
import psutil


def kill_tree(pid: int) -> None:
    """Kill a process and all descendants. Cross-platform via psutil.

    Defensively guards every psutil call against ``NoSuchProcess`` to
    survive races with fast-exiting children. Used by process_service,
    browser_service, and BaseProcessSupervisor for tree termination.
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass


def _console_process_ids() -> set[int]:
    """PIDs attached to this process's console; empty when it has none (Windows only)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetConsoleProcessList.restype = wintypes.DWORD
    kernel32.GetConsoleProcessList.argtypes = [wintypes.LPDWORD, wintypes.DWORD]
    ids = (wintypes.DWORD * 1)()
    while True:
        # 0 without a console; a count larger than the buffer is the size it needs.
        count = kernel32.GetConsoleProcessList(ids, len(ids))
        if count <= len(ids):
            return set(ids[:count])
        ids = (wintypes.DWORD * count)()


def send_ctrl_break(pid: int) -> bool:
    """Send ``CTRL_BREAK_EVENT`` to the process group ``pid`` leads (Windows only).

    Returns whether the event went out: False on other platforms, when
    ``pid`` is not attached to this process's console (Windows delivers
    console events only within one console), and when Windows refuses it.
    Calls ``GenerateConsoleCtrlEvent`` rather than ``os.kill``, which before
    CPython 3.12.9 and 3.13.2 (gh-58689) turns a refused event into a
    ``TerminateProcess`` hard kill and returns with the ``OSError`` still
    set, so a ``SystemError`` surfaces later from an unrelated call. The CLI
    has the same helper in ``cli/tree.py``; the server cannot import the CLI
    package and has no pywin32.
    https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent
    """
    if sys.platform != "win32" or pid not in _console_process_ids():
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    kernel32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    return bool(kernel32.GenerateConsoleCtrlEvent(signal.CTRL_BREAK_EVENT, pid))


async def terminate_then_kill(
    proc: anyio.abc.Process,
    *,
    grace: float = 5.0,
    use_ctrl_break: bool = False,
) -> None:
    """Send a graceful stop, wait ``grace`` seconds, then tree-kill.

    POSIX path: ``proc.terminate()`` (SIGTERM), wait, ``kill_tree()``.
    Windows graceful path (``use_ctrl_break=True``, requires the process
    to have been spawned with ``CREATE_NEW_PROCESS_GROUP``):
    ``CTRL_BREAK_EVENT`` via :func:`send_ctrl_break` — the only way to send
    a real SIGINT-equivalent on Windows.
    """
    if proc.returncode is not None:
        return

    if use_ctrl_break and sys.platform == "win32":
        # When the event does not go out, the grace wait ends in the tree-kill below.
        send_ctrl_break(proc.pid)
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    with anyio.move_on_after(grace):
        await proc.wait()

    if proc.returncode is None:
        kill_tree(proc.pid)
        await proc.wait()


async def drain_stream(
    stream: Optional[anyio.abc.ByteReceiveStream],
    log_fn: Callable[[str], None],
    *,
    prefix: str = "",
) -> None:
    """Forward subprocess output line-by-line to a logger callable.

    Safe against cancellation and closed streams. ``log_fn`` is something
    like ``logger.info`` or ``logger.error``. Lines are decoded as UTF-8
    with ``errors="replace"`` so binary garbage never crashes the drain.
    """
    if stream is None:
        return
    buf = b""
    try:
        async for chunk in stream:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log_fn(f"{prefix}{text}" if prefix else text)
        if buf:
            text = buf.decode("utf-8", errors="replace").rstrip()
            if text:
                log_fn(f"{prefix}{text}" if prefix else text)
    except (anyio.ClosedResourceError, anyio.EndOfStream, asyncio.CancelledError):
        pass
    except Exception as exc:  # pragma: no cover — defensive
        logging.getLogger(__name__).debug("drain_stream ended: %s", exc)

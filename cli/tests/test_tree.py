"""Tests for ``cli.tree``."""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from cli import tree

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="console control events exist only on Windows")


def _sleeper(creationflags: int) -> subprocess.Popen:
    # The base interpreter: a venv launcher would add a process of its own.
    return subprocess.Popen(
        [sys._base_executable, "-c", "import time; time.sleep(30)"],
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_send_ctrl_break_is_windows_only():
    with patch.object(tree.sys, "platform", "linux"):
        assert tree.send_ctrl_break(123) is False


@windows_only
def test_send_ctrl_break_leaves_a_process_outside_this_console_alone():
    """Windows cannot deliver the event outside this console. ``os.kill`` turned
    that refusal into a hard kill and a pending ``OSError`` on Python 3.12.8."""
    sleeper = _sleeper(subprocess.DETACHED_PROCESS)
    try:
        assert tree.send_ctrl_break(sleeper.pid) is False
        with pytest.raises(subprocess.TimeoutExpired):
            sleeper.wait(timeout=0.5)
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


@windows_only
def test_send_ctrl_break_stops_a_process_group_on_this_console():
    pywintypes = pytest.importorskip("pywintypes")
    win32console = pytest.importorskip("win32console")
    try:
        win32console.GetConsoleProcessList()
    except pywintypes.error:
        pytest.skip("this test process has no console")
    sleeper = _sleeper(subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        deadline = time.monotonic() + 10
        while sleeper.pid not in win32console.GetConsoleProcessList():
            assert time.monotonic() < deadline, "the sleeper never attached to this console"
            time.sleep(0.05)
        assert tree.send_ctrl_break(sleeper.pid) is True
        sleeper.wait(timeout=10)  # long before its own 30 s sleep ends
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=10)

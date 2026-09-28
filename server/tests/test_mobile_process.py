"""Streaming installer subprocess contracts, without launching an installer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodes.mobile import _process as module


class Process:
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.stdin = SimpleNamespace(write=lambda _: None, drain=AsyncMock(), close=lambda: None)
        self.returncode = None
        self.done = asyncio.Event()
        self.killed = False

    async def wait(self):
        await self.done.wait()
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.stdout.feed_eof()
        self.done.set()


async def test_reports_carriage_return_progress_before_process_exits(monkeypatch):
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    events = []
    received = asyncio.Event()

    def progress(line):
        events.append(line)
        received.set()

    task = asyncio.create_task(module.command(["sdkmanager"], progress=progress))
    process.stdout.feed_data(b"Downloading 10%\rDownloading 20%\r\n")
    await asyncio.wait_for(received.wait(), 1)
    assert not task.done()
    process.stdout.feed_data("Extracting café".encode()[:-1])
    await asyncio.sleep(0)
    process.stdout.feed_data("Extracting café".encode()[-1:])
    process.stdout.feed_eof()
    process.done.set()
    output = await task
    assert events == ["Downloading 10%", "Downloading 20%", "Extracting café"]
    assert "Downloading 10%" in output


async def test_output_and_unterminated_progress_are_bounded(monkeypatch):
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    process.stdout.feed_data(b"old-output\n" + b"x" * (module.OUTPUT_TAIL_BYTES * 3) + b"last-output")
    process.stdout.feed_eof()
    process.done.set()
    lines = []
    output = await module.command(["sdkmanager"], progress=lines.append)
    assert len(output.encode()) == module.OUTPUT_TAIL_BYTES
    assert output.endswith("last-output") and "old-output" not in output
    assert max(map(len, lines)) <= 4096


async def test_timeout_kills_waits_and_explains_last_progress(monkeypatch):
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    process.stdout.feed_data(b"Downloading image: 42%\r")
    with pytest.raises(TimeoutError, match="timed out after.*retry setup.*42%"):
        await module.command(["sdkmanager"], timeout=0.02)
    assert process.killed and process.returncode == 0


async def test_cancelled_command_reaps_process(monkeypatch):
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    entered = asyncio.Event()
    task = asyncio.create_task(module.command(["sdkmanager"], progress=lambda _: entered.set()))
    process.stdout.feed_data(b"working\r")
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.returncode == 0

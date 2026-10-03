"""The chat run watchdog (services/chat/watchdog.py): sweeps at once when it
starts and then every interval, survives a failing sweep, stops cleanly, and
asks Temporal whether a run's workflow has closed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.chat import watchdog


async def test_it_sweeps_at_once_and_keeps_sweeping_after_a_failure(monkeypatch):
    sweeps = []

    async def sweep(database, *, temporal_status):
        sweeps.append((database, temporal_status))
        if len(sweeps) == 1:
            raise RuntimeError("database is locked")
        return []

    monkeypatch.setattr(watchdog.ledger, "sweep", sweep)
    dog = watchdog.ChatRunWatchdog("db", interval=0.01)
    dog.start()
    dog.start()  # starting twice runs one loop
    for _ in range(50):
        if len(sweeps) >= 3:
            break
        await asyncio.sleep(0.01)
    await dog.stop()
    await dog.stop()  # stopping twice is harmless
    assert len(sweeps) >= 3
    assert all(database == "db" and status is watchdog.temporal_workflow_closed for database, status in sweeps)
    count = len(sweeps)
    await asyncio.sleep(0.05)
    assert len(sweeps) == count, "the loop kept running after stop"


def _container(monkeypatch, client):
    import core.container as container_module

    wrapper = SimpleNamespace(client=client) if client is not None else None
    monkeypatch.setattr(container_module, "container", SimpleNamespace(temporal_client=lambda: wrapper))


async def test_without_temporal_the_answer_is_unknown(monkeypatch):
    _container(monkeypatch, None)
    assert await watchdog.temporal_workflow_closed("w", "r") is None


@pytest.mark.parametrize(("status", "closed"), [("RUNNING", False), ("COMPLETED", True), ("TERMINATED", True), ("CANCELED", True)])
async def test_a_workflow_is_closed_once_it_stops_running(monkeypatch, status, closed):
    from temporalio.client import WorkflowExecutionStatus

    asked = []

    class Handle:
        async def describe(self):
            return SimpleNamespace(status=getattr(WorkflowExecutionStatus, status))

    class Client:
        def get_workflow_handle(self, workflow_id, run_id=None):
            asked.append((workflow_id, run_id))
            return Handle()

    _container(monkeypatch, Client())
    assert await watchdog.temporal_workflow_closed("w", "r") is closed
    assert asked == [("w", "r")]


async def test_a_workflow_temporal_no_longer_knows_is_closed(monkeypatch):
    from temporalio.service import RPCError, RPCStatusCode

    class Handle:
        def __init__(self, status):
            self.status = status

        async def describe(self):
            raise RPCError("nope", self.status, b"")

    class Client:
        status = RPCStatusCode.NOT_FOUND

        def get_workflow_handle(self, workflow_id, run_id=None):
            return Handle(self.status)

    client = Client()
    _container(monkeypatch, client)
    assert await watchdog.temporal_workflow_closed("w", "r") is True
    client.status = RPCStatusCode.UNAVAILABLE
    assert await watchdog.temporal_workflow_closed("w", "r") is None

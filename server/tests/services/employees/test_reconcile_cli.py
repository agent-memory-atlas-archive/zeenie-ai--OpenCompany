"""The maintenance command reports NDJSON without invoking app startup."""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.employees import reconcile_jobs as reconcile


@pytest.fixture
def command(monkeypatch):
    import core.config
    import core.database
    import sqlalchemy.ext.asyncio
    from temporalio.client import Client

    database = SimpleNamespace(startup=AsyncMock())
    engine = SimpleNamespace(dispose=AsyncMock())
    client = object()
    connect = AsyncMock(return_value=client)
    repair = AsyncMock(return_value=[])
    monkeypatch.setattr(core.config, "Settings", lambda: SimpleNamespace(
        database_url="sqlite+aiosqlite:///:memory:",
        temporal_server_address="unused", temporal_namespace="default"))
    monkeypatch.setattr(core.database, "Database", lambda _: database)
    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(sqlalchemy.ext.asyncio, "async_sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(Client, "connect", connect)
    monkeypatch.setattr(reconcile, "reconcile_jobs", repair)
    return SimpleNamespace(database=database, engine=engine, client=client, connect=connect, repair=repair,
        arguments=argparse.Namespace(workflow_id="graph", job_id=None, apply=False))


@pytest.mark.parametrize("decisions", [[], [
    {"workflow_id": "graph", "job_id": "job", "decision": "skip", "reason": "generation_paused_or_changed"},
]])
async def test_command_emits_parseable_reports_and_remains_dry_run(command, decisions, capsys):
    command.repair.return_value = decisions

    assert await reconcile._run_cli(command.arguments) == 0

    lines = capsys.readouterr().out.splitlines()
    reports = [json.loads(line) for line in lines]
    assert reports == (decisions or [{"workflow_id": "graph", "job_id": None,
        "decision": "skip", "reason": "no_matching_jobs"}])
    command.repair.assert_awaited_once_with(command.database, command.client,
        workflow_id="graph", job_id=None, apply=False)
    command.database.startup.assert_not_awaited()
    command.engine.dispose.assert_awaited_once()


async def test_command_failure_reports_only_category_and_disposes(command, capsys):
    command.connect.side_effect = RuntimeError("PRIVATE EMAIL CONTENT")

    assert await reconcile._run_cli(command.arguments) == 1

    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert json.loads(output) == {"workflow_id": "graph", "job_id": None,
        "decision": "skip", "reason": "connection_or_database_unavailable"}
    command.repair.assert_not_awaited()
    command.database.startup.assert_not_awaited()
    command.engine.dispose.assert_awaited_once()

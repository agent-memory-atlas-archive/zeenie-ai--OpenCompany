r"""Read-only diagnostic for #145; outbound node execution is intercepted.

Run from server/: .venv\Scripts\python.exe -m pytest -c pyproject.toml -p tests.conftest
    ../.tmp_issue_validation/test_employee_stall.py -q -p no:cacheprovider
The real Database session/task-query methods use an isolated SQLite database.
No LLM, Temporal server, credentials, approval or Gmail service is contacted.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from models.database import AgentTeam, TeamTask
from models.employees import EmployeeJob
from services.employees.jobs import bind_job_team, deliver_job, job_progress
from tests.services.employees.test_jobs import context, install, saved


@pytest.fixture
async def isolated_database():
    name = "tests._employee_stall_db_" + uuid4().hex
    source = Path(__file__).resolve().parents[1] / "server/core/database.py"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    database = module.Database(SimpleNamespace())
    database.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    database.async_session = async_sessionmaker(database.engine, class_=AsyncSession, expire_on_commit=False)
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: SQLModel.metadata.create_all(
            sync, tables=[EmployeeJob.__table__, AgentTeam.__table__, TeamTask.__table__]
        ))
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(name, None)


@pytest.fixture
def intercepted_delivery(isolated_database, monkeypatch):
    execute = AsyncMock(return_value={"success": True, "result": {"approved": True}})
    monkeypatch.setattr("core.container.container", SimpleNamespace(
        database=lambda: isolated_database,
        workflow_service=lambda: SimpleNamespace(execute_node=execute),
    ))
    return execute


async def test_zero_tasks_and_lead_final_draft_persist_working_without_approval(
    isolated_database, intercepted_delivery,
):
    database = isolated_database
    await install(database, sink="gmailSend")
    async with database.reserved_session() as session:
        job = await session.get(EmployeeJob, "job")
        job.team_id = None
        job.state = "running"
        session.add(AgentTeam(id="team", workflow_id="7", team_lead_node_id="lead"))
        await session.commit()
    await bind_job_team(database, workflow_id="7", lead_node_id="lead", execution_id="origin", team_id="team")
    assert await database.get_team_tasks("team") == []
    assert (await saved(database)).state == "working"

    result = await deliver_job(database, context("Here is my drafted email reply."), lead_node_id="lead")

    assert result == {"delivered": False, "state": "working", "job_id": "job"}
    job = await saved(database)
    assert job.state == "working"
    assert job.result is None
    assert job.delivery["inflight"] is None
    assert job.delivery["completed"] == []
    assert (await job_progress(database, "7"))["state"] == "working"
    intercepted_delivery.assert_not_awaited()


async def test_accepted_task_control_enters_approval_then_gmail_and_finishes(
    isolated_database, intercepted_delivery,
):
    database = isolated_database
    await install(database, sink="gmailSend")
    async with database.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="7", team_lead_node_id="lead"))
        session.add(TeamTask(id="accepted-task", team_id="team", title="Draft reply",
            created_by="lead", assigned_to="specialist", workflow_id="7", status="accepted"))
        await session.commit()
    assert [task["status"] for task in await database.get_team_tasks("team")] == ["accepted"]

    result = await deliver_job(database, context("The reviewed email reply."), lead_node_id="lead")

    assert result == {"delivered": True, "state": "delivered", "job_id": "job"}
    assert (await saved(database)).state == "delivered"
    assert [call.kwargs["node_type"] for call in intercepted_delivery.await_args_list] == ["approvalGate", "gmailSend"]

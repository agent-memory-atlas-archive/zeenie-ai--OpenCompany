r"""Read-only diagnostics for issue #144; no product code or external services.

Run from server/:
  .\.venv\Scripts\python.exe -m pytest -q -s -o asyncio_mode=auto -p tests.conftest \
      ../.tmp_issue_validation/test_long_email.py -p no:cacheprovider

tests.conftest isolates app/container imports. Gmail decoding, parameter
resolution, employee parameter validation, v2 admission, job creation and
SQLite persistence below use the actual implementations.
"""
from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models.employees import Employee, EmployeeJob
from nodes.google._gmail import format_message
from nodes.workflow.employee_job import EmployeeJobParams
from services.employees.builder import build_employee_graph
from services.employees.team_runtime import admit_employee_runtime_job
from services.parameter_resolver import ParameterResolver
from tests.services.employees.test_graph_builder import hire, inputs
from tests.services.employees.test_jobs import database  # noqa: F401


def gmail_team():
    request = hire(
        name="Leo", role="Inbox assistant", apps=["Gmail"],
        job="Read new email, answer what you can and flag the rest",
        steps=[{"title": "Read new email", "role": "trigger", "app": "Gmail"}],
        trigger={"kind": "app_event", "app": "Gmail"},
    )
    return build_employee_graph(inputs(request, "gmail", connected={"gmail"}, team=True))


def email_event(body: str):
    return format_message({
        "id": "long-message", "threadId": "email-thread",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "Subject", "value": "Promotion"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }, include_body=True)


async def resolved_mission(built, event):
    async def absent_output(*_args):
        return {}

    # Recipe v1 assigned this same trigger-derived prompt to intake.mission.
    lead = built.node_roles["agent"]
    parameters = await ParameterResolver(None, absent_output).resolve(
        {"mission": built.parameters[lead]["prompt"]}, lead,
        built.nodes, built.edges, "diagnostic-scope",
        run_outputs={built.node_roles["trigger"]: event},
    )
    return parameters["mission"]


async def test_reported_86310_character_body_reproduces_v1_cap():
    built = gmail_team()
    body = "<p>" + "x" * 86303 + "</p>"
    event = email_event(body)
    mission = await resolved_mission(built, event)

    assert len(body) == len(event["body"]) == 86310
    assert mission.endswith(body)
    assert len(mission) == 86364
    with pytest.raises(ValidationError) as caught:
        EmployeeJobParams(operation="intake", mission=mission)
    error = caught.value.errors()[0]
    assert error["loc"] == ("mission",)
    assert error["type"] == "string_too_long"
    assert error["msg"] == "String should have at most 20000 characters"
    print(f"#144 v1: body={len(body)}, mission={len(mission)}, error={error['msg']}")


@pytest.mark.parametrize("mission_length", [19999, 20000, 20001])
async def test_v1_limit_applies_to_entire_resolved_mission(mission_length):
    built = gmail_team()
    prefix_length = len(await resolved_mission(built, email_event("")))
    body = "x" * (mission_length - prefix_length)
    mission = await resolved_mission(built, email_event(body))
    assert len(mission) == mission_length
    if mission_length <= 20000:
        assert EmployeeJobParams(operation="intake", mission=mission).mission == mission
    else:
        with pytest.raises(ValidationError, match="at most 20000"):
            EmployeeJobParams(operation="intake", mission=mission)
    print(f"#144 v1 boundary: mission={mission_length}, admitted={mission_length <= 20000}")


async def test_current_v2_admission_persists_same_long_mission_in_sqlite(database):
    built = gmail_team()
    assert built.team_plan["version"] == 2
    assert not any(node["type"] == "employeeJob" for node in built.nodes)
    lead, trigger = built.node_roles["agent"], built.node_roles["trigger"]
    assert any(edge["source"] == trigger and edge["target"] == lead for edge in built.edges)
    event = email_event("<p>" + "x" * 86303 + "</p>")
    mission = await resolved_mission(built, event)
    assert await database.save_workflow(
        "7", "Long email diagnostic", "long-email-diagnostic",
        {"graphVersion": 2, "owner_id": "owner", "nodes": built.nodes, "edges": built.edges},
    )
    async with database.reserved_session() as session:
        session.add(Employee(
            id="long-email-employee", workflow_id="7", owner_id="owner",
            hire_state="ready", team_plan=built.team_plan, node_roles=built.node_roles,
        ))
        await session.commit()

    context = {
        "workflow_id": "7", "node_id": lead, "execution_id": "long-email-origin",
        "nodes": built.nodes, "edges": built.edges, "generation": 1,
        "session_id": "7", "data_scope_id": "diagnostic-scope", "user_id": "owner",
        "parameter_snapshot": built.parameters, "inputs": {trigger: event},
    }
    # This calls the actual create_job and commits an EmployeeJob row.
    await admit_employee_runtime_job(database, context, built.team_plan, mission)
    async with database.get_session() as session:
        stored = await session.get(EmployeeJob, context["employee_job_id"])
        assert stored is not None
        assert stored.mission == mission
        assert stored.source["outputs"][trigger]["body"] == event["body"]
        assert stored.state == "running"
        print(f"#144 current v2 SQLite: admitted and persisted mission={len(stored.mission)}")


def test_postgresql_schema_evidence_only():
    ddl = str(CreateTable(EmployeeJob.__table__).compile(dialect=postgresql.dialect()))
    assert "mission VARCHAR(20000) NOT NULL" in ddl
    print("#144 PostgreSQL: schema still has mission VARCHAR(20000); no live PostgreSQL reproduction")

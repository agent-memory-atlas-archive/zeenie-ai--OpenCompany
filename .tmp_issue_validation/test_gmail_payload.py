"""Read-only diagnostic for GitHub #143; these assert observed behavior.

The controller, listener, graph construction, Gmail formatter, starter
substitution, and parameter resolver are real. Only Temporal runtime/I/O
entry points are replaced. No Gmail server, real child workflow, LLM, or
approval queue is exercised.

Run from server/ (the shared test bootstrap stubs unrelated application DI):
.venv/Scripts/python.exe -m pytest ../.tmp_issue_validation/test_gmail_payload.py -q -s -p tests.conftest -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import base64
import copy
import functools
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

from nodes.google._events import gmail_message_received
from nodes.google._gmail import format_message
from services.employees.builder import _substitute
from services.parameter_resolver import ParameterResolver
from temporalio import workflow


@functools.lru_cache
def historical_source(revision: str, stem: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{revision}:server/services/temporal/{stem}.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )


def load_temporal_modules(revision: str, monkeypatch):
    """Use exact historical workflow source without changing the checkout."""
    package = importlib.import_module("services.temporal")
    stems = (
        "trigger_listener_workflow",
        "polling_trigger_workflow",
        "workflow_control_workflow",
    )
    current = {
        stem: importlib.import_module(f"services.temporal.{stem}")
        for stem in stems
    }
    if revision == "working":
        return current
    loaded = {}
    for stem in stems:
        name = f"services.temporal.{stem}"
        module = types.ModuleType(name)
        module.__package__ = "services.temporal"
        module.__file__ = f"git:{revision}:server/services/temporal/{stem}.py"
        monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr(package, stem, module)
        exec(compile(historical_source(revision, stem), module.__file__, "exec"), module.__dict__)
        loaded[stem] = module
    return loaded


def gmail_poll_result():
    """Use the real Gmail formatter, then the polling activity's stable ID."""
    body = "Please reply to this email."
    raw = format_message(
        {
            "id": "gmail-test",
            "threadId": "gmail-thread",
            "snippet": body,
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Reply requested"},
                ],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
            },
        },
        include_body=True,
    )
    raw.setdefault("id", raw["message_id"])
    assert "data" not in raw
    return raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revision,control_version,queue_patch,enveloped,expect_loss",
    [
        ("d36f7db", 0, True, False, True),
        ("d36f7db", 0, True, True, False),
        ("d36f7db", 0, False, False, False),
        ("working", 0, True, False, True),
        ("working", 0, True, True, False),
        ("working", 0, False, False, False),
        ("working", 1, True, False, True),
        ("working", 1, True, True, False),
    ],
)
async def test_controller_poll_to_child_to_mission(
    monkeypatch, revision, control_version, queue_patch, enveloped, expect_loss
):
    modules = load_temporal_modules(revision, monkeypatch)
    controller = modules["workflow_control_workflow"].WorkflowControlWorkflow()
    email = gmail_poll_result()
    event = gmail_message_received(email).model_dump(mode="json") if enveloped else email
    captured = []
    polls = []
    completed = asyncio.Event()

    async def fake_wait(predicate):
        while not predicate():
            await asyncio.sleep(0)

    async def fake_sleep(_duration):
        await asyncio.sleep(0)

    async def fake_activity(name, payload, **_options):
        if name == "poll.googleGmailReceive.v1":
            polls.append(copy.deepcopy(payload))
            if payload["baseline_only"]:
                return {"events": [], "seen_ids": ["old-gmail"]}
            if len(polls) == 2:
                return {"events": [copy.deepcopy(event)], "seen_ids": ["old-gmail", "gmail-test"]}
            await completed.wait()
            return {"events": [], "seen_ids": ["old-gmail", "gmail-test"]}
        if name == "evaluate_trigger_filter_activity":
            return True
        if name == "broadcast_trigger_status_activity":
            return None
        raise AssertionError(f"Unexpected activity: {name}")

    async def fake_child(name, *, args, **_options):
        assert name == "MachinaWorkflow"
        captured.append(args[0])
        controller._closed = True
        completed.set()

    monkeypatch.setattr(workflow, "logger", MagicMock())
    monkeypatch.setattr(workflow, "patched", lambda name: queue_patch if name == "controller-durable-queue-v2" else True)
    monkeypatch.setattr(workflow, "wait_condition", fake_wait)
    monkeypatch.setattr(workflow, "sleep", fake_sleep)
    monkeypatch.setattr(workflow, "execute_activity", fake_activity)
    monkeypatch.setattr(workflow, "start_child_workflow", fake_child)
    monkeypatch.setattr(workflow, "in_workflow", lambda: False)
    monkeypatch.setattr(workflow, "info", lambda: types.SimpleNamespace(
        workflow_id="gmail-controller", run_id="probe-run",
        get_current_history_length=lambda: 0,
        is_continue_as_new_suggested=lambda: False,
    ))

    nodes = [
        {"id": "gmail", "type": "googleGmailReceive", "data": {"label": "Gmail"}},
        {"id": "job", "type": "employeeJob", "data": {"label": "Employee job"}},
    ]
    edges = [{"source": "gmail", "target": "job", "sourceHandle": "output-main", "targetHandle": "input-main"}]
    spec = {
        "listener_id": "gmail-listener", "workflow_type": "PollingTriggerWorkflow",
        "trigger_node_id": "gmail", "event_type": "com.opencompany.gmail.message.received",
        "listener_args": {
            "trigger_node_id": "gmail", "trigger_label": "gmail", "node_type": "googleGmailReceive",
            "workflow_id": "gmail-probe", "workflow_slug": "gmail-probe",
            "data_scope_id": "gmail-probe-scope", "graphVersion": 2, "generation": 1,
            "version": 1, "filter_params": {"poll_interval": 60}, "seen_ids": [],
            "nodes": nodes, "edges": edges,
        },
    }
    control_data = {
        "state": "running", "generation": 1,
        "execution_control_version": control_version,
        "controller_workflow_id": "gmail-controller",
        "triggers": {"gmail-listener": spec},
    }
    try:
        await asyncio.wait_for(controller.run(control_data), timeout=5)
    finally:
        completed.set()
        for task in controller._poll_tasks.values():
            task.cancel()
        await asyncio.gather(*controller._poll_tasks.values(), return_exceptions=True)

    assert len(captured) == 1
    assert len(polls) >= 2
    assert polls[0]["baseline_only"] is True
    assert polls[1]["baseline_only"] is False
    child = captured[0]
    trigger = next(node for node in child["nodes"] if node["id"] == "gmail")
    assert trigger["_pre_executed"] is True
    output = trigger["_trigger_output"]
    assert output["_event_envelope"] == event

    config = json.loads((ROOT / "server/config/employee_apps.json").read_text(encoding="utf-8"))
    prompt = _substitute(config["apps"]["gmail"]["trigger"]["prompt"], {}, "gmail", {})

    async def shared_output(*_args):
        raise AssertionError("The mission must use this run's trigger output")

    resolver = ParameterResolver(None, shared_output)
    resolved = await resolver.resolve(
        {"mission": prompt}, "job", child["nodes"], child["edges"], "gmail-probe-scope",
        run_outputs={"gmail": output},
    )
    expected_complete = "New email from sender@example.com\nSubject: Reply requested\n\nPlease reply to this email."
    if expect_loss:
        assert set(output) == {"_event_envelope"}
        assert resolved["mission"] == "New email from \nSubject: \n\n"
    else:
        assert output["from"] == email["from"]
        assert output["subject"] == email["subject"]
        assert output["body"] == email["body"]
        assert resolved["mission"] == expected_complete
    print(f"{revision} control={control_version} queue_patch={queue_patch} enveloped={enveloped}: {resolved['mission']!r}")

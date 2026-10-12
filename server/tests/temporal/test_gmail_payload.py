"""Regression for native polling output on the controller's shared spawn path."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from temporalio import workflow

from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow


def spec(kind="PollingTriggerWorkflow"):
    return {"listener_id": "gmail-listener", "workflow_type": kind, "trigger_node_id": "gmail",
        "listener_args": {"trigger_node_id": "gmail", "trigger_label": "gmail", "node_type": "googleGmailReceive",
            "workflow_id": "gmail-probe", "workflow_slug": "gmail-probe", "data_scope_id": "scope",
            "graphVersion": 2, "generation": 1, "filter_params": {}, "version": 1,
            "nodes": [{"id": "gmail", "type": "googleGmailReceive", "data": {}},
                      {"id": "job", "type": "employeeJob", "data": {}}],
            "edges": [{"source": "gmail", "target": "job", "targetHandle": "input-main"}]}}


@pytest.fixture
def temporal(monkeypatch):
    captured, activities = [], []

    async def invoke(name, payload, **options):
        activities.append((name, copy.deepcopy(payload)))
        if name == "evaluate_trigger_filter_activity":
            return True

    async def child(name, *, args, **options):
        captured.append({"name": name, "payload": args[0], **options})
        return MagicMock()

    monkeypatch.setattr(workflow, "logger", MagicMock())
    monkeypatch.setattr(workflow, "patched", lambda _name: True)
    monkeypatch.setattr(workflow, "in_workflow", lambda: False)
    monkeypatch.setattr(workflow, "execute_activity", invoke)
    monkeypatch.setattr(workflow, "start_child_workflow", child)
    return captured, activities


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["PollingTriggerWorkflow", "TriggerListenerWorkflow"])
async def test_recorded_spec_selects_payload_and_preserves_business_data(temporal, kind):
    captured, activities = temporal
    email = {"id": "provider-42", "from": "sender@example.com", "subject": "Reply", "body": "Complete body",
             "data": {"business": "nested field"}}
    event = email if kind == "PollingTriggerWorkflow" else {"id": "provider-42", "data": email, "type": "gmail"}
    await WorkflowControlWorkflow()._spawn_push_run(event, spec(kind))
    assert len(captured) == 1
    call = captured[0]
    assert call["id"] == "gmail-probe-gmail-provider-42"
    output = call["payload"]["nodes"][0]["_trigger_output"]
    assert output == {**email, "_event_envelope": event}
    assert [name for name, _ in activities] == ["broadcast_trigger_status_activity", "broadcast_trigger_status_activity"]


@pytest.mark.asyncio
async def test_push_default_remains_envelope_and_admission_precedes_start(temporal):
    captured, _ = temporal
    admitted = []

    async def admit():
        assert captured == []
        admitted.append(True)

    trigger = spec("TriggerListenerWorkflow")["listener_args"]
    trigger["node_type"] = "webhookTrigger"
    await TriggerListenerWorkflow()._spawn_child_run({"id": "p", "data": {"value": 4}}, trigger, admission_check=admit)
    assert admitted == [True]
    assert captured[0]["payload"]["nodes"][0]["_trigger_output"]["value"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("control_version,queue_patch", [(0, False), (0, True), (1, True)])
async def test_actual_poll_queue_and_legacy_paths_preserve_complete_email(monkeypatch, temporal, control_version, queue_patch):
    captured, _ = temporal
    controller = WorkflowControlWorkflow()
    polls = []
    done = asyncio.Event()
    email = {"id": "gmail-42", "message_id": "gmail-42", "thread_id": "thread-42",
             "from": "sender@example.com", "subject": "Reply requested", "body": "Please reply."}

    async def wait(predicate):
        while not predicate():
            await asyncio.sleep(0)

    async def sleep(_duration):
        await asyncio.sleep(0)

    async def invoke(name, payload, **options):
        if name == "poll.googleGmailReceive.v1":
            polls.append(copy.deepcopy(payload))
            if payload["baseline_only"]:
                return {"events": [], "seen_ids": ["old"]}
            if len(polls) == 2:
                return {"events": [email, email], "seen_ids": ["old", "gmail-42"]}
            await done.wait()
            return {"events": [], "seen_ids": ["old", "gmail-42"]}
        if name == "evaluate_trigger_filter_activity":
            return True

    original_child = workflow.start_child_workflow

    async def child(*args, **options):
        result = await original_child(*args, **options)
        controller._closed = True
        done.set()
        return result

    monkeypatch.setattr(workflow, "wait_condition", wait)
    monkeypatch.setattr(workflow, "sleep", sleep)
    monkeypatch.setattr(workflow, "execute_activity", invoke)
    monkeypatch.setattr(workflow, "start_child_workflow", child)
    monkeypatch.setattr(workflow, "patched", lambda name: queue_patch if name == "controller-durable-queue-v2" else True)
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(workflow_id="controller", run_id="run",
        get_current_history_length=lambda: 0, is_continue_as_new_suggested=lambda: False))
    try:
        await asyncio.wait_for(controller.run({"state": "running", "generation": 1,
            "execution_control_version": control_version, "controller_workflow_id": "controller",
            "triggers": {"gmail-listener": spec()}}), 5)
    finally:
        done.set()
        for task in controller._poll_tasks.values():
            task.cancel()
        await asyncio.gather(*controller._poll_tasks.values(), return_exceptions=True)
    assert len(captured) == 1
    output = captured[0]["payload"]["nodes"][0]["_trigger_output"]
    assert all(output[key] == value for key, value in email.items())
    assert output["_event_envelope"] == email
    assert polls[0]["baseline_only"] and not polls[1]["baseline_only"]

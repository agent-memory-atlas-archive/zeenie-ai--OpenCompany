"""``apply_employee_changes``: restart an employee on its latest saved graph
and answer with its fresh summary; the restart's own conflicts and
failures come back as error codes, still with the summary. Changes to one
employee run one at a time."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import services.employees  # noqa: F401 - registers the handlers
from services.deployment.restart import RestartResult
from services.employees import handlers

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


class Auth:
    async def has_valid_key(self, key):
        return False

    async def get_oauth_tokens(self, provider):
        return None

    async def list_api_key_providers(self):
        return []


@pytest.fixture()
def harness(monkeypatch, real_database):
    import core.container as container_module
    import services.plugin.deps as deps

    state = SimpleNamespace(database=real_database, calls=[], result=RestartResult("restarted"))

    async def restart(workflow_id, *, owner_id, key):
        state.calls.append((workflow_id, owner_id, key))
        return state.result

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth()))
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())
    monkeypatch.setattr(handlers, "restart_with_latest_graph", restart)
    return state


async def saved(database, workflow_id="7"):
    graph = {"nodes": [{"id": f"{workflow_id}:aiAgent:1", "type": "aiAgent", "data": {"label": "Maya"}}], "edges": []}
    assert await database.save_workflow(workflow_id=workflow_id, name="Maya", slug=f"Maya_{workflow_id}", data=graph)


async def apply(workflow_id="7", key="k1"):
    return await handlers.handle_apply_employee_changes({"workflow_id": workflow_id, "idempotency_key": key}, SOCKET)


@pytest.mark.parametrize("outcome", ["restarted", "reset", "unchanged"])
async def test_it_restarts_on_the_saved_graph(harness, outcome):
    await saved(harness.database)
    harness.result = RestartResult(outcome)
    result = await apply()
    assert result["success"] is True and result["employee"]["workflow_id"] == "7"
    assert harness.calls == [("7", "owner", "k1")]


@pytest.mark.parametrize("error", ["conflict", "restart_failed"])
async def test_a_restart_that_did_not_finish_says_why(harness, error):
    await saved(harness.database)
    harness.result = RestartResult("unchanged", error)
    result = await apply()
    assert (result["success"], result["error"]) == (False, error)
    assert result["employee"]["workflow_id"] == "7"


async def test_bad_requests(harness):
    assert await apply("missing") == {"success": False, "error": "not_found", "workflow_id": "missing"}
    assert await handlers.handle_apply_employee_changes({"idempotency_key": "k"}, SOCKET) == {"success": False, "error": "invalid_request"}
    assert await handlers.handle_apply_employee_changes({"workflow_id": "7"}, SOCKET) == {"success": False, "error": "invalid_request"}
    assert harness.calls == []


async def test_changes_to_one_employee_run_one_at_a_time(harness, monkeypatch):
    await saved(harness.database)
    order = []
    release = asyncio.Event()

    async def slow_restart(workflow_id, *, owner_id, key):
        order.append(f"start {key}")
        if key == "k1":
            await release.wait()
        order.append(f"end {key}")
        return RestartResult("restarted")

    monkeypatch.setattr(handlers, "restart_with_latest_graph", slow_restart)
    first = asyncio.ensure_future(apply(key="k1"))
    second = asyncio.ensure_future(apply(key="k2"))
    await asyncio.sleep(0.05)
    assert order == ["start k1"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["start k1", "end k1", "start k2", "end k2"]

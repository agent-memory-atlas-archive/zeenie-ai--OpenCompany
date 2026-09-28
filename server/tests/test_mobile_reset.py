"""Reset acknowledges only after phone tasks and admitted writes have drained."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nodes.mobile._control import MobileError
from nodes.mobile._node import MobileParams, MobileUseAgent
from nodes.mobile._runtime import MobileRuntime
from nodes.mobile._tool import AndroidTool
from services.plugin import NodeContext, NodeUserError


@pytest.fixture
def runtime(monkeypatch):
    import nodes.mobile._runtime as module

    value = MobileRuntime()
    value.serial = "emulator-test"
    value._setup_loaded = True
    monkeypatch.setattr(module, "event", Mock())
    monkeypatch.setattr(value, "driver_call", AsyncMock(return_value={"width": 1080, "height": 1920}))
    monkeypatch.setattr(value, "_publish_progress", AsyncMock())
    return value


def args(**updates):
    return {
        "principal": "owner", "workflow_id": "wf", "node_id": "phone", "run_id": "run",
        "generation": 4, "params": {"prompt": "Open settings", "max_steps": 5, "timeout_s": 30},
        "model": {}, "broker_url": "http://127.0.0.1/broker", **updates,
    }


async def cancelled(task):
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)


async def reset(runtime, **updates):
    await runtime.reset_execution_state(**{"workflow_id": "wf", "node_id": "phone", "generation": 4, **updates})


async def test_reset_cancels_queued_coroutine_without_acquiring_device(runtime, monkeypatch):
    worker = AsyncMock()
    monkeypatch.setattr(runtime, "_run_worker", worker)
    await runtime.task_lock.acquire()
    task = asyncio.create_task(runtime.run(**args()))
    other = asyncio.create_task(runtime.run(**args(run_id="other", workflow_id="other-wf")))
    await asyncio.sleep(0)
    await asyncio.wait_for(reset(runtime), 1)
    await cancelled(task)
    assert [entry["run_id"] for entry in runtime.queue] == ["other"]
    assert not other.done()
    assert runtime.last_task is None
    assert "run" not in runtime._run_tasks
    worker.assert_not_awaited()
    await runtime.cancel("other-wf", "phone")
    await cancelled(other)
    runtime.task_lock.release()


class WaitingWorker:
    def __init__(self):
        self.returncode = None
        self.terminated = asyncio.Event()

    def terminate(self):
        self.returncode = -15
        self.terminated.set()

    def kill(self):
        self.terminate()

    async def wait(self):
        await self.terminated.wait()
        return self.returncode


async def test_reset_stops_model_wait_and_clears_activity_before_ack(runtime, monkeypatch):
    entered = asyncio.Event()
    process = WaitingWorker()

    async def worker(*_args):
        runtime.worker = process
        runtime.active.update(phase="Waiting for model", activity=[{"message": "old"}], plan=[{"description": "old"}])
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_run_worker", worker)
    task = asyncio.create_task(runtime.run(**args()))
    await entered.wait()
    assert runtime.capabilities
    await asyncio.wait_for(reset(runtime), 1)
    await cancelled(task)
    assert process.terminated.is_set()
    assert runtime.worker is None
    assert not runtime.capabilities
    assert not runtime._run_tasks
    assert runtime.active is None
    assert runtime.last_task is None
    assert runtime.serial == "emulator-test"
    await reset(runtime)  # The two reset paths may call a hook twice.
    assert runtime.last_task is None


async def test_reset_paused_task_preserves_human_lease(runtime):
    lease = await runtime.takeover("viewer")
    task = asyncio.create_task(runtime.run(**args()))
    await asyncio.sleep(0)
    assert runtime.active["status"] == "awaiting_user"
    await asyncio.wait_for(reset(runtime), 1)
    await cancelled(task)
    assert runtime.control.owner == lease.owner
    assert runtime.control.epoch == lease.epoch
    assert runtime.viewer == "viewer"
    assert runtime.last_task is None


async def test_reset_cleans_task_cancelled_during_initial_progress(runtime, monkeypatch):
    entered = asyncio.Event()

    async def publish(_phase):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_publish_progress", publish)
    task = asyncio.create_task(runtime.run(**args()))
    await entered.wait()
    await asyncio.wait_for(reset(runtime), 1)
    await cancelled(task)
    assert runtime.active is None
    assert runtime.last_task is None
    assert not runtime._run_tasks


async def test_reset_waits_for_admitted_mutation_and_rejects_late_generation(runtime, monkeypatch):
    entered, mutation_entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def worker(*_args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_run_worker", worker)
    task = asyncio.create_task(runtime.run(**args()))
    await entered.wait()
    lease = next(iter(runtime.capabilities.values()))[1]

    async def mutation():
        mutation_entered.set()
        await finish.wait()

    write = asyncio.create_task(runtime.control.perform(lease, "write", mutation))
    await mutation_entered.wait()
    resetting = asyncio.create_task(reset(runtime))
    await asyncio.sleep(0)
    assert not resetting.done()
    assert runtime.control.owner is None
    assert not runtime.capabilities
    with pytest.raises(MobileError, match="reset workflow generation"):
        await runtime.run(**args(run_id="late"))
    finish.set()
    await write
    await asyncio.wait_for(resetting, 1)
    await cancelled(task)
    assert runtime.active is None
    assert runtime.last_task is None
    monkeypatch.setattr(runtime, "_run_worker", AsyncMock(return_value={"success": True, "result": "new"}))
    assert (await runtime.run(**args(run_id="new", generation=5)))["response"] == "new"


@pytest.mark.parametrize("reset_generation,other", [
    (4, {"workflow_id": "other"}), (4, {"node_id": "other"}), (4, {"generation": 5}),
    (0, {"generation": 1}), (0, {"generation": 5}),
])
async def test_reset_preserves_unrelated_active_task_and_capability(runtime, monkeypatch, reset_generation, other):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def worker(*_args):
        entered.set()
        await finish.wait()
        return {"success": True, "result": "untouched"}

    monkeypatch.setattr(runtime, "_run_worker", worker)
    task = asyncio.create_task(runtime.run(**args(**other)))
    await entered.wait()
    before = dict(runtime.capabilities)
    owner, epoch = runtime.control.owner, runtime.control.epoch
    await reset(runtime, generation=reset_generation)
    assert runtime.capabilities == before
    assert (runtime.control.owner, runtime.control.epoch) == (owner, epoch)
    assert not task.done()
    finish.set()
    assert (await task)["response"] == "untouched"
    assert runtime.last_task["status"] == "completed"


async def test_cancel_run_id_waits_for_only_selected_queue_entry(runtime):
    await runtime.task_lock.acquire()
    first = asyncio.create_task(runtime.run(**args(run_id="first")))
    second = asyncio.create_task(runtime.run(**args(run_id="second")))
    await asyncio.sleep(0)
    await runtime.cancel("wf", "phone", run_id="first")
    await cancelled(first)
    assert [entry["run_id"] for entry in runtime.queue] == ["second"]
    assert not second.done()
    await runtime.cancel("wf", "phone", run_id="second")
    await cancelled(second)
    runtime.task_lock.release()


async def test_phone_only_reset_cancels_direct_queue_but_preserves_graph_and_other_node(runtime):
    await runtime.task_lock.acquire()
    direct = asyncio.create_task(runtime.run(**args(run_id="direct", generation=0)))
    graph = asyncio.create_task(runtime.run(**args(run_id="graph", generation=5)))
    other = asyncio.create_task(runtime.run(**args(run_id="other", node_id="other", generation=0)))
    await asyncio.sleep(0)
    await reset(runtime, generation=0)
    await cancelled(direct)
    assert [entry["run_id"] for entry in runtime.queue] == ["graph", "other"]
    assert not graph.done() and not other.done()
    assert runtime._retired_generations == {}
    await runtime.cancel("wf", "phone")
    await runtime.cancel("wf", "other")
    await cancelled(graph)
    await cancelled(other)
    runtime.task_lock.release()


async def test_negative_reset_generation_cannot_expand_reset_scope(runtime):
    await runtime.task_lock.acquire()
    task = asyncio.create_task(runtime.run(**args()))
    await asyncio.sleep(0)
    prior = {"workflow_id": "wf", "node_id": "phone", "run_id": "prior", "generation": 4}
    runtime.last_task = prior
    with pytest.raises(MobileError, match="cannot be negative"):
        await reset(runtime, generation=-1)
    assert runtime.last_task is prior
    assert runtime.queue[0]["status"] == "queued"
    assert not task.done()
    assert runtime._retired_generations == {}
    await runtime.cancel("wf", "phone")
    await cancelled(task)
    runtime.task_lock.release()


async def test_reset_during_natural_cleanup_does_not_interrupt_drain(runtime, monkeypatch):
    worker_entered, finish_worker = asyncio.Event(), asyncio.Event()
    mutation_entered, finish_mutation = asyncio.Event(), asyncio.Event()

    async def worker(*_args):
        worker_entered.set()
        await finish_worker.wait()
        return {"success": True, "result": "completed"}

    async def mutation():
        mutation_entered.set()
        await finish_mutation.wait()

    monkeypatch.setattr(runtime, "_run_worker", worker)
    task = asyncio.create_task(runtime.run(**args()))
    await worker_entered.wait()
    lease = next(iter(runtime.capabilities.values()))[1]
    write = asyncio.create_task(runtime.control.perform(lease, "write", mutation))
    await mutation_entered.wait()
    finish_worker.set()
    await asyncio.sleep(0)
    assert "run" in runtime._draining_runs
    first = asyncio.create_task(reset(runtime))
    second = asyncio.create_task(reset(runtime))
    await asyncio.sleep(0)
    assert not first.done() and not second.done()
    assert not task.cancelling()
    finish_mutation.set()
    await write
    await asyncio.wait_for(asyncio.gather(task, first, second), 1)
    assert runtime.active is None
    assert runtime.last_task is None
    assert not runtime._run_tasks


@pytest.mark.parametrize("reset_generation,generation,cleared", [
    (4, 0, True), (4, 4, True), (4, 5, False),
    (0, 0, True), (0, 4, False), (0, 5, False), (0, -1, False),
])
async def test_reset_clears_only_matching_finished_activity(runtime, reset_generation, generation, cleared):
    entry = {"workflow_id": "wf", "node_id": "phone", "run_id": "old", "generation": generation,
             "status": "completed", "activity": [{"message": "old activity"}]}
    runtime.last_task = entry
    await reset(runtime, generation=reset_generation)
    assert runtime.last_task is (None if cleared else entry)


@pytest.mark.parametrize("node_cls", [MobileUseAgent, AndroidTool])
async def test_plugin_reset_does_not_instantiate_runtime(monkeypatch, node_cls):
    import nodes.mobile._runtime as module

    monkeypatch.setattr(module, "_runtime", None)
    constructor = Mock(side_effect=AssertionError("Reset must not initialize a phone"))
    monkeypatch.setattr(module, "MobileRuntime", constructor)
    result = await node_cls.reset_execution_state(node_id="phone", workflow_id="wf", execution_id="exec",
                                                 generation=4, graph={}, database=None)
    assert result == {"reset": False}
    constructor.assert_not_called()


@pytest.mark.parametrize("node_cls", [MobileUseAgent, AndroidTool])
async def test_plugin_reset_delegates_existing_runtime(monkeypatch, node_cls):
    import nodes.mobile._runtime as module

    hook = AsyncMock(return_value={"reset": True})
    monkeypatch.setattr(module, "_runtime", SimpleNamespace(reset_execution_state=hook))
    assert await node_cls.reset_execution_state(node_id="phone", workflow_id="wf", execution_id="exec",
                                                generation=4, graph={}, database=None) == {"reset": True}
    hook.assert_awaited_once_with(workflow_id="wf", node_id="phone", generation=4)


@pytest.mark.parametrize("generation,status,rejected", [(4, "resetting", True), (4, "reset", True),
                                                       (5, "active", True), (4, "active", False)])
async def test_graph_admission_reads_latest_control_immediately_before_run(monkeypatch, generation, status, rejected):
    import nodes.mobile._node as node
    import nodes.mobile._runtime as module
    import services.plugin.deps as deps

    order = []

    async def broker():
        order.append("broker")
        return "http://local"

    async def latest(_workflow):
        order.append("control")
        return SimpleNamespace(generation=generation, status=status)

    async def run(**kwargs):
        order.append("run")
        assert kwargs["generation"] == 4
        return {"outcome": "completed"}

    value = SimpleNamespace(ensure_broker=broker, run=AsyncMock(side_effect=run))
    monkeypatch.setattr(module, "get_runtime", lambda: value)
    monkeypatch.setattr(node, "require_mobile_owner", AsyncMock())
    monkeypatch.setattr(node, "resolve_model", AsyncMock(return_value={}))
    monkeypatch.setattr(deps, "get_database", lambda: SimpleNamespace(get_latest_workflow_control=latest))
    ctx = NodeContext(node_id="phone", node_type="mobile_use_agent", workflow_id="wf", execution_id="exec", raw={"generation": 4})
    if rejected:
        with pytest.raises(NodeUserError, match="inactive workflow generation"):
            await MobileUseAgent().execute_op(ctx, MobileParams(prompt="Open settings"))
        value.run.assert_not_awaited()
        assert order == ["broker", "control"]
    else:
        await MobileUseAgent().execute_op(ctx, MobileParams(prompt="Open settings"))
        assert order == ["broker", "control", "run"]


async def test_workspace_generation_zero_does_not_query_graph_control(monkeypatch):
    import nodes.mobile._node as node
    import nodes.mobile._runtime as module
    import services.plugin.deps as deps

    value = SimpleNamespace(ensure_broker=AsyncMock(return_value="http://local"), run=AsyncMock(return_value={}))
    monkeypatch.setattr(module, "get_runtime", lambda: value)
    monkeypatch.setattr(node, "require_mobile_owner", AsyncMock())
    monkeypatch.setattr(node, "resolve_model", AsyncMock(return_value={}))
    database = Mock(side_effect=AssertionError("Direct invocations use their Temporal barrier"))
    monkeypatch.setattr(deps, "get_database", database)
    ctx = NodeContext(node_id="phone", node_type="mobile_use_agent", workflow_id="wf", execution_id="node-invoke-direct")
    await MobileUseAgent().execute_op(ctx, MobileParams(prompt="Open settings"))
    assert value.run.await_args.kwargs["generation"] == 0
    database.assert_not_called()

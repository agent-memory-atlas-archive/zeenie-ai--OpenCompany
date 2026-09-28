"""Android uses the standard tool schema and the same controlled runtime."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import pytest
from pydantic import ValidationError
from services.plugin import NodeContext, ToolNode
from nodes.mobile._tool import AndroidTool, AndroidTaskInput
from nodes.mobile import _node, _runtime, _diagnostics


def test_android_is_discoverable_as_a_tool():
    from services.node_registry import get_node_class
    assert get_node_class("android_tool") is AndroidTool
    assert issubclass(AndroidTool, ToolNode)
    assert AndroidTool.component_kind == "tool"
    assert set(AndroidTool.ToolInput.model_fields) == {"prompt"}
    assert any(handle["role"] == "tools" for handle in AndroidTool.handles)
    with pytest.raises(ValidationError):
        AndroidTaskInput(prompt="Open settings", workflow_id="other")


async def test_tool_uses_saved_limits_and_existing_mobile_pipeline(monkeypatch):
    runtime = SimpleNamespace(run=AsyncMock(return_value={"response": "Done", "run_id": "test", "outcome": "completed"}),
                              ensure_broker=AsyncMock(return_value="http://127.0.0.1/private"))
    monkeypatch.setattr(_runtime, "get_runtime", lambda: runtime)
    owner = AsyncMock()
    model = AsyncMock(return_value={"provider": "openai"})
    monkeypatch.setattr(_node, "require_mobile_owner", owner)
    monkeypatch.setattr(_node, "resolve_model", model)
    ctx = NodeContext(node_id="phone-tool", node_type="android_tool", workflow_id="wf", execution_id="run", user_id="owner")
    result = await AndroidTool().execute_as_tool({"prompt": "Open Settings"}, {"max_steps": 7, "timeout_s": 60}, ctx)
    assert result.get("error") is None, result
    args = runtime.run.await_args.kwargs
    assert args["params"]["prompt"] == "Open Settings"
    assert args["params"]["max_steps"] == 7
    assert args["params"]["timeout_s"] == 60
    assert args["node_id"] == "phone-tool"
    owner.assert_awaited_once_with("owner")
    model.assert_awaited_once()
    assert model.await_args.args[0] is ctx
    assert model.await_args.args[1].model_source == "global"


def test_diagnostics_are_bounded_and_exclude_content(monkeypatch, tmp_path):
    monkeypatch.setattr(_diagnostics, "mobile_root", lambda: tmp_path)
    try:
        for index in range(45):
            _diagnostics.event("device_action_completed", operation="text", duration_ms=index,
                               prompt="secret prompt", parameters={"text": "password"}, api_key="secret-key")
        records = [json.loads(line) for line in (tmp_path / "mobile.log").read_text().splitlines()]
        assert len(records) == 45
        assert len(_diagnostics.recent()) == 40
        assert "secret" not in json.dumps(records)
        assert "password" not in json.dumps(records)
        assert records[-1]["duration_ms"] == 44
    finally:
        for handler in list(_diagnostics._logger.handlers):
            handler.close()
            _diagnostics._logger.removeHandler(handler)
        _diagnostics._recent.clear()

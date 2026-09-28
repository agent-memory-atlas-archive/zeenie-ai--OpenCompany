"""Natural-language Android tasks exposed through the standard agent tool port."""

from pydantic import BaseModel, ConfigDict, Field
from services.plugin import NodeContext, Operation, ToolNode
from ._node import MobileUseAgent, MobileParams, MobileOutput


class AndroidTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=20000, description="What to do on the Android phone, in plain language.")


class AndroidTool(ToolNode):
    type = "android_tool"
    display_name = "Android"
    description = "Let an AI agent carry out a task on your Android phone."
    group = ("tool",)
    tool_name = "android"
    tool_description = (
        "Carry out a task on the shared Android phone. Supply a clear plain-language prompt. "
        "The phone must be set up and started in Workspace > Mobile. Tasks share the phone and wait "
        "when a person is using it. Do not request passwords in chat; let the person sign in on the phone."
    )
    tool_schema_locked = True
    requires_context = True
    needs_canvas = True
    workspace_task = True
    ui_hints = {"workspace": {"kind": "mobile"}}
    handles = (
        {"name": "input-model", "kind": "input", "position": "bottom", "label": "Model", "role": "model"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = MobileUseAgent.task_queue
    retry_policy = MobileUseAgent.retry_policy
    # Share the mobile agent's allowance for queueing and manual-control waits.
    start_to_close_timeout = MobileUseAgent.start_to_close_timeout
    Params = MobileParams
    ToolInput = AndroidTaskInput
    Output = MobileOutput

    @classmethod
    async def reset_execution_state(cls, **kwargs) -> dict:
        return await MobileUseAgent.reset_execution_state(**kwargs)

    @classmethod
    def interpret_result(cls, result):
        # Agent tool invocation flattens failures to {"error": ...}. Do not
        # report a rejected Android task as a successful Temporal activity.
        if isinstance(result, dict) and "success" not in result and result.get("error"):
            return False, result, str(result["error"])
        return super().interpret_result(result)

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: AndroidTaskInput | MobileParams) -> dict:
        config = ctx.raw.get("_tool_config")
        if isinstance(config, MobileParams):
            params = config.model_copy(update={"prompt": params.prompt})
        elif not isinstance(params, MobileParams):
            params = MobileParams(prompt=params.prompt)
        return await MobileUseAgent().execute_op(ctx, params)

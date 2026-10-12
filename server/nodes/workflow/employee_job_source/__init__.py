"""Intrinsic read-only access to an admitted job's complete email source."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class EmployeeJobSourceParams(BaseModel):
    content: Literal["body_text", "original_body", "original_mission"] = "body_text"
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=10000, ge=1, le=10000, strict=True)
    model_config = ConfigDict(extra="forbid")


class EmployeeJobSourceOutput(BaseModel):
    content: str
    text: str
    offset: int
    next_offset: int
    total_chars: int
    complete: bool
    message_id: str | None = None
    thread_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    subject: str | None = None
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class EmployeeJobSourceNode(ActionNode):
    type = "employeeJobSource"
    display_name = "Original email"
    description = "Read a page of this admitted employee job's complete original email or mission"
    tool_name = "employee_job_source"
    tool_description = "Read the complete original email for your current assigned job, in pages of up to 10000 characters. Use next_offset for the next page."
    group = ("workflow",)
    component_kind = "square"
    usable_as_tool = True
    task_queue = TaskQueue.DEFAULT
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    handles = ({"name": "output-top", "kind": "output", "position": "top", "role": "tool"},)
    Params = EmployeeJobSourceParams
    Output = EmployeeJobSourceOutput

    @Operation("read")
    async def read(self, ctx: NodeContext, params: EmployeeJobSourceParams) -> EmployeeJobSourceOutput:
        from services.plugin.deps import get_database
        from services.employees.job_source import read_employee_job_source
        result = await read_employee_job_source(get_database(), ctx.raw, **params.model_dump())
        return EmployeeJobSourceOutput(**result)

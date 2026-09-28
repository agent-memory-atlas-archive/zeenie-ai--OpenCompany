"""Reply in Chat: an answer into the workflow's chat thread.

A sink wired after an agent: it posts ``message`` (a template, usually
``{{<agent label>.response}}``) to the thread of the workflow it runs in, as
the assistant. The owner reads it in Talk on the employee's Home page and
in the editor's chat pane. The write goes through services/chat_thread.py,
which stamps the live generation and announces ``chat.updated``.

A message that is empty, or exactly NO_REPLY (the agent had nothing to
say), posts nothing.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.approvals.contract import NO_REPLY
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class ChatReplyParams(BaseModel):
    message: str = Field(
        default="",
        json_schema_extra={"rows": 3, "placeholder": "{{agent.response}}"},
    )

    model_config = ConfigDict(extra="ignore")

    @field_validator("message", mode="before")
    @classmethod
    def _as_text(cls, value: Any) -> str:
        # A whole-value template keeps its type (parameter_resolver), so an
        # upstream field that is not text arrives as-is.
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)


class ChatReplyOutput(BaseModel):
    #: False when there was nothing to post.
    posted: bool = False
    message: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatReplyNode(ActionNode):
    type = "chatReply"
    display_name = "Reply in Chat"
    subtitle = "Answer in Talk"
    group = ("chat",)
    description = "Post a message to this workflow's chat thread, where the owner talks to it"
    component_kind = "square"
    handles = ({"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},)
    hide_output_handle = True
    annotations = {"destructive": False, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = ChatReplyParams
    Output = ChatReplyOutput

    @Operation("reply")
    async def reply(self, ctx: NodeContext, params: ChatReplyParams) -> ChatReplyOutput:
        from services.chat_thread import record_chat_message
        from services.plugin.deps import get_database

        text = params.message.strip()
        if not text or text == NO_REPLY:
            return ChatReplyOutput(posted=False)
        if not ctx.workflow_id:
            raise NodeUserError("Reply in Chat posts to the workflow's chat: save the workflow first.")
        if not await record_chat_message(get_database(), ctx.workflow_id, "assistant", text):
            raise RuntimeError("The reply could not be saved to the chat")
        return ChatReplyOutput(posted=True, message=text)

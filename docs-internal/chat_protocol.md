# Chat protocol (v1)

The wire contract between the server's chat runtime (`server/services/chat/`) and the shared chat UI
(`client/src/features/chat/`). Home's employee page and Dev's console Chat pane both speak it. It follows AG-UI's
event model and ordering ([events](https://docs.ag-ui.com/concepts/events),
[interrupts](https://docs.ag-ui.com/concepts/interrupts.md),
[activity events](https://docs.ag-ui.com/spec/1.0/events/activity.md)), carried on the existing WebSocket with
snake_case fields in the repo's CloudEvents envelope (`services/events/envelope.py`), never as literal camelCase
AG-UI JSON.

Status: being built on `feature/chat-genui`; the design and the owner's decisions are in the plan recorded at
`~/.claude/plans/analyze-the-new-generative-zazzy-cosmos.md`. Change this document in the same commit as any change
to the shapes below, and bump `protocol_version` when an existing field changes meaning.

## Concepts

| Term | Meaning |
|---|---|
| Session | One conversation. Its id is the workflow id (`"default"` with no workflow open). |
| Run | One answer the employee works on: an owner message, an edit, a regenerate, or a button press in generated UI (kinds `message`, `edit`, `regenerate`, `action`). Approved sends execute as runs of kind `resume`. |
| Lane | At most one non-terminal run per session, `resume` runs excepted. A second send is refused with `run_in_progress`; the composer shows Stop instead of Send while a run is live. |
| Message tree | Messages link to their parent. Owner messages that share a parent are branches; replies under the same owner message are versions. The session's active leaf picks the path shown. |
| Part | Structured content attached to a reply: steps, generated UI, artifacts, approvals, sources, follow-ups. |
| Generation | The deployment generation a message was written in (`run_key`). A Reset clears the thread. |

## Delivery

Run events never ride `StatusBroadcaster.broadcast` (which reaches every socket). A socket receives a session's run
events only after `chat_subscribe` succeeds. That handler applies the same checks as the canvas handlers: refuse the
internal socket, load the workflow, and compare its owner with the socket's execution principal.

```json
{"type": "chat_run_event",
 "data": {"specversion": "1.0", "id": "r_7f3c…:12", "source": "opencompany://services/chat",
          "type": "com.opencompany.chat.run.text.content", "subject": "r_7f3c…",
          "time": "2026-10-03T21:40:12.512Z", "datacontenttype": "application/json",
          "data": {"workflow_id": "…", "session_id": "…", "run_id": "r_7f3c…", "seq": 12,
                   "hub_epoch": "e_91ab…", "message_id": "r_7f3c….0.1", "delta": "Saturday is "}}}
```

- Scope fields (`workflow_id`, `session_id`, `run_id`) live inside `data`, never as top-level CloudEvents extension
  attributes.
- `seq` increases by one per run. The client drops duplicates `(run_id, seq)` and asks for a snapshot on a gap.
- `hub_epoch` changes when the server process restarts. A changed epoch means the client must resync.
- Each subscriber has a bounded queue. Under pressure the hub merges consecutive text deltas of one segment, never
  drops lifecycle events, and finally sends `custom` `opencompany.resync`.
- `chat.updated` remains an identity-only broadcast (`{workflow_id, session_id, role}`) that tells every open thread
  to refetch `get_chat_messages`.

## Events

Every event's `data` carries `{workflow_id, session_id, run_id, seq, hub_epoch}` plus the fields below. The CloudEvents
`type` is `com.opencompany.chat.run.<suffix>`; `subject` is the run id; `id` is `<run_id>:<seq>`.

| AG-UI event | Suffix | Fields |
|---|---|---|
| RUN_STARTED | `started` | `kind`, `parent_run_id?`, `user_message_id?`, `reply_message_id`, `started_at` |
| RUN_FINISHED | `finished` | `outcome` (below), `result {reply_message_id?, no_reply?}`, `duration_ms`, `step_count` |
| RUN_ERROR | `failed` | `message` (safe to show, at most 500 chars), `code`, `hint?`, `requires_user_action?` |
| STEP_STARTED | `step.started` | `step_id`, `step_name`, `icon?`, `agent_node_id`, `tool_node_id?`, `attempt` |
| STEP_FINISHED | `step.finished` | `step_id`, `step_name` (the done label), `state` (`done`, `failed`, `skipped`), `detail?`, `duration_ms`, `narration?` |
| TEXT_MESSAGE_START | `text.started` | `message_id` (segment id `{run_id}.{iteration}.{attempt}`), `role: "assistant"`, `agent_node_id` |
| TEXT_MESSAGE_CONTENT | `text.content` | `message_id`, `delta` (never empty) |
| TEXT_MESSAGE_END | `text.ended` | `message_id`, `final` (bool), `reply_message_id?`, `output_tokens?` |
| TOOL_CALL_START | `tool_call.started` | `tool_call_id`, `tool_call_name`, `parent_message_id?`, `label` (sending tools only) |
| TOOL_CALL_ARGS | `tool_call.args` | `tool_call_id`, `delta` |
| TOOL_CALL_END | `tool_call.ended` | `tool_call_id` |
| TOOL_CALL_RESULT | `tool_call.result` | `message_id`, `tool_call_id`, `content` (a JSON string), `role: "tool"` |
| ACTIVITY_SNAPSHOT | `activity.snapshot` | `message_id` (the part id), `activity_type`, `content` (an object), `replace` |
| ACTIVITY_DELTA | `activity.delta` | `message_id`, `activity_type`, `patch` (RFC 6902 operations on `content`) |
| REASONING_START / END | `reasoning.started` / `reasoning.ended` | `message_id`; lifecycle only, no reasoning text |
| CUSTOM | `custom` | `name`, `value` |

`activity_type` values: `json_render`, `approval`, `sources`, `followups`, `artifact`.

`custom` names: `opencompany.segment_discarded` (`{message_id}`: a retried LLM attempt replaces this segment),
`opencompany.retrying` (`{retry_after?, attempt}`), `opencompany.stopping`, `opencompany.resync`.

**Text segments.** Only the agent that answers the owner streams text. A segment that ends with `final: false`
accompanied a tool call: the client folds it into the steps as narration. The segment with `final: true` is the
reply. Text that could still turn out to be `NO_REPLY` or a `<followups>` block is held back on the server and never
streamed.

**Outcomes** (`run.finished`):

| `outcome.type` | Meaning |
|---|---|
| `success` | The run ended. `result.reply_message_id` names the saved reply, or `result.no_reply` is true. |
| `interrupt` | Drafts wait for the owner. `outcome.interrupts: [{id, reason: "tool_call", message, tool_call_id, response_schema, expires_at}]`; `id` is the approval id. |
| `stopped` | The owner pressed Stop. OpenCompany extension; AG-UI defines only `success` and `interrupt`. |

Never-picked-up runs end with `run.failed` code `not_delivered`; a Reset ends live runs with code `reset`.

## Handlers

All are WebSocket request/response handlers with snake_case payloads. Failures answer
`{success: false, error: "<code>", …}`.

### Chat (`server/services/chat/handlers.py`)

| Handler | Request | Response |
|---|---|---|
| `send_chat_message` | `{session_id, message, role: "user", timestamp?, client_message_id?, attachments?: FileRef[], options?: {web?}, ui_event?: {part_id, element_id, action, params}}` | `{success, message_id, run_id, delivery: "now" \| "queued", timestamp}` |
| `get_chat_messages` | `{session_id, limit?, all_generations?}` | `{success, messages, thread: {active_leaf_id, revision}, active_runs}` |
| `chat_subscribe` / `chat_unsubscribe` | `{session_id}` | `{success, hub_epoch, active_runs: [RunSnapshot]}` |
| `get_chat_run` | `{run_id}` | `{success, run: RunSnapshot}` |
| `stop_chat_run` | `{run_id}` or `{session_id}` | `{success, run_id, state}` |
| `edit_chat_message` | `{message_id, text, attachments?, expected_revision}` | `{success, message_id, run_id}` |
| `regenerate_chat_reply` | `{message_id, expected_revision}` | `{success, run_id}` |
| `switch_chat_branch` | `{message_id, target_id, expected_revision}` | `{success, thread}` |
| `set_chat_feedback` | `{message_id, rating: "good" \| "bad" \| null, comment?}` | `{success, reaches: ["next_reply", "memory"?]}` |
| `get_chat_context` | `{session_id}` | `{success, commands, suggestions, capabilities, genui_catalog, limits, ask_first: {value, editable, replies_gated}}` |
| `clear_chat_messages` | `{session_id}` | `{success}`; also clears runs, parts, snapshots, notes and feedback, and makes the employee forget the conversation |

`send_chat_message` with the same `client_message_id` returns the run the first call created.

**RunSnapshot** is the state the client reducer would hold after replaying the run's events:
`{run_id, kind, state, seq, steps, segments: [{message_id, text, final}], activities, interrupts, started_at}`.

### Approvals (`server/services/approvals/handlers.py`)

| Handler | Request | Response |
|---|---|---|
| `list_approvals` | `{workflow_id?, status?: str \| [str] \| "open" \| "recent", run_id?, kind?, limit?}` | `{success, approvals, counts, server_time}` |
| `get_approvals` | `{approval_ids}` (at most 50) | `{success, approvals, server_time}` |
| `decide_approval` | `{approval_id, decision: "send" \| "discard" \| "undo" \| "restore" \| "retry", decision_key, edited_args?, text?, subject?, expected_revision?, confirm?}` | `{success, approval, will_send_on_resume, execution}` |
| `resolve_interrupt` | `{interrupt_id, status: "resolved" \| "cancelled", payload: {approved, edited_args?}, decision_key}` | as `decide_approval` |
| `set_ask_first` | `{workflow_id, ask_first, expected_revision?}` | `{success, ask_first, revision, replies_gated, needs_apply}` |

`edited_args` replaces the call's arguments in full and may differ from them only in the editable keys. The approval
summary adds `kind` (`gate` or `tool_call`), `node_type`, `tool_name`, `tool_call_id`, `run_id`, `args`, `editable`,
`body_field`, `subject_field`, `approved_by` (`user` or `auto`), `undo_until`, `restore_until` and
`outcome {certainty: "sent" | "not_sent" | "unknown", error?, at}`.

### Plugins

| Handler | Owner | Request | Response |
|---|---|---|---|
| `chat_ui_state` | `nodes/chat/chat_ui` | `{workflow_id, part_id, delta: [{op: "replace" \| "add", path, value}], base_revision}` | `{success, state_revision}` or `stale` |
| `canvas_versions` | `nodes/tool/canvas` | `{workflow_id, node_id, item_id}` | `{success, versions: [{version, title, created_at, source, size_bytes}], latest}` |
| `canvas_version` | `nodes/tool/canvas` | `{workflow_id, node_id, item_id, version}` | `{success, item_id, version, title, content, language, created_at, filename}` |
| `transcribe_audio` | `nodes/speech` | `{workflow_id, audio: FileRef, language?}` | `{success, text, provider, model, duration_seconds}` |

## Messages

`get_chat_messages` returns the active path, oldest first:

```
{id, legacy_id, role, kind, text, message, timestamp, run_key, run_id, parent_id, status, attachments,
 parts, feedback, siblings: {index, count, ids}, editable}
```

- `message` equals `text`; it is kept for older readers.
- `kind`: `text`, `report` (Post to Talk: no parent, not editable), `action` (a button press), `notice`.
- `status`: `complete`, `stopped`, `error`.
- `siblings` are branches for owner messages and versions for replies.

Parts render in this order, whatever order they were produced in: steps, text, generated UI, artifacts, approvals,
sources, follow-ups.

```
parts: {
  steps?:     {duration_ms, items: [{step_id, name, state, detail?, duration_ms?}]},
  ui?:        [{part_id, ui_id, title?, spec, state, state_revision, elements}],
  artifacts?: [{workflow_id, canvas_node_id, item_id, version, title, format}],
  approvals?: [{approval_id, tool_call_id?}],
  sources?:   [{n, display, label, detail?, url?, kind, cited}],
  followups?: [string],
  stopped?:   true
}
```

Approvals are joined live from the approvals store; the part only names them.

## Generated UI

- **Source.** The employee calls the `show_ui` tool (`chatUi` plugin) with a json-render flat spec
  `{root, state, elements}`. The server validates it against `server/config/chat_genui_catalog.json`, the single
  source of truth for the client catalog, and streams it as `activity.snapshot` (empty spec) followed by one
  `activity.delta` per patch: `add /root`, `add /state`, then `add /elements/<id>` in depth-first order.
- **Owner edits.** `$bindState` writes stay in the message's local state and are sent with `chat_ui_state`; the last
  write per path wins, and the client flushes after 300 ms idle, on blur, before an action and when the page hides.
  The employee sees the state on its next turn as a `[ui-state]` note.
- **Buttons.** A press resolves `$state` params at click time. `ask` sends its text as the owner's message only when
  it equals the button's label; otherwise the text goes into the composer for review. Any other action becomes
  `send_chat_message{ui_event}`; the server checks it against the saved spec and starts a run of kind `action`
  whose turn carries `[ui-event]{…}[/ui-event]`.
- **Sanitising.** json-render 0.21 does not guard state paths (`setByPath` descends into `__proto__`), does not
  validate props against the catalog, and supports `watch` (actions fired by state changes), `repeat`, `$computed`
  and `confirm`. Both sides therefore:
  - refuse `__proto__`, `constructor` and `prototype` path segments everywhere (`$state`, `$bindState`, `$template`,
    `visible`, action params, patch paths);
  - strip `watch`, `repeat`, `slots`, `$computed` and `confirm`;
  - validate every element's props (the client degrades one bad prop at a time with forgiving zod schemas);
  - drop unknown component types before rendering.

  json-render renders each element inside its own error boundary, so one failing element renders nothing instead of
  breaking the reply.

## Notes to the employee

Things the employee should learn on its next turn are queued in `chat_notes` and prepended to that turn's user
message in brackets: `[updates]` (approval outcomes), `[ui-state]`, `[feedback]`. The agent's system prompt says that
bracketed lines come from OpenCompany, not from the owner. Notes are claimed when the next run's payload is prepared
and marked delivered when its conversation is saved, so a failed run offers them again.

## Fixtures

`client/src/features/chat/__fixtures__/` holds the design handoff's examples in this protocol's shapes, for client
and server tests alike:

- `<name>.spec.json` and `<name>.patches.jsonl` (`saturday-booking`, `reply-insights`, `reminders`): json-render
  specs and the depth-first patch stream for each. SlotPicker options use the catalog's generic keys (`time`,
  `detail`, `recommended`, `unavailable`, `note`) instead of the handoff's salon ones.
- `saturday-booking.events.json`: the handoff's AG-UI run as `chat_run_event` frames — steps, text, a generated UI
  streamed as `activity.delta` patches, a held WhatsApp send ending the run with an `interrupt` outcome, and the
  `resume` run that sends it once the owner presses Send.

## Error codes

| Code | Where | Meaning |
|---|---|---|
| `run_in_progress` | send, edit, regenerate, switch | A run is live in this session; `run_id` names it. |
| `save_failed` | send | The message could not be saved; nothing was dispatched. |
| `not_running` | send | The employee is not running and cannot queue messages. |
| `conflict` | edit, regenerate, switch, set_ask_first | `expected_revision` is stale. |
| `not_editable` | edit, regenerate, decide | The message belongs to an older generation, its prefix was compacted, or an edited argument is not editable. |
| `ui_event_rejected` | send | The element or action does not match the saved spec. |
| `access_denied` | all | The socket's principal does not own the workflow. |
| `too_late` | decide | The Undo or Restore window has passed. |
| `speech_unavailable` | transcribe_audio | No dictation provider has a stored key. |

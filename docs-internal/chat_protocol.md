# Chat protocol (v1)

The wire contract between the server's chat runtime (`server/services/chat/`) and the shared chat UI
(`client/src/features/chat/`). Home's employee page and Dev's console Chat pane both speak it. It follows AG-UI's
event model and ordering ([events](https://docs.ag-ui.com/concepts/events),
[interrupts](https://docs.ag-ui.com/concepts/interrupts.md),
[activity events](https://docs.ag-ui.com/spec/1.0/events/activity.md)), carried on the existing WebSocket with
snake_case fields in the repo's CloudEvents envelope (`services/events/envelope.py`), never as literal camelCase
AG-UI JSON.

Status: being built in phases on `main`; the design and the owner's decisions are in the plan recorded at
`~/.claude/plans/analyze-the-new-generative-zazzy-cosmos.md`. Change this document in the same commit as any change
to the shapes below, and bump `protocol_version` (returned by `get_chat_messages`) when an existing field changes
meaning.

Built so far: runs and their storage, the lifecycle events (`started`, `finished`, `failed`), subscriptions,
`get_chat_messages` v2 (with each message's `run`), `get_chat_run`, the watchdog and the MachinaWorkflow patch (see
[Runs](#runs)), and the shared chat UI on both hosts (see [Client](#client)). Every other event, handler and part
below is the contract the later phases build to.

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
- `chat_subscribe` answers with the session's live runs as snapshots, each with the `seq` it reflects. The hub is
  read before the database, so an event published meanwhile carries a higher `seq` than its snapshot: drop events
  with `seq` at or below the snapshot's, and buffer events that arrive before the response.
- Each subscriber has a bounded queue (`hub.subscriber_queue_size` in `server/config/chat_defaults.json`). A socket
  that falls that far behind has its queue replaced by one `custom` `opencompany.resync` per session it follows
  (`data: {session_id, hub_epoch, name, value}`, no `run_id`); the client then takes fresh snapshots. Merging
  consecutive text deltas of one segment comes with streaming.
- Delivery is in-process: events published by an activity on a worker in another process do not reach sockets here
  yet (a relay is planned).
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

Runs nothing will finish end with `run.failed`: code `not_delivered` (never picked up, or the employee stopped
first), `timed_out` (running longer than `runs.max_running_s`), `interrupted` (its workflow closed without finishing
it and no reply was saved; with a saved reply it finishes instead). A Reset ends live runs with code `reset`, the
owner's Clear with `cleared`, and both delete them.

## Handlers

All are WebSocket request/response handlers with snake_case payloads. Failures answer
`{success: false, error: "<code>", …}`.

### Chat (`server/services/chat/handlers.py`)

| Handler | Request | Response |
|---|---|---|
| `send_chat_message` | `{session_id, message, role: "user", timestamp?, client_message_id?, attachments?: FileRef[], options?: {web?}, ui_event?: {part_id, element_id, action, params}}` | `{success, message_id, run_id, delivery: "now" \| "queued", timestamp}`; `run_id` is null when the message starts no run |
| `get_chat_messages` | `{session_id, limit?, all_generations?}` | `{success, protocol_version, session_id, messages, thread: {active_leaf_id, revision}, active_runs: [RunSnapshot]}` |
| `chat_subscribe` | `{session_id}` | `{success, session_id, hub_epoch, active_runs: [RunSnapshot]}` |
| `chat_unsubscribe` | `{session_id}` | `{success, session_id, hub_epoch}` |
| `get_chat_run` | `{run_id}` | `{success, run: RunSnapshot}`, or `not_found` |
| `stop_chat_run` | `{run_id}` or `{session_id}` | `{success, run_id, state}` |
| `edit_chat_message` | `{message_id, text, attachments?, expected_revision}` | `{success, message_id, run_id}` |
| `regenerate_chat_reply` | `{message_id, expected_revision}` | `{success, run_id}` |
| `switch_chat_branch` | `{message_id, target_id, expected_revision}` | `{success, thread}` |
| `set_chat_feedback` | `{message_id, rating: "good" \| "bad" \| null, comment?}` | `{success, reaches: ["next_reply", "memory"?]}` |
| `get_chat_context` | `{session_id}` | `{success, commands, suggestions, capabilities, genui_catalog, limits, ask_first: {value, editable, replies_gated}}` |
| `clear_chat_messages` | `{session_id}` | `{success}`; also clears runs, parts, snapshots, notes and feedback, and makes the employee forget the conversation |

`send_chat_message` with the same `client_message_id` (1 to 100 letters, digits or `_.:-`) returns the message
and run the first call created, and dispatches nothing again.

**RunSnapshot** is the state the client reducer would hold after replaying the run's events
(`server/services/chat/reducer.py` folds them the same way):
`{run_id, session_id, workflow_id, kind, state, seq, hub_epoch, user_message_id, reply_message_id, parent_run_id,
created_at, started_at, finished_at, steps, segments: [{message_id, text, final}], activities, interrupts, outcome,
result, error, error_code}`. `state` is `queued`, `pending`, `running`, `stopping`, `finished`, `error` or `stopped`.
A snapshot read after a server restart has its steps but no text segments (text is stored with the reply).

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
 parts, feedback, siblings: {index, count, ids}, editable, client_message_id?,
 run?: {run_id, state, outcome, error?: {message, code, hint?}}}
```

- `id` is the message's stable id (`m_…` for the owner's, `a_<run id>` for a run's reply; rows saved before chat
  runs are `m<number>`). `legacy_id` is the database row number.
- `message` equals `text`; it is kept for older readers.
- `client_message_id` is echoed on the owner's messages that carried one, so an optimistic row and its saved row
  keep one key.
- `run` says how the run a message started (or answers) stood when the thread was read: its `state`, `outcome`, and
  for a failed run the error with the hint the run recorded. It is how a reload still shows that a message went
  unanswered; while the session is subscribed, the run's events are fresher (see [Client](#client)).
- Until branches land, `siblings` always holds the message alone and `editable` is false.
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
  and an action binding's `confirm`, `onSuccess` and `onError` (the last two set state or run further actions).
  Both sides therefore (the client in `client/src/lib/jsonRender/`):
  - refuse `__proto__`, `constructor` and `prototype` path segments everywhere (`$state`, `$bindState`, `$template`,
    `visible`, action params, patch paths); an expression that reads such a path is dropped, and a `visible`
    condition that reads one is false;
  - keep only an element's `type`, `props`, `children`, `visible` and `on`, and only an action binding's `action`
    and `params`: `watch`, `repeat`, `slots`, `confirm`, `onSuccess`, `onError` and `preventDefault` go;
  - drop `$computed`, and `$item`, `$index` and `$bindItem`, which mean nothing without `repeat`;
  - validate every element's props (the client degrades one bad prop at a time with forgiving zod schemas);
  - drop unknown component types before rendering.

  json-render renders each element inside its own error boundary, so one failing element renders nothing instead of
  breaking the reply.

## Runs

`server/services/chat/` (never imports `nodes/`):

- **Admission** (`ledger.admit_message`). `send_chat_message` writes the owner's message and its run in one write
  transaction reserved before its first read (`Database.reserved_session`, SQLite `BEGIN IMMEDIATE`), after checking
  the lane; a partial unique index on `chat_runs` enforces it too. A message starts a run only when a chat trigger in
  the deployed graph (the control generation's `graph_snapshot`) accepts its session, judged by that trigger's own
  filter (`event_waiter.build_filter`). Otherwise it is saved and dispatched without a run, like the editor's
  `"default"` session, which keeps its unscoped delivery.
- **Dispatch.** The `chat_message_received` event (`services/chat/events.py`, source `opencompany://services/chat`)
  has the run id as its CloudEvent id and carries `message_id` and `run_id` in `data`, so the listener's child run
  id is `<slug>-<trigger label>-<run id>`. It is never broadcast.
- **Start and finish.** MachinaWorkflow, behind the `machina-chat-run-v1` patch, reads the run id only from an event
  with that source and type, claims the run (`chat_run.start`: `pending` or `queued` to `running`, recording the
  Temporal workflow and run ids) once the firing trigger's output is stored, passes `run_scope {run_id, session_id}`
  to every node context, and finishes it at its single exit (`chat_run.finish`). With several chat triggers in one
  graph, the first to claim tracks the run and the others run untracked. Only the claimant may finish.
- **The reply.** `chatReply` with a `run_scope` saves the answer through `ledger.post_reply`: the first reply takes
  the run's reply id `a_<run id>`, another reply node in the same run `a_<run id>.<n>`, and a retry from the same node
  saves nothing new. A run whose conversation was reset or cleared posts nothing.
- **One chain.** Every message is appended after the session's active leaf (`chat_threads`) inside the same reserved
  transaction, so concurrent writes never fork the thread.
- **The watchdog** (`services/chat/watchdog.py`, started by `main.py`) sweeps every `runs.watchdog_interval_s` and
  ends the runs nothing will finish (codes above). A pending run's wait (`runs.pickup_timeout_s`) counts from the
  later of its creation and the server's start.

## Client

`client/src/features/chat/` is the shared chat; only its `index.ts` is public (an ESLint rule keeps the rest
private, tests excepted). Hosts give it a `ChatHost` (`host.ts`): the session and scope, who answers, whether the
message box sends now, waits for Resume or is closed, and what sits around the conversation (notices, a top slot, a
slot after the thread, a footnote, how to tell the owner something). Home's employee page (`EmployeeChat`) and the
editor's console pane (`ConsoleChat`, compact, scope `live`) are the two hosts.

- **Run events** reach `stores/chatRunStore.ts` through one `case 'chat_run_event'` in `WebSocketContext.tsx`.
  `lib/agui/events.ts` checks each frame (source, type prefix, `subject`, `id` = `<run id>:<seq>`, scope fields) and
  `lib/agui/reduceRun.ts` folds it, the same way `services/chat/reducer.py` does. Frames are folded once per
  animation frame. Per run: a duplicate `seq` changes nothing; a gap, an unknown run already past `seq` 1, a new
  `hub_epoch` or the hub's resync frame mark the session `syncing`, hold what arrives meanwhile, and ask for a fresh
  snapshot, after which the held frames fold in and the ones the snapshot covers drop out.
- **Subscribing** (`data/runs.ts`): a mounted chat subscribes its session whenever the socket is ready (so again
  after every reconnect) and whenever the store asks for a snapshot, retrying a failed subscribe after 1, 3, then
  every 10 seconds; the last chat following a session unsubscribes it. Runs the store held as live that a snapshot
  no longer lists ended unseen and are read once with `get_chat_run`. Until the first snapshot the thread's own
  `active_runs` stand in; after it the store alone says which runs are live, and a message whose `run` still reads
  live is read with `get_chat_run` (`useThreadRunReconcile`).
- **Turns** (`thread/model.ts`): one per message with a divider where `run_key` changes. A run that is going,
  failed or was stopped shows on its last answer, or on a turn of its own right after its last message before it
  has answered (a live run whose messages are out of view goes last). Failures whose code only says no answer came
  (`not_delivered`, `timed_out`, `interrupted`) disappear once an answer lands; `reset` and `cleared` never show. A
  message sent from this tab and its saved row share a key (its `client_message_id`), and a run's first answer takes
  the key its run's turn had, so neither remounts.
- **Sending** (`data/send.ts`): the message shows at once; the server's answer admits its run into the store
  (`queued` or `pending`), so the employee shows working before the run's first event. While the lane is held, Send
  waits. A refused or failed send takes the message out of the thread and puts its text back in the box
  (`state/composerStore.ts`, a draft per session that survives switching conversations); after a failure in transit
  the draft keeps its `client_message_id`, so sending it again is the same message.

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
| `invalid_request` | send, save | A malformed field (`detail` says which): an empty message, a role other than the owner's, or a bad `client_message_id`. |
| `read_failed` | get_chat_messages, chat_subscribe | The thread could not be read. Never answered as an empty thread. |
| `not_found` | get_chat_run | No such run. |
| `conflict` | edit, regenerate, switch, set_ask_first | `expected_revision` is stale. |
| `not_editable` | edit, regenerate, decide | The message belongs to an older generation, its prefix was compacted, or an edited argument is not editable. |
| `ui_event_rejected` | send | The element or action does not match the saved spec. |
| `access_denied` | all | The socket's principal does not own the workflow, or it is the internal worker socket. |
| `too_late` | decide | The Undo or Restore window has passed. |
| `speech_unavailable` | transcribe_audio | No dictation provider has a stored key. |

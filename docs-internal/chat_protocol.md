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
[Runs](#runs)); the answer streaming as text events, working steps and Stop (see [Streaming, steps and
Stop](#streaming-steps-and-stop)); generated UI in replies with its state and button presses (see [Generated
UI](#generated-ui)); and the shared chat UI on both hosts (see [Client](#client)). Every other event, handler and
part below is the contract the later phases build to.

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
  (`data: {session_id, hub_epoch, name, value}`, no `run_id`); the client then takes fresh snapshots. Text deltas
  are batched before they are published (see [Streaming, steps and Stop](#streaming-steps-and-stop)), not merged in
  the queue.
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
| STEP_STARTED | `step.started` | `step_id` (the tool call's id), `step_name`, `icon?` |
| STEP_FINISHED | `step.finished` | `step_id`, `step_name`, `state` (`done`, `failed`, `skipped`), `detail?` (at most 200 chars), `duration_ms`, `narration?` |
| TEXT_MESSAGE_START | `text.started` | `message_id` (segment id `{run_id}.{iteration}.{attempt}`), `role: "assistant"` |
| TEXT_MESSAGE_CONTENT | `text.content` | `message_id`, `delta` (never empty) |
| TEXT_MESSAGE_END | `text.ended` | `message_id`, `final` (bool), `reply_message_id?` (with `final: true`) |
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

**Text segments.** Only the agent that answers the owner streams text. A segment that ends with `final: false` was
written beside tool calls (narration, "Let me check the calendar."); the segment with `final: true` is the reply.
Text that could still turn out to be `NO_REPLY` or a `<followups>` block is held back on the server and never
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
| `stop_chat_run` | `{run_id}` | `{success, run_id, state: "stopping" \| "stopped"}`; `not_stoppable` (with `state`) for a run that ended otherwise |
| `chat_ui_state` | `{session_id, part_id, changes: [{path, value}]}` (at most 32; the last value per path wins) | `{success, part_id, state_revision}`; `not_found`, `invalid_request` |
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
created_at, started_at, finished_at, steps, segments: [{message_id, text, final}], activities: [{message_id,
activity_type, content, patches}], interrupts, outcome, result, error, error_code}`. `state` is `queued`, `pending`,
`running`, `stopping`, `finished`, `error` or `stopped`. A snapshot read after a server restart has its steps but no
text segments or activities (those are stored with the reply).

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
| `canvas_versions` | `nodes/tool/canvas` | `{workflow_id, node_id, item_id}` | `{success, versions: [{version, title, created_at, source, size_bytes}], latest}` |
| `canvas_version` | `nodes/tool/canvas` | `{workflow_id, node_id, item_id, version}` | `{success, item_id, version, title, content, language, created_at, filename}` |
| `transcribe_audio` | `nodes/speech` | `{workflow_id, audio: FileRef, language?}` | `{success, text, provider, model, duration_seconds}` |

## Messages

`get_chat_messages` returns the active path, oldest first:

```
{id, legacy_id, role, kind, text, message, timestamp, run_key, run_id, parent_id, status, attachments,
 parts, feedback, siblings: {index, count, ids}, editable, client_message_id?,
 run?: {run_id, state, outcome, error?: {message, code, hint?}, steps?: [{step_id, name, state, detail?,
        duration_ms?}], duration_ms?}}
```

- `id` is the message's stable id (`m_…` for the owner's, `a_<run id>` for a run's reply; rows saved before chat
  runs are `m<number>`). `legacy_id` is the database row number.
- `message` equals `text`; it is kept for older readers.
- `client_message_id` is echoed on the owner's messages that carried one, so an optimistic row and its saved row
  keep one key.
- `run` says how the run a message started (or answers) stood when the thread was read: its `state`, `outcome`, for
  a failed run the error with the hint the run recorded, the steps it saved, and once it has ended how long it
  worked (`duration_ms`). It is how a reload still shows that a message went unanswered and what the employee did;
  while the session is subscribed, the run's events are fresher (see [Client](#client)).
- Until branches land, `siblings` always holds the message alone and `editable` is false.
- `kind`: `text`, `report` (Post to Talk: no parent, not editable), `action` (a button press), `notice`.
- `status`: `complete`, `stopped`, `error`.
- `siblings` are branches for owner messages and versions for replies.

Parts render in this order, whatever order they were produced in: steps, text, generated UI, artifacts, approvals,
sources, follow-ups.

```
parts: {
  steps?:     {duration_ms, items: [{step_id, name, state, detail?, duration_ms?}]},
  ui?:        [{part_id, spec, state, state_revision, elements}],
  artifacts?: [{workflow_id, canvas_node_id, item_id, version, title, format}],
  approvals?: [{approval_id, tool_call_id?}],
  sources?:   [{n, title, url, detail?}],
  followups?: [string],
  stopped?:   true
}
```

Approvals are joined live from the approvals store; the part only names them.

A run's tools record parts on the run as they go (`chat_run_parts`, keyed so a retried activity writes the same
row; `services/chat/parts.py`). Reply in Chat saves its reply with the parts recorded so far, and the run's end seals
any later ones into it, creating an empty-text reply when the run showed an interface but wrote nothing (sources
alone make none: they show only where a reply cites them); the end is published after the seal, so its
`result.reply_message_id` names a reply that holds them.

## Generated UI

- **Source.** The employee calls the `show_ui` tool (`chatUi` plugin, `nodes/chat/chat_ui`) with a json-render flat
  spec `{root, state, elements}`. `services/genui/spec.py` checks it against `server/config/chat_genui_catalog.json`
  (JSON Schema per component, which prop each input binds, what each layout may hold, the limits): a spec that
  breaks a rule fails the call with every reason, so the model writes it again; unknown types, unreachable elements,
  stray fields and props are left out and reported back. The tool's description is generated from the same manifest,
  byte for byte the same for the same file, and a test on each side reads the other's catalogue. The checked spec is
  saved as a `ui` part and streamed as `activity.snapshot` (empty spec) followed by one `activity.delta` per patch:
  `add /root`, `add /state`, then `add /elements/<id>` in depth-first order (`services/genui/patches.py`, matching
  the handoff's `*.patches.jsonl` line for line). Only the agent answering the owner's chat message can show UI; a
  call anywhere else says nothing was shown.
- **Owner edits.** `$bindState` writes go to the interface's own state store and to `chat_ui_state`; the last write
  per path wins, and the client sends after 300 ms idle, when the page hides, when the chat closes and before a
  button press goes (`features/chat/data/uiState.ts`). The server keeps them on the part (`state`,
  `state_revision`), on the reply once it is saved, so a reload shows them.
- **Buttons.** A press resolves `$state` params at click time; the client tags each press with its element and label
  (json-render hands a handler the params only). `ask` sends its text as the owner's message only when it is what
  the button says (case and spacing aside); otherwise the text goes into the composer for review. Any other action
  becomes `send_chat_message{ui_event: {part_id, element_id, action, params}}`: the server checks it against the
  saved spec (a Button whose press runs that action, params it declares), saves the owner's message as the button's
  label (`kind: "action"`, the press in `meta.ui_event`), starts a run of kind `action`, and sends the employee the
  line `[ui-event]{"ui_id", "element", "label", "action", "params"}[/ui-event]`. A press while the employee is still
  answering is held back with a notice. `ui_event_rejected` says why one does not fit.
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

## Streaming, steps and Stop

Settings are in `server/config/chat_defaults.json` (`stream`, `steps`, `runs`).

- **Who streams.** `agent.prepare_payload` gives an agent a `chat_stream` (`services/chat/stream.py`
  `chat_stream_for`) when the run's `run_scope` reached it, it is not working for another agent (no
  `parent_node_id`), and its output goes straight to a node whose plugin declares `answers_chat_run` (Reply in Chat).
  The payload also carries `chat_run_id` for every agent of the run. Both ride the AgentWorkflow's activity inputs
  only (LLM steps and tool calls), so no workflow patch was needed and recorded histories replay unchanged.
- **Text.** `agent.execute_llm_step` passes a `ChatStreamEmitter` as the provider's `on_event` sink (providers that
  declare `streaming` in `llm_defaults.json` stream; for the others the unifier replays the finished response as
  events: see [Native LLM SDK](./native_llm_sdk.md)). Deltas go out every `stream.flush_ms` or `stream.flush_chars`.
  A retried attempt first sends `opencompany.segment_discarded` for each earlier attempt's segment. Streaming never
  changes the step's result.
- **Steps.** `BaseNode.as_activity` wraps every tool call that carries a `chat_stream` and a `tool_call_id`
  (`services/chat/steps.py`): `step.started` when it begins, `step.finished` when it ends, saved on the run as it
  finishes (`ledger.record_step`, at most `steps.max_per_run`). The label is the plugin's `chat_step`
  ("Searched the web"), else "Used <display name>"; plugins with `chat_step_hidden` (the clock, the checklist) show
  none, and skill loads (`agent.skill.invoke`) never pass through it. A tool may put a short line in its result as
  `_step_detail` ("3 events on Saturday"): it is shown under the step and taken out before the model reads the
  result.
- **Stop** (`stop_chat_run`, `ledger.request_stop`). A run nothing has picked up (pending, or queued until Resume)
  ends `stopped` at once and frees the lane; a workflow that picks it up later claims it still `stopped`
  (`chat_run.start`) and its agent answers nothing. A running run moves to `stopping` (`custom`
  `opencompany.stopping`) and stops itself:
  - its agent's model step polls the run every `runs.stop_poll_s` while the provider writes (an activity heartbeat
    arrives too late to stop a stream mid-sentence), drops the call, and returns what the owner saw so far as the
    final answer; a step about to start returns at once, before paying for a request;
  - a tool call not started yet is answered "Not run: the owner stopped this answer." without running;
  - Reply in Chat saves the partial answer with `status: "stopped"`, and the run finishes with outcome `stopped`.
  
  A run still stopping `runs.stop_grace_s` after Stop (a long tool, a lost workflow) is ended by the watchdog, which
  cancels its Temporal workflow. The conversation keeps the stopped turn: the request and the partial answer (its
  text block marked `stopped`), or the request alone when nothing was written; a tool call a cancelled run left
  without a result is answered when the conversation is next loaded (see
  [Agent Context Flow](./agent_context_flow.md)).

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
  failed or was stopped (or finished, while its answer is on the way) shows on its last answer, or on a turn of its
  own right after its last message before it has answered (a live run whose messages are out of view goes last). Failures whose code only says no answer came
  (`not_delivered`, `timed_out`, `interrupted`) disappear once an answer lands; `reset` and `cleared` never show. A
  message sent from this tab and its saved row share a key (its `client_message_id`), and a run's first answer takes
  the key its run's turn had, so neither remounts.
- **Sending** (`data/send.ts`): the message shows at once; the server's answer admits its run into the store
  (`queued` or `pending`), so the employee shows working before the run's first event. While the lane is held, Send
  is Stop. A refused or failed send takes the message out of the thread and puts its text back in the box
  (`state/composerStore.ts`, a draft per session that survives switching conversations); after a failure in transit
  the draft keeps its `client_message_id`, so sending it again is the same message.
- **A working run** (`turns/AssistantTurn.tsx`): skeleton lines until text comes; then the latest segment, muted
  while it is narration, with a caret while it streams (`ReplyMarkdown` renders each finished block once,
  `markdown/blocks.ts`); a status line saying "Thinking", "Writing · N tok/s" or "Stopping…", with an Esc hint. The
  steps disclosure (`turns/StepsDisclosure.tsx`) sits on the run's first turn: "Working…" and open while the run
  works, "Worked for 12s · 3 steps" after; a run read back later starts it closed. A finished run keeps its turn
  until its saved reply lands in the thread, so the streamed answer and the reply stay one element.
- **Stop** (`data/stop.ts`): the Stop button, or Esc anywhere in the pane, sends `stop_chat_run` for the lane's run
  and applies the answer to the store at once; the run's events take it from there.
- **Generated UI** (`features/chat/genui/`, `turns/GeneratedUiBlock.tsx`): a reply's interfaces come from its saved
  `parts.ui`, or while the run streams them from its `json_render` activities (`data/parts.ts`), the run's first turn
  keeping them until the saved reply carries them, so one element shows throughout and keeps what the owner set. The
  renderer (`genui/ChatUi.tsx`, with json-render, in its own chunk) sanitizes again (`genui/prepare.ts`), draws the
  twelve components (`genui/views.tsx`, through `lib/jsonRender/guard.tsx`), reveals a live one element by element
  (forward only, so patches arriving in bursts never restart it), and routes any button action (`genui/actions.ts`,
  a Proxy over action names). Development builds show the element and patch counts and an Inspect view.

## Notes to the employee

Things the employee should learn on its next turn are kept in `chat_notes` (`services/chat/notes.py`) and put ahead
of that turn's user message, one bracketed line each. Built: `[ui-state]{"ui_id", "state"}[/ui-state]`, written by
`chat_ui_state` for what the owner set without pressing anything; later phases add `[updates]` (approval outcomes)
and `[feedback]`. A note is keyed per session (`ui-state:<part id>`), so a newer one replaces an older one not yet
told. `agent.prepare_payload` claims the session's untold notes for the agent answering a run (the one with a
`chat_stream`); the run's end marks them told when it answered (it finished, or it stopped having written
something), so a run that failed or stopped before writing offers them again, and a note changed after its claim stays
untold. Clearing the chat forgets them.

The answering agent's system prompt ends with a fixed guide (`services/chat/guide.py` `CHAT_REPLY_GUIDE`, byte for
byte the same on every turn): bracketed lines come from OpenCompany, not from the owner; a search result numbered
n is cited as `[n]`; and how to suggest follow-ups.

## Follow-ups

The answering agent may end its reply with `<followups>["…", "…"]</followups>` (a list of lines works too). The
stream holds the block back; Reply in Chat takes it off the reply (`guide.split_followups`: the last block, at most
3 suggestions, 160 characters each, repeats dropped) and saves the suggestions as `parts.followups`. Outside a chat
run the block is dropped, and a message that is only the block posts nothing. The chat shows them as buttons under
the latest answer only, once it is done; one sends its text as the owner's message, or waits in the box while an
answer is still coming.

## Sources

A plugin that declares `chat_sources` (the web searches) returns `results` with addresses. When the answering
agent calls it, `BaseNode.as_activity` numbers them for the conversation (`services/chat/sources.py`: the session's
counter `chat_threads.next_source`, so a number never repeats in a conversation and an older `[n]` keeps meaning
what it did), writes `n` on each result the model reads, and saves `{n, title, url, detail?}` (at most 10 per call)
as a `sources` part of the run; the reply carries them as `parts.sources`. The guide tells the agent to cite what it
relies on as `[n]`. The client numbers a reply's sources 1, 2, ... in the order its text first cites them
(`markdown/citations.ts`): each `[n]` outside code reads as a chip with that number and a tooltip naming the
source, and the cited sources are listed under the answer (`turns/SourceChips.tsx`); sources it did not cite are
not shown. A reply may cite a source an earlier search found: each answer resolves `[n]` against the conversation's
sources up to it (`thread/model.ts`, `ChatTurn.sources`).

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
| `not_found` | get_chat_run, stop_chat_run | No such run. |
| `not_stoppable` | stop_chat_run | The run ended before Stop reached it; `state` says how. |
| `conflict` | edit, regenerate, switch, set_ask_first | `expected_revision` is stale. |
| `not_editable` | edit, regenerate, decide | The message belongs to an older generation, its prefix was compacted, or an edited argument is not editable. |
| `ui_event_rejected` | send | The element or action does not match the saved spec. |
| `access_denied` | all | The socket's principal does not own the workflow, or it is the internal worker socket. |
| `too_late` | decide | The Undo or Restore window has passed. |
| `speech_unavailable` | transcribe_audio | No dictation provider has a stored key. |

# Normal mode ("Home")

Normal mode is the landing screen for owners who are not technical. They
describe a job in plain words, an LLM on the server drafts a setup screen for
a new AI employee, they adjust it and press **Hire**, and the server builds,
saves and starts a workflow that does the job. Each employee appears in a
sidebar with a live status, and the owner talks to them on their page
(Talk), where they can also be asked to take on new tools and skills. The
workflow editor is **Dev mode**, one switch away.

**One employee is one workflow.** The workflow id is the employee id and the
workflow's name is the employee's name. Workflows created in the editor show
up in Home too, with details derived from their graph.

| Layer | Where |
|---|---|
| Shell (screen switch, mode toggle, theme rule) | [client/src/app/](../client/src/app/), [components/shell/ModeToggle.tsx](../client/src/components/shell/ModeToggle.tsx) |
| Home UI | [client/src/features/home/](../client/src/features/home/) |
| Setup-screen pipeline (parse, normalise, render) | [client/src/features/home/genui/](../client/src/features/home/genui/) |
| Employees service (list, setup, hire, start, run records) | [server/services/employees/](../server/services/employees/) |
| Approvals (the "ask me first" step) | [server/services/approvals/](../server/services/approvals/), node [approvalGate](./node-logic-flows/workflow_triggers/approvalGate.md) |
| Talk (the chat thread and the talk line) | [server/services/chat_thread.py](../server/services/chat_thread.py), [services/employees/talk.py](../server/services/employees/talk.py), node [chatReply](./node-logic-flows/chat_utility/chatReply.md) |
| Growing a saved employee (Turn on Talk, the Agent Builder, Apply) | [server/services/graph_build.py](../server/services/graph_build.py), [services/employees/policy.py](../server/services/employees/policy.py), [services/workflow_storage/mutate.py](../server/services/workflow_storage/mutate.py), [services/deployment/restart.py](../server/services/deployment/restart.py), node [agentBuilder](./node-logic-flows/ai_tools/agentBuilder.md) |
| Built-in skills offered to new hires (Settings > Skills > Discover) | [server/skills/employee/](../server/skills/employee/) |

The design reference is the `design_handoff_opencompany_home/` bundle (kept
out of git; the 2026-09-25 export). Where its token values differ from the
repo's, the repo wins. Its README's Settings section predates the Settings
redesign, so for Settings the prototype (`reference/OpenCompany Home.dc.html`)
is the reference.

## Switching screens

- **Flag**: `featureFlags.normalMode` ([lib/featureFlags.ts](../client/src/lib/featureFlags.ts))
  is on by default; `VITE_NORMAL_MODE=false` turns Home off. The app then
  opens straight into the editor, and the toolbar's Normal/Dev switch goes back
  to filtering the component palette.
- **State**: `useAppStore.shellMode` (`'normal' | 'dev'`), persisted as
  `ui_shell_mode`. While that key is unset it comes from the old palette
  choice: `ui_pro_mode === 'true'` starts in Dev.
- **Switching** goes through `enterNormal` / `enterDev` in
  [app/useShellActions.ts](../client/src/app/useShellActions.ts), never
  `setShellMode` directly. `enterDev({workflowId})` settles unsaved editor work
  first (saves it when auto-save is on, asks otherwise), opens the workflow and
  preloads the editor chunk; `enterNormal` preloads Home. Then
  [app/shellTransition.ts](../client/src/app/shellTransition.ts) fades the
  current screen out and swaps. Requests during a switch coalesce. The toggle
  lives in the editor toolbar and the Home header; Ctrl/Cmd+Shift+D toggles too.
- **Screens are lazy chunks** ([app/ShellModeSwitch.tsx](../client/src/app/ShellModeSwitch.tsx)),
  so ReactFlow stays out of a Normal-mode session and Home out of a Dev one.
- **App-level concerns** live in [app/AppShell.tsx](../client/src/app/AppShell.tsx)
  rather than in `Dashboard`: the `.app-frame`, boot effects (current workflow,
  sound sync, page activity, UI defaults, the shortcut), and the Settings and
  Credentials dialogs.
- **Themes**: Home shows only light and dark.
  [app/ShellThemeProvider.tsx](../client/src/app/ShellThemeProvider.tsx) passes
  `baseOnly` to `ThemeProvider` while Home is showing, so a stylized theme
  chosen in the editor appears as its family's base (Atomic as light, Cyber as
  dark) and applies again in Dev mode. See [Theme System](./theme_system.md).

## The Home screen

[features/home/](../client/src/features/home/):

| Folder | Contents |
|---|---|
| `HomeShell.tsx` | Sidebar, header, the current view (hire or one employee), the Workspace dock, Settings, the connect dialog, the guided Connect an AI model dialog, the orb's stage, and under an employee's page a line saying whether they ask first |
| `sidebar/`, `header/` | The team list, New employee, the profile row; the view title, the Workspace pill, the mode toggle (on an employee's page, Dev opens their workflow) and the theme button |
| `hire/` | The hero, the composer, the template chips, the hire notice (`HireNotice.tsx`), and the starter bundles (`starters.json`), which the chips and Settings > Plugins both read |
| `genui/` | The setup draft under the composer, and the hire itself, from a setup or a starter (below) |
| `employee/` | One employee's page, which is the conversation with them: only their name under a small orb, then Talk (below). What to act on shows in the conversation only while there is something to do: the drafts waiting for the owner after the messages, and above the message box their main action while they can't read messages (Resume, Start, or connect what is missing; `useEmployeeControl`, `PrimaryActionButton`) or Help in browser while they wait there. It never says why they stopped. Pausing them, and watching them work, is the Workspace's: the header's Workspace pill opens it on the employee on screen, and its header carries the same main action |
| `connectAI/` | The guided Connect an AI model dialog (below) |
| `workspace/` | The Workspace dock (below), its header pill, and its Canvas tab, which loads in its own chunk |
| `settings/` | The Settings pages (Profile, Billing, Skills, Connectors, Plugins), the shared catalog page the last three build on, and `ConnectDialog` (the editor's credential panel for one provider, in its compact variant) |
| `approvals/` | The drafts query, the decide mutation (optimistic), the approval broadcast listener |
| `data/` | zod-parsed queries for employees, connectors and the profile; `talk.ts`, the thread and its mutations; `presentation.ts` maps server state to pills, actions and the message box's mode without deriving new rules |
| `orb/` | The 3D orb (below) |
| `ui/` | Small shared pieces (avatar, status dot and pill, app mark), the pill toast, and `useAutoGrow` (the composer's and the message box's growing text area) |
| `state/homeStore.ts` | UI state only: the view, the sidebar, Settings, the Connect an AI model dialog, the last hire's notice, the Workspace dock, one-shot glow and pulse signals |

Status comes from the server: an employee summary is `working`, `ready`,
`paused` or `attention` (an automatic pause, see below), and a pending draft
shows as "Needs you". So does a browser waiting for the owner: when an agent
calls `request_user`, the summary's `browser_request` (`{node_id, reason,
since}`, never the agent's message, since summaries reach every socket) is
said above the message box on their page, beside Help in browser, which opens
the Workspace on its Browser tab. The Browser plugin publishes
that state through `services/employees/node_signals.py` and re-sends the
summary when it changes. Motion goes through [lib/motion.ts](../client/src/lib/motion.ts),
which reads the `--dur-*` / `--ease-*` tokens, runs at 1 ms under reduced
motion and while the page is hidden ([lib/pageActivity.ts](../client/src/lib/pageActivity.ts)),
and never starts loops then. Toasts are a second sonner toaster
(`ui/pillToast.tsx`): one bottom-centre pill at a time.

### The orb

A 3D version of the logo drawn behind Home's content
([orb/orbEngine.ts](../client/src/features/home/orb/orbEngine.ts), a port of
the design prototype's scene on current three.js; loaded in its own chunk the
first time it runs). [orb/orb.ts](../client/src/features/home/orb/orb.ts) holds
what the engine reads each frame (the slot to fill, an energy target, a spike)
and its lifecycle: leaving Home keeps the renderer, the app shell disposes it.
`OrbStage` is the canvas host below the header; each view's `OrbSlot` reserves
the square the orb glides into (`--size-orb-hire`, `--size-orb-employee`:
large on the hire view, small on an employee's page, where the conversation
needs the room). The composer sets the energy target (focused, holding text,
a setup being written). These spike it: a hire, a theme or mode
switch, a connect, a task change, opening Settings or the Workspace, a setup
arriving or failing, and saving the profile (`SPIKE` in orb.ts). In dark the orb keeps the
logo's colours with white particles and packets; in light it is glossy
piano-black under a white rim light. Its glows and particles blend additively
in dark and normally in light (additive glow vanishes on white), fading through
zero at the midpoint of a theme change. Without WebGL, under reduced motion, or
after a lost WebGL context, the slot shows the static mark.

## The Workspace

A dock on the right of Home ([workspace/WorkspaceDock.tsx](../client/src/features/home/workspace/WorkspaceDock.tsx))
that shows what one employee is working on. The header's Workspace pill
opens and closes it, and carries a blinking dot while it is closed and
someone is working. It shows the employee last opened (their page, or their
Help in browser), else the first on the team.

- **Header**: the avatar, "{Name}’s workspace", the live task line when
  there is one (`useLiveTask`, else the summary's task), and a pill: Live
  while the employee works, otherwise their status (Ready, Paused, Needs
  attention, Needs you). Then their main action (Pause, Resume, Start, or
  connect what is missing), the one place to pause them in Normal mode, and
  Expand and Close.
- **Canvas**: the board named by the summary's `canvas_node_id`, drawn by
  the editor's Canvas renderer (`CanvasContent`, see [Canvas Node](./canvas_node.md)).
  It loads in its own chunk, which keeps the board's markdown, code and
  JSON viewers out of Home's, and refreshes on `canvas_updated` like the
  editor's hosts. An employee without a Canvas gets a note and Open
  workflow.
- **Browser**: the shared `components/browser/BrowserWorkspace` attaches to
  the employee's saved Browser nodes, supplied by `browser_nodes` in its
  summary. Multiple nodes get a selector. Running sessions appear automatically;
  Start browser opens an idle session. The view supports navigation, tabs,
  Take control / Hand back, and browser dialogs. Frames use `/ws/browser`,
  not a URL iframe. Help in browser, above the message box on the
  employee's page while the agent waits for the owner, opens the Workspace
  on this tab. See
  [Browser workspace](./browser_workspace.md).
- **Android**: a shared panel explains that live mirroring is not yet available.
  Dev mode uses the same three workspace tabs and browser viewer.
- **Size and motion**: 460px wide by default. The left edge drags from
  360px to the window less 420px, and Expand gives a bigger dock without
  a drag. At 1100px and wider the dock pushes the page aside; narrower, it
  lies over it with `--shadow-dock`. Like the sidebar it stays mounted and
  transitions its width (`--dur-dock-in` to open, `--dur-sidebar-out` to
  close), so a reload with it open does not animate, and its contents
  mount on the first open. Open, width and tab persist under
  `home_workspace_v1`; Expand and the employee last only for the session.

## Hiring

### 1. The setup screen

`generate_employee_setup {job, refine?, history?, draft_token}`
([services/employees/setup.py](../server/services/employees/setup.py)):

- **The server builds every message** ([setup_prompt.py](../server/services/employees/setup_prompt.py)):
  a preamble, the component catalogue, and a context block
  ([context.py](../server/services/employees/context.py)) with the connected
  and connectable apps, the team's names, the owner profile, the local time and
  whether an AI model is set up. A change request carries the latest reply
  plus at most two earlier turns, within a character budget.
- **Model choice** ([llm.py](../server/services/employees/llm.py)): a local
  provider when the owner prefers local AI, else the global default provider
  when it has a key, else the first usable provider, else the first saved
  endpoint. Calls go through `ChatUnifier` with a deadline (longer for local
  models). One retry when the reply was cut off or cannot be salvaged. Usage is
  recorded for every call.
- **One request in flight per owner**: a newer `draft_token` cancels the older
  call, and `cancel_employee_setup` cancels explicitly. The payload field is
  `draft_token` because `request_id` is the WebSocket correlation id.
- **Privacy**: neither the job nor the reply is ever logged.
- **Apps**: the response's `apps` maps each app the reply mentions to its
  AppRef plus `can_trigger` (the app can start the work), so the screen can
  offer "When something new arrives in Gmail" without its own copy of the app
  registry.
- **No preferred trigger**: every employee can be talked to on Home, so in
  the prompt one that works when the owner messages them is as good as any.
  The Hire button's `trigger` carries a schedule's time as one of the
  catalogue's `trigger.times`.
- **Errors**: `no_ai_provider` (the guided Connect an AI model dialog opens,
  below; the owner's words stay in the draft, which is sent again by itself
  once a model is connected), `timeout`, `provider_error`, `unparseable`,
  `cancelled`, `busy`, `invalid_request`.

A template chip under the composer fills the box with its starter's job for
the owner to read, change and send; it never sends by itself. While the box
still holds that job as written, the chip shows as picked with the starter's
summary and **Hire now** (see [One-click starters](#one-click-starters)).
While the model writes, the draft panel shows one honest line, "Writing their
setup…", with the time so far and Cancel, and after `SLOW_AFTER_SECONDS` a
note that some models take a few minutes. The screen's layout JSON is shown
in development builds only.

The reply is a flat JSON UI spec. The client parses, repairs and normalises it
and renders it from a fixed component catalogue
([genui/](../client/src/features/home/genui/): `catalog.ts`, `parse.ts`,
`normalize.ts`, `expressions.ts`, `render.tsx`). The normaliser keeps the
root a vertical stack, enforces a tree, guarantees one Hire button, one
change button, the "Ask me before sending anything" toggle (on by default)
and one `Schedule` (below), and caps sizes. The server mirrors the catalogue in
[config/genui_catalog.json](../server/config/genui_catalog.json);
`server/tests/test_genui_catalog_sync.py` keeps the two in step, and a shared
corpus of bad model replies (`genui/__fixtures__/replies.json`) is parsed the
same way on both sides. Only what `genui/index.ts` exports (`HireDraftPanel`,
`useHireComposer`, `DraftMessagePreview`) leaves the folder: ESLint refuses
imports of its internal modules.

**When they work.** Every screen shows one `Schedule` after the routine. The
normaliser inserts it (the catalogue marks it `inserted`, so the model is
never offered it) and binds it to `/trigger` (`state_paths.trigger`). It starts
as the Hire button's `trigger`, else a new message in the app the routine's
first "When" step names, else the owner messaging them, snapped to what the
server builds: a known kind, a frequency, a time from `trigger.times` (the
builder's `SCHEDULE_TIMES`; `test_genui_catalog_sync.py` holds them equal), and
a weekday or day of the month. It reads as one sentence ("Every weekday at
08:00"). Edit offers the owner messaging them, a schedule,
or a new message in any app whose `can_trigger` is true; a change rewrites the
routine's "When" step, and the hire payload reads `/trigger` before the
button's params. `render.tsx` labels the routine "Their routine" and its steps
in plain words (When, They, Using, Then).

### 2. Hire

`hire_employee` ([hire.py](../server/services/employees/hire.py)) takes a
`HireEmployeeRequest` ([hire_request.py](../server/services/employees/hire_request.py);
the client's `HIRE_PAYLOAD_KEYS` must match, locked by
`test_hire_payload_contract.py`) and:

1. checks size and shape, then the idempotency key: the same key with the same
   payload returns the employee already hired, a different payload `conflict`,
   and one whose first attempt is still building `busy`. A row a failed attempt
   left behind is resumed, and so is one still marked building after
   `BUILDING_STALE_AFTER` (the server stopped mid-way);
2. resolves the apps it names through the app registry
   ([config/employee_apps.json](../server/config/employee_apps.json),
   [apps.py](../server/services/employees/apps.py)): each app's provider,
   its trigger / reply / notify-owner / tool nodes with parameter templates,
   and a `side_effects` class. Apps the registry does not know are recorded as
   unsupported, named in the instructions, and never block a start;
3. builds the graph ([builder.py](../server/services/employees/builder.py),
   pure): one trigger (an app event, a schedule on `cronScheduler`, or manual
   chat), one `aiAgent` whose system message comes from
   [prompt.py](../server/services/employees/prompt.py), always-on tools (web
   search, todos, clock, a Canvas, plus memory when the owner allows it) and
   the apps' tools, the owner's skill library, delivery, an "Activity
   log" console node, and Talk ([the talk line](#the-talk-line)). Which tools
   and skills a hire may have is
   [policy.py](../server/services/employees/policy.py)'s rule, the same one
   the Agent Builder applies later. A schedule is recorded as it will run, in
   the owner's time (`cronScheduler` runs only at `SCHEDULE_TIMES`, in a short
   list of zones), with a warning when that is not what the owner asked for.
   Ids, labels and edges come from
   [services/graph_build.py](../server/services/graph_build.py), shared with
   every server-side graph writer, and
   `tests/fixtures/employee_builder_snapshot.json` pins the output
   (`UPDATE_BUILDER_SNAPSHOT=1` rewrites it). The instructions ask the
   employee to put finished
   work the owner will want to look at later on its Canvas, and to leave
   routine replies off it. The library is every skill that is on in Settings > Skills. It goes
   on one Skills node (`masterSkill`) with each skill's text copied in, so a
   later change to the library never alters an employee already hired. A
   skill named `skill`, or one ending in `-personality`, is left out: it would
   take over the Skill tool, or replace the whole system message. With "Ask me before
   sending anything" on, a reply goes through `approvalGate`, and tools that
   send or spend money are left off, except one that declares
   `ask_first_params`: the "Web browser" app's browser stays on with
   `interaction: read_only`, so it can read pages and hands any change to the
   owner through `request_user`. The Web browser app has nothing to connect
   (its catalogue entry's `connected_check` is `builtin`); its Connectors
   panel manages optional login profiles. Every node type must pass
   `node_allowlist.is_hire_allowed` ([Node Allowlist](./node_allowlist.md));
   node types never come from the payload;
4. validates it with `validate_workflow` and saves it with
   [persist_new_workflow](../server/services/workflow_storage/persist.py)
   (shared with workflow import);
5. records on the row the apps the graph actually uses (`built.app_ids`), so
   an app the hire named but left out (a tool that sends while they ask
   first) never shows as one to connect; summaries read apps off the graph
   the same way;
6. answers `{employee, started, missing_apps, needs_ai, unsupported_apps, warnings, idempotent}`
   and, when every app is connected and an AI model exists, starts the
   employee in the background. Errors: `invalid_request`, `too_large`,
   `conflict`, `busy`, `not_allowed`, `build_failed`, `save_failed`.

On the client ([genui/useHire.ts](../client/src/features/home/genui/useHire.ts))
every successful hire lands on the new employee's page, with a glow in the
sidebar and a toast. The response's `warnings` show there once as the hire
notice ("A few notes on {Name}'s setup", `hire/HireNotice.tsx`), until the
owner dismisses it or opens another view; `needs_ai` opens Connect an AI
model; every error code has plain words (`busy`: they are still being set
up).

### 3. Start, pause, resume

`start_employee {workflow_id, expected_revision, idempotency_key}`
([start.py](../server/services/employees/start.py)) refuses with
`missing_apps` or `needs_ai`, moves every agent the employee has (the worker
and the agent it talks to the owner through, `node_roles` `agent` and
`talk_agent`) onto a usable model, and calls `start_saved_workflow`, the same
start path the editor uses. One that stopped after a problem is reset first
(`reset_if_failed`); the reset moves its control revision on, so the revision
the page sent is checked here instead. Pause and Resume are the editor's
`pause_workflow` / `resume_workflow`.

Automatic pauses now record why: `WorkflowControlExecution.pause_reason`
(`failures` from the circuit breaker, `recovery` after a crash,
`controller_missing`) and `pause_detail`, emitted by `serialize_control` and
cleared on resume. Home shows such an employee as "Needs attention". See
[Temporal Workflow Control](./temporal-workflow-control.md#recovery-policies).

### One-click starters

Each starter bundle in `hire/starters.json` (Receptionist on WhatsApp, Inbox
assistant on Gmail, Social media helper every week, Daily briefer every
weekday morning) carries a `hire` block, the starter's own setup: names to
pick an unused one from, the role, a one-line description, the routine, what
starts the work and the app it answers through (`hire/templates.ts` parses it
with zod). **Hire now**, on a picked chip or as Hire on a Settings > Plugins
card, puts the starter's skills in the library (switching on any that are
off), then hires that setup as it stands with "Ask me before sending
anything" on (`genui/useStarterHire.ts`, `starterHirePayload` in
`genui/hirePayload.ts`). It shares the one hire slot with the setup screen
and lands like any hire. `test_home_catalog_contract.py` builds every starter
that way and fails when one loses an app it names or the way it starts.

### Connecting an AI model

[connectAI/ConnectAIDialog.tsx](../client/src/features/home/connectAI/ConnectAIDialog.tsx)
is the guided dialog for an owner who has never made an API key. It lists the
featured providers of onboarding's Connect your AI step
(`components/onboarding/aiProviderLinks.ts`: a hint and the page where the key
is made), with names, marks and connected state from the credential
catalogue. Picking one opens that provider's own panel, compact, with a link
to its key page, and the dialog closes once the provider connects; "See all
AI models" opens Settings > Connectors on AI. `homeStore.openConnectAI()`
opens it when a setup answers `no_ai_provider`, after a hire that answers
`needs_ai`, from the page's Connect an AI model, and when Start is refused
with `needs_ai`. The key-page links live in `aiProviderLinks.ts`, not in the
credential catalogue.

## Talk

The owner talks to an employee on its page. The conversation is the chat
session whose id is the employee's workflow id, the thread the editor's chat
pane also shows for that workflow (there, only the live generation). Every
row goes through
[services/chat_thread.py](../server/services/chat_thread.py): the owner's
messages from `send_chat_message`, answers and reports from the
[chatReply](./node-logic-flows/chat_utility/chatReply.md) ("Reply in Chat")
node. Each row is stamped with the live generation, and each insert or clear
is announced as `chat.updated`. Clearing the chat in the editor's chat pane
(`clear_chat_messages`, `chat_thread.clear_chat_session`) deletes the thread
and, through the Context plugin's chat-cleared listener, every conversation
of the workflow, so the employee starts over with the chat.

### On the employee's page

[employee/EmployeeTalk.tsx](../client/src/features/home/employee/EmployeeTalk.tsx)
sits under the name: the thread, then a message box pinned to the bottom of
the page (`HomeShell`'s scroll area is a column the view fills). Its queries
and mutations are in [data/talk.ts](../client/src/features/home/data/talk.ts).

- **The thread** is `get_chat_messages` with `all_generations: true` (the
  newest `THREAD_LIMIT` messages): the conversation since the employee last
  started, since a Reset clears it (see [Turn on Talk and Apply](#turn-on-talk-and-apply)).
  Where a message's `run_key` (its generation) differs from the one before
  (a message left from before a Start, such as a test run in Dev mode), a
  divider reads "{Name} restarted — they start fresh from here". Both sides
  are one bubble, the same raised surface, border and type in every theme;
  only the side (the owner's on the right, answers on the left beside the
  avatar) and the corner that points at the speaker differ. Answers render
  as markdown (`ThreadMarkdown`, in its own chunk). The bubbles carry
  `chat-msg-user` / `chat-msg-bot`, the theme hooks the editor's chat pane
  uses too. Times, "Working…" and the divider share the body font. An empty
  thread is just the box. The list is an `aria-live` log. It refetches on
  `chat.updated`, after a runtime reset, and when the socket reopens. The
  drafts waiting for the owner's OK follow the messages (`EmployeeTalk`'s
  `drafts`).
- **The box** follows the control state the way `send_chat_message` does
  (`talkMode` in `presentation.ts`):
  - *send* (running, starting, resuming): a message shows at once
    ("Sending…") and goes to the employee. "Working…" then holds the box
    until an answer arrives. `useReplyWait` follows the talk agent's node
    status in `nodeStatusStore` (for any workflow, not only the one open in
    Dev mode) and gives up after `PICKUP_WAIT_MS` if the agent never starts,
    `REPLY_WAIT_MS` while it works, or `SETTLE_WAIT_MS` after it stops, then
    says "No answer from {Name} yet." Holding the box matters: overlapping
    runs would each save over the other's conversation.
  - *queue* (paused, pausing): one message waits for Resume ("Your message is
    waiting…"), and the box holds until the employee runs again.
  - *start* (never started, ready, resetting, failed): no box; a line says
    they can't read messages, beside their main action (Start, or what they
    are missing), which the Workspace header offers too.
  - While the agent waits for the owner in the browser, the server's line
    for it ("Needs you to sign in to a site in the browser") sits above the
    box beside Help in browser.
  - A refused message leaves the thread and its text goes back in the box;
    `not_running` (the state moved meanwhile) says "{Name} isn't running" and
    refetches the team.
- **Turn on Talk**: an employee whose `talk.state` is `off` (hired before
  Talk, or built in Dev mode) shows Turn on Talk instead of the thread. An
  `AlertDialog` confirms first, since it restarts them, and says so when drafts
  are waiting (a restart cancels them). `unsupported` gets a note that their
  setup has no way to answer.
- **Pending changes**: while `pending_changes` is true, a notice above the box
  reads "{Name} has new abilities for this conversation. Apply to make them
  part of all their work (restarts {name} and clears this conversation)."
  Its Apply (`ActionButton intent="config"`) calls
  `apply_employee_changes`.
- **Asking first**: under the page, a line says whether the employee asks
  before sending anything on the owner's behalf (the summary's `asks_first`).

### The talk line

A run keeps only the firing trigger's downstream nodes plus their tools and
config, so a line started by the owner's messages runs on those alone and
never disturbs the work path ([talk.py](../server/services/employees/talk.py)):

```
chatTrigger (session_id = workflow id) -> agent -> chatReply ("Reply in Chat")
```

- **A chat hire** (the owner gives them work by messaging them): its own
  "Chat" trigger and agent are the line, and Hire adds Reply in Chat after the
  agent. No second chat trigger is added: two on one session would start two
  runs per message.
- **App-event and schedule hires** get a line beside the worker: a "Talk" chat
  trigger on the workflow's session; a "Talk with {Name}" agent of the
  worker's type, on its provider and model, with the prompt `{{talk.message}}`
  and instructions written for Talk; its own Context (a conversation is never
  shared); the worker's tools and Skills node, when it has one, wired to it
  too (the same nodes, so Memory, the checklist, the Canvas and the skills are
  shared); and its Reply in Chat.
- **Schedule hires** also get "Post to Talk", a Reply in Chat fed by the
  worker, so its routine reports land in the thread. When the app the reports
  go out through can't be used yet, the hire's warning says they are in Talk
  meanwhile.
- **The Agent Builder**: the agent that answers the owner (a chat hire's
  agent, or the talk agent) gets the `agentBuilder` tool. A worker that
  strangers write to (a public app trigger) never has it.
- Answers and reports go out only when the agent had something to say: the
  edge into Reply in Chat carries `result.response neq NO_REPLY`.
- The talk agent's instructions use prompt.py's `talk` delivery: the answer
  goes straight to the owner, and it may add tools and skills with
  `agent_builder` when the owner asks, but nothing that sends or spends while
  they ask to check first.
- The parts join `node_roles`: `talk_trigger`, `talk_agent`, `talk_context`,
  `talk_reply`, `builder`, `report_post`. A chat hire's talk trigger, agent
  and Context are its `trigger`, `agent` and `context`.
- Labels are "Talk", "Talk with {Name}", "Reply in Chat", "Post to Talk" and
  "Agent Builder", numbered when a label is taken ("Talk 2"). A new trigger's
  label is also kept unique by `node_label_slug`, since a deployment registers
  one listener per trigger label slug.

`talk_state` reads a graph: `on` when a chat trigger feeds an agent (any
registered agent type) that answers through Reply in Chat; `off` when one step
adds it, either a chat trigger feeding an agent with no reply yet, or no chat
trigger and an `aiAgent` / `chatAgent` a talk agent can copy; `unsupported`
otherwise (a chat trigger that feeds no agent counts here). While a
generation is live the summary reads the running snapshot, whose node ids are
the ones that report status; otherwise the saved graph. `talk.agent_node_id`
is the agent that answers. It stays out of `watch_node_ids`, and `useLiveTask`
skips it: while it works, the conversation shows "Working…", and the
Workspace's task line stays on their other work.

### Turn on Talk and Apply

Both live in [handlers.py](../server/services/employees/handlers.py), run one
at a time per employee, and answer with the employee's fresh summary, which
the client puts in the team cache.

- **`enable_employee_talk`** plans the line (`plan_talk_line`), adds it
  through `apply_graph_additions` (one transaction; an editor with the
  workflow open adopts it), records its roles on a hired row
  (`store.merge_node_roles`), and restarts the employee on the saved graph. A
  hired employee's talk agent gets instructions written again from its hire,
  the Agent Builder, and, for a schedule worker, Post to Talk. A workflow built
  in Dev mode gets a talk agent with the worker's own instructions plus a note
  that this is Talk (`talk_addendum`), and neither extra. Talk already on
  succeeds without a restart unless saved changes are waiting.
- **`apply_employee_changes`** restarts the employee on the latest saved
  graph.

A restart is `restart_with_latest_graph`
([services/deployment/restart.py](../server/services/deployment/restart.py)):
running → Reset, then Start again (ends running); paused or failed → Reset
(ends ready, and the next Start takes the saved graph); ready or never
started → nothing to do; starting, pausing, resuming or resetting →
`conflict`. The same idempotency key reports the restart it already made. A
Reset ends the chat session with the generation: the Context node clears
the workflow's conversations and the chat nodes (`chatTrigger`, `chatReply`)
clear its thread through the same `reset_execution_state` hook, so Talk and
the editor's chat pane never show a conversation the agent no longer has.
It also drops messages queued while paused and cancels the drafts waiting
for the owner. Deleting the workflow deletes its thread (a workflow-deleted
hook `chatReply` registers).

`pending_changes` is true while a generation is live (starting, running,
pausing, paused, resuming) and the saved graph, put through Start's own
normalization, differs from its snapshot in anything a run takes from it:
nodes (id, type, label key, disabled) and edges (ends, handles, condition).
Positions, edge ids and list order don't count, so moving a node in Dev mode
is not a change; that is why `control.graph_hash` can't answer it. The
summary refreshes after every save: `save_workflow` and
`apply_graph_additions` notify the graph-changed listeners
([services/workflow_storage/listeners.py](../server/services/workflow_storage/listeners.py)),
where Normal mode registers its coalesced summary refresh.

### Adding tools and skills from Talk

The agent that answers the owner extends the employee with the
[Agent Builder](./node-logic-flows/ai_tools/agentBuilder.md) when the owner
asks, within the rule Hire applies
([policy.py](../server/services/employees/policy.py)):

- **Tools**: the ones every hire gets (web search, checklist, clock, Memory,
  Canvas) and the app registry's. While "Ask me before sending anything" is
  on, nothing that sends or spends, except a tool with `ask_first_params` in
  that form (the browser, read-only). Every type must pass `is_hire_allowed`,
  and an app's tool needs the app connected. A refusal is one plain sentence
  the agent passes on.
- **Skills**: from the owner's library (Settings > Skills, on or off for new
  hires) and the Discover folder (`server/skills/employee/`), with their text
  copied in; never `skill` or a `*-personality` skill.
- **No teammates**: `add_subagent` is refused for a hired employee.
- A new tool is wired to both the worker and the talk agent. A skill goes on
  the Skills node they share, else on a new one wired to both.

What goes live when:

- A tool is callable at once in the conversation that asked for it: the agent
  loop binds it for the rest of that run (`auto_rebind_tools_after_canvas_change`
  in user settings, on by default).
- A skill added to an existing Skills node applies from the next message:
  node parameters are read live.
- Everything else waits for a restart, because a live generation's runs take
  their nodes and edges from its snapshot: the worker gets a new tool, and a
  new Skills node starts working, after Apply. Meanwhile `pending_changes`
  shows the notice. On a later message the talk agent can ask for the tool
  again, which binds the saved node without adding a second one.

## Asking before sending

With the rule on, a reply waits in an `approvalGate` until the owner decides
on the employee's page. The gate stores the draft in `approval_requests`,
wakes the moment it is decided, survives restarts (its idempotency key finds
the same row on every attempt), expires after `timeout_hours`, and fails
closed: nothing is sent unless it was approved, and the recipient always comes
from the run's trigger. Full contract:
[approvalGate](./node-logic-flows/workflow_triggers/approvalGate.md).

The same rule limits what an employee may add to itself from Talk (nothing
that sends or spends; see [Adding tools and skills from Talk](#adding-tools-and-skills-from-talk)),
and a talk agent that asks first is told it cannot send or spend anything for
the owner, only write it for them to send. The summary's `asks_first` is the
hire's rule (for a workflow built in Dev mode, whether it has an approval
gate), shown in a line under the employee's page.

## "Done today"

Every trigger-spawned run that finishes writes a `workflow_run_records` row
([runs.py](../server/services/employees/runs.py)): on Temporal from the
`workflow_runs.record_completion` activity, scheduled at the end of
`MachinaWorkflow.run` behind the `machina-run-record-v1` patch; in-process
from `DeploymentManager`. The count starts at local midnight in the owner's
timezone, and rows are pruned after 35 days, which is longer than a month, so
Billing's count of this month's tasks (from the 1st, in the owner's timezone,
across the whole team) is always complete.

## Settings

A 1040x760 dialog ([settings/HomeSettings.tsx](../client/src/features/home/settings/HomeSettings.tsx))
with a 224px nav. One `PAGES` list drives the nav and the panels. The pages
are grouped under *Settings* (Profile, Billing) and *Customize* (Skills,
Connectors, Plugins), and the nav's search matches each page's label and
keywords, dropping a group with no match. Opening Settings on a category
(`openSettings('connectors', 'ai')`) only sets where the page starts:
changing page clears it.

- **Profile**: `profile_full_name`, `profile_call_name`, `profile_role`,
  `profile_preferences`, `profile_timezone` (written from `Intl` on save),
  `memory_across_chats` and `prefer_local_ai` on `UserSettings`, normalised on
  save by [services/settings/profile.py](../server/services/settings/profile.py).
  Every hire reads them into its instructions.
- **Billing**: usage only. It shows the tasks done this month
  (`get_employee_usage`) and the number of employees in the sidebar. A count
  that can't be read shows a dash, never a zero.
- **Skills**: the owner's library, which is the user-skills table. Each row is
  on (`is_active`) or off for employees hired from now on.
  - *Discover* lists the built-in `server/skills/employee/` folder. These are
    short skills written for AI employees, with no tools to connect, and their
    cards read the SKILL.md `metadata.title` and `metadata.summary`.
  - *Adding* a built-in copies its text into the library under the same name.
  - *Create* writes a new skill in plain words. Its name is the title's slug,
    never a taken, built-in or reserved one.

  The Dev editor's Master Skill panel reads the same library. It no longer
  switches a skill back on when saving it, and it keeps the extra keys a hired
  employee's Skills node carries. `DISCOVER_SKILL_FOLDER` in
  `features/home/data/skills.ts` names the folder.
- **Plugins**: starter bundles, from `features/home/hire/starters.json`, the
  same list the composer's template chips read. A bundle is a job, the apps it
  needs (named in the job, since the job is all the setup model reads), its
  skills, and its own setup (`hire`).
  - *Hire* adds the bundle's skills to the library, switching on any that
    are off, then hires the starter as it stands and shows the new employee
    (see [One-click starters](#one-click-starters)). A bundle can be hired
    again, so Discover offers Hire on every card.
  - A bundle counts as installed when all its skills are in the library;
    nothing else is stored.

  `tests/test_home_catalog_contract.py` holds the bundles and the Discover
  folder to each other and to the app registry.
- **Connectors**: every provider in `config/credential_providers.json`, the
  same set as the editor's Credentials modal. Each declares a
  `consumer_category` (`messages`, `organize`, `business`, `research`,
  `language`, `developer`, `devices`, `ai`), a short `description`, a
  `publisher` (the card's "by …" line) and `verified`, and
  `test_credential_catalogue_consumer_fields.py` fails when one does not, or
  when a plugin credential has no catalogue entry at all, so a new connector
  cannot silently miss the page. They are listed in category order, so apps
  come before AI models. The catalogue adds `connected`, which differs from
  `stored` for providers with a `connected_check` (WhatsApp's live pairing,
  the IMAP/SMTP account's keys; `builtin`, always connected, for the Web
  browser, whose panel manages optional login profiles).

**The catalog page** ([settings/CatalogLayout.tsx](../client/src/features/home/settings/CatalogLayout.tsx)).
Skills, Connectors and Plugins are built on a shared page:
- a title with Yours / Discover, search, and a category filter behind a
  button;
- an optional primary action that opens an inline form;
- a grid of cards.

The page supplies both lists, so the layout never needs to know which page it
is on:
- Discover shows eight cards until Show all.
- A card's "+" turns into a check, which removes on hover only when the page
  can remove.
- Yours shows a switch only when the page can toggle.
- A card glows when its item turns added while it is on screen.

## Wire contract

WebSocket requests (snake_case; failures come back as `success: false` with an
`error` code):

| Type | Payload | Response |
|---|---|---|
| `list_employees` | `{}` | `{employees}`; each summary's `canvas_node_id` is its Canvas board: the one it was hired with, else the graph's first Canvas node, else null. `browser_request` is a browser waiting for the owner (`{node_id, reason, since}`), else null. `talk` is `{state: "on" \| "off" \| "unsupported", agent_node_id}` (the agent that answers the owner; not in `watch_node_ids`). `task` is `{label, text}`, or null when the page already says it (a running employee with Talk on whose only work is the owner's messages). `asks_first` is the hire's "ask me first" rule (built in Dev mode: whether it has an approval gate). `pending_changes` is true when the saved graph's structure differs from the live generation's snapshot |
| `get_employee` | `{workflow_id}` | the summary plus `description`, `job`, `plan`, `rules`, `choices`, `trigger_text`, `last_run`, `latest_report` |
| `get_employee_usage` | `{}` | `{tasks_this_month}` (successful runs since the 1st, owner's timezone, whole team) |
| `generate_employee_setup` | `{job, refine?, history?, draft_token}` | `{draft_token, reply, provider, model, usage, retried, finish_reason, apps}`; `apps` maps each app the reply mentions to its AppRef plus `can_trigger` |
| `cancel_employee_setup` | `{draft_token}` | `{cancelled}` |
| `hire_employee` | `HireEmployeeRequest` | `{employee, started, missing_apps, needs_ai, unsupported_apps, warnings, idempotent}`; errors include `busy` while the same key's first attempt is still building |
| `start_employee` | `{workflow_id, expected_revision, idempotency_key}` | as `start_workflow` |
| `enable_employee_talk` | `{workflow_id, idempotency_key}` | `{employee}`. Errors: `invalid_request`, `not_found`, `unsupported`, `conflict` (a start, pause, resume or reset is under way, or the graph changed meanwhile), `restart_failed`; the last three carry `employee` too |
| `apply_employee_changes` | `{workflow_id, idempotency_key}` | `{employee}`: running ends running, paused or failed ends ready, ready is left alone. Errors: `invalid_request`, `not_found`, `conflict`, `restart_failed` (the last two with `employee`) |
| `send_chat_message` | `{message, role: "user", session_id: <workflow_id>, timestamp}` | `{timestamp, delivery}`: `"now"` while running, starting or resuming; `"queued"` while paused or pausing (it runs on Resume). In any other state `not_running`, and nothing is saved or sent. Session `"default"` works as before, with no `delivery` |
| `get_chat_messages` | `{session_id, limit?, all_generations?}` | `{messages}`, oldest first, each `{id, role, message, timestamp, run_key}`. Timestamps carry their UTC offset; `run_key` is the generation the row was written in. Without `all_generations`, only the latest generation's rows (none after a Reset; every row when the workflow was never started) |
| `list_approvals` | `{workflow_id?, status?, limit <= 100}` | `{approvals, counts, server_time}` |
| `decide_approval` | `{approval_id, decision, text?, subject?, decision_key}` | `{approval, will_send_on_resume}` |

Broadcasts, all CloudEvents events broadcast directly (no Temporal
consumer; see [Event Framework](./event_framework.md#ui-only-lifecycle-events-broadcast-directly-never-through-emit));
every frame but `workflow_ops_apply` carries the whole envelope:

| Wire key | Type | Notes |
|---|---|---|
| `employee_lifecycle` | `com.opencompany.employee.{hired,updated,removed}` | Subject is the workflow id; `updated` is coalesced to one per second per employee, except control changes and browser control changes (each agent step, and the wait for the owner), which go out at once |
| `approval_lifecycle` | `com.opencompany.approval.{requested,decided,expired,cancelled}` | Identity only, never the message or the recipient |
| `workflow_lifecycle` | gains `created` and `deleted` stages | So open editors refresh their workflow lists |
| `chat.updated` | `com.opencompany.chat.updated` | Sent after every chat insert and clear (`services/chat_thread.py`). Data `{workflow_id, session_id, role}`, identity only: `role` is null for a clear, `workflow_id` null for session `"default"`. Home's thread and the editor's chat pane refetch |
| `workflow_ops_apply` | `com.opencompany.workflow.ops.applied` | The frame is the event's flat data, `{workflow_id, caller_node_id, operations, persisted?}`, not the envelope. `persisted: true` marks a batch the server already saved (`apply_graph_additions`: Turn on Talk, the Agent Builder), whose ops carry the server's ids; editors adopt it without saving. See [Workflow Operations Protocol](./workflow_ops_protocol.md#persisted-batches) |

## Tests

Server: `tests/services/employees/`, `tests/services/approvals/`,
`tests/test_edge_condition_parity.py`, `tests/test_genui_catalog_sync.py`,
`tests/test_hire_payload_contract.py`, `tests/test_node_allowlist_hire.py`,
`tests/test_user_settings_profile_fields.py`,
`tests/test_credential_catalogue_consumer_fields.py`,
`tests/test_home_catalog_contract.py` (the starter bundles and the Discover
folder against each other and the app registry, and each starter built with
"ask me first" on),
`tests/temporal/test_machina_run_record.py` (including replay of a pre-patch
history). Talk and growing a saved employee: `tests/services/employees/`
(`test_talk.py`, `test_enable_talk.py`, `test_apply_changes.py`,
`test_policy.py`, and `test_builder_snapshot.py` against
`tests/fixtures/employee_builder_snapshot.json`),
`tests/services/test_chat_thread.py`, `tests/nodes/test_chat_reply.py`,
`tests/services/test_graph_build.py`, `tests/services/test_graph_additions.py`,
`tests/services/test_graph_listeners.py`,
`tests/services/test_deployment_restart.py`,
`tests/nodes/test_agent_builder_employee.py`. Client:
`features/home/**/__tests__` (including `talk.test.tsx`, `thread.test.ts`,
`connectAI.test.tsx`), `app/__tests__`,
`contexts/__tests__/themePrePaint.test.ts`,
`contexts/__tests__/webSocketActions.test.tsx` (`chat.updated`, the editor
chat's rollback), `lib/__tests__/workflowOps.test.ts` and
`hooks/__tests__/useWorkflowOpsListener.test.ts` (persisted batches).

## Known gaps

- An employee's latest report is not captured yet (`latest_report` is always
  empty).
- `list_employees` is not filtered per owner (multi-user mode shares one
  store anyway; see [Authentication](./authentication.md)).
- OAuth sign-in is not opened ahead of the request, so a browser may block
  the popup.
- Each waiting draft holds one `triggers-event` worker slot.
- Billing has no plans, payment method or invoices: no billing account sits
  behind OpenCompany. A deleted employee's runs leave the month's count,
  because deleting a workflow deletes its run records.
- Connectors has no custom (MCP) connector.
- Skill library changes reach only new hires: an employee keeps the skills it
  was hired with, and one hired before the library existed has none, until
  the owner asks them in Talk to add one. The library is shared across users
  in multi-user mode, like the team list.
- An employee hired before Canvas has no Canvas node, so its
  `canvas_node_id` is null. Adding one in Dev mode fills it in.
- The Workspace's Android tab has no live mirror yet. Browser now uses the
  shared live viewer described above; timeline and replay are not available.
- The Web browser app counts as connected even on a machine with no
  installed Chrome, Edge or Chromium; the employee's first browser step
  reports it. An employee hired before the app existed has no browser;
  adding a Browser node in Dev mode gives it one.
- With login on, an agent's Canvas writes land under the default owner:
  its tool call carries no user id
  ([agent_workflow.py](../server/services/temporal/agent_workflow.py)).
  The Workspace reads the signed-in owner's board, so it shows nothing
  there. This predates the Workspace and applies to the editor's Memory
  and Data Source tools as well. Fixing it moves existing memories to a
  different owner, so it needs its own change.
- The talk agent has its own conversation: it cannot see the worker's
  exchanges with customers or the text of its routine reports. The two share
  Memory, the Canvas, the checklist and the skills.
- A tool added from Talk works at once only in the talk agent's current run.
  The next message is a new run from the live generation's snapshot, which
  lacks it until the agent asks the Agent Builder for it again (that binds
  the saved node, bind-only). The worker gets it only after Apply.
- Messages queued while paused each start a run on Resume; the page allows
  one queued message at a time.
- Turn on Talk and Apply reset the employee: the conversation starts fresh
  (its thread is cleared with the agent's Context), messages queued while
  paused are dropped, and drafts waiting for the owner are cancelled (the
  confirmation and the notice warn about those).
- A Dev editor holding unsaved edits made before a server-side change (Turn on
  Talk, the Agent Builder) can still overwrite it on its next save, because
  saves carry no revision check. Adopting persisted batches narrows the
  window.
- Pre-existing: with Temporal disabled, deployed chat triggers never fire,
  and a canvas Run of a `chatTrigger` waits forever.
- An example workflow whose agent serves several triggers posts all of that
  agent's answers into the thread: AI Employee and Claude Assistant answer
  Telegram through the same agent.
- Threads are per workflow, not per user.
- A chat trigger with a custom `session_id` still counts as Talk `on`, though
  Home's messages (session = the workflow id) never reach it.
- Turn on Talk on a chat hire made before Talk adds Reply in Chat and the
  Agent Builder but leaves the worker's instructions as they were, so they do
  not mention the `agent_builder` tool.
- A schedule in a time zone `cronScheduler` does not list runs in a zone with
  the same offset at hire time (else UTC, shifted), so it drifts across
  daylight-saving changes.

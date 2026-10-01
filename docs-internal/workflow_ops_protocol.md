# Workflow Operations Protocol

The standard wire format any backend service uses to mutate the React
Flow canvas. A service returns `{operations: [...]}`; the frontend
walks the list with a single applier (`client/src/lib/workflowOps.ts`)
that creates / removes nodes + edges and saves parameter rows, unless
the server already saved the batch (see [Persisted batches](#persisted-batches)).

**Why a protocol**: keeps domain rules in the backend (visuals.json
lookups, plugin-registry checks, canonical config shapes) without
forcing each new feature to invent its own RPC + frontend glue.
"Auto-add Skill on tool connect" is the first consumer; future
features (workflow templates, AI-suggested edits, automation recipes)
just emit op batches.

## Wire shape

```json
{
  "operations": [
    { "type": "add_node", ... },
    { "type": "add_edge", ... }
  ]
}
```

Operations apply **in order**. Earlier ops can produce React Flow node
ids that later ops reference via `client_ref` placeholders.

## Operation types (v1)

| Type | Required | Optional | Purpose |
|---|---|---|---|
| `add_node` | `client_ref`, `node_type`, `parameters` | `label`, `position`, `minted_id`, `data` | Create a new node + save its initial params. `client_ref` is a batch-local id later ops can target. When `minted_id` is set (a server-side writer allocated the id, so status broadcasts route to the same React Flow id), the FE applier adopts it verbatim instead of minting one. `data` is node data beyond the label (a Context's `systemManaged` / `agentNodeId` link to its agent). |
| `add_edge` | `source`, `target` | `source_handle`, `target_handle`, `edge_id`, `condition` | Wire two nodes. `source` / `target` are either existing node ids (string) or `{client_ref}` references. `edge_id` is the server's id for an edge it saved; the applier adopts it. `condition` gates the edge (saved as `edge.data.condition`). |
| `set_node_parameters` | `node_id`, `parameters` | -- | `parameters` is the node's WHOLE row: the applier saves it with `saveNodeParameters`, which replaces the row, so an op that is not persisted must carry every parameter to keep. In a persisted batch it is the server's merged row, and nothing is saved. |
| `delete_node` | `node_id` | -- | Remove node; edges to/from it cascade-delete. |
| `delete_edge` | `edge_id` | -- | Remove a single edge. |
| `move_node` | `node_id`, `position` | -- | Reposition without changing identity. |
| `replace_node` | `node_id`, `node_type`, `parameters` | `label`, `preserve_edges` (default `true`) | Atomic delete-and-add at the same position. With `preserve_edges`, edges to/from the old id rewire to the new id. |

### `PositionSpec`

Either absolute or anchored:

```ts
type PositionSpec =
  | { x: number; y: number }
  | { anchor_node_id: string;
      offset?: { x?: number; y?: number };
      fallback?: { x: number; y: number } };
```

Anchored positions stay backend-agnostic about pixel coords -- the
frontend resolves the anchor against current React Flow state. If the
anchor doesn't exist, `fallback` is used (or a sensible default).

### `NodeRef`

```ts
type NodeRef = string | { client_ref: string };
```

A string is an existing React Flow node id. A `{client_ref}` is a
placeholder that resolves to the id of an `add_node` op earlier in
the same batch.

## Application semantics

- **Order**: ops apply sequentially. Anchor lookups and `client_ref`
  resolution see the cumulative state of earlier ops in the batch.
- **Best-effort**: a failed op (e.g. `delete_node` on a missing id) is
  logged and reported in the result; subsequent ops still apply. v1
  has no rollback. Backend services should write op sequences that are
  robust to partial application.
- **Not a diff reconciler**: re-applying a batch is not assumed safe.
  The protocol is a one-shot mutation wire format. Persisted batches are
  the exception: they are applied idempotently (below).

## Persisted batches

A server-side writer that changes a saved workflow goes through
`services/workflow_storage/mutate.py::apply_graph_additions`: one
transaction appends the nodes and edges to `workflow.data`, writes a fresh
parameter row for each new node, merges into existing rows (a Skills
node's `skills_config`), and records a ledger row keyed by the caller's
mutation id, so a retry returns the first result instead of adding twice.
After the commit it pushes the batch with `persisted: true`:

```json
{"type": "workflow_ops_apply",
 "data": {"workflow_id": "7", "caller_node_id": "7:aiAgent:2",
          "operations": [...], "persisted": true}}
```

The ops carry the server's node ids (`minted_id`), edge ids (`edge_id`),
absolute positions, node `data`, edge `condition`, and full parameter rows.
An editor adopts them as they are:

- nothing is saved: the server wrote the nodes, edges and rows itself (an
  editor that re-saved a row would REPLACE it, which is how a hire's copied
  library skills used to be wiped);
- ids the canvas already has are skipped, because the same batch can
  arrive twice (a retried write announces it again);
- `set_node_parameters` goes to the parameter cache, not the graph.

Unlike `save_workflow` this never replaces the graph, so it cannot drop what
an editor saved meanwhile. An editor holding unsaved changes made before the
push can still overwrite it on its next save (saves carry no revision check);
adopting the batch into the editor's working copy narrows that window.
Such a save cannot bring back a workflow deleted meanwhile: saving an
existing workflow is an update only (`require_existing`), so it fails with
`workflow_not_found`.

## Backend usage

```python
from services import workflow_ops

ref = "new_master"
return {
    "operations": [
        workflow_ops.add_node(
            ref, "masterSkill",
            {"skillsConfig": {...}},
            label="Master Skill",
            position=workflow_ops.anchored(agent_id, offset_x=-60, offset_y=220),
        ),
        workflow_ops.add_edge(
            {"client_ref": ref}, agent_id,
            source_handle="output-tool", target_handle="input-skill",
        ),
    ],
}
```

Helpers live in `server/services/workflow_ops.py` (TypedDicts +
builder functions). Empty result: `workflow_ops.empty()` returns
`{"operations": []}`.

## Frontend usage

```ts
import { applyOperations } from '@/lib/workflowOps';

const result = await applyOperations(operations, {
  nodes, edges, setNodes, setEdges, saveNodeParameters,
});
// result.applied  -> count of successful ops
// result.errors   -> [{op, message}, ...]
// result.refMap   -> { client_ref: generated_node_id, ... }
```

`applyOperations` does not throw; callers inspect `errors` to surface
failures (toast, log, retry, etc.).

## Current consumers

| Service | Trigger | Module |
|---|---|---|
| Auto-add Skill on tool connect | WS request `evaluate_auto_skill` (frontend on edge connect/disconnect) | `server/services/auto_skill.py` |
| Agent Builder runtime tools | `apply_graph_additions`, then the persisted `workflow_ops_apply` push (mid-execution, from the agent's tool call) | `server/nodes/tool/agent_builder/__init__.py` |
| Turn on Talk (any employee on Home, hired or built in Dev mode) | `apply_graph_additions`, then the persisted push | `server/services/employees/handlers.py` |
| Vertex managed agent cloud-tool nodes | whole-graph `database.save_workflow`, then a push that is NOT persisted (the editor saves the parameter rows) | `server/nodes/agent/vertex_managed_agent/_ops.py` |

## Two delivery modes

The protocol carries the same `{operations: [...]}` payload in both
directions:

* **Request/response** (auto-skill pattern). Frontend sends a WS
  request, the backend handler returns ops in the response, the
  frontend applies them. Used when the user takes an action on the
  canvas and the backend decides what should happen.

* **Push broadcast** (Agent Builder pattern). Backend code (often
  inside an LLM tool execution) calls
  `services.workflow_ops.broadcast_workflow_ops(workflow_id=...,
  caller_node_id=..., operations=..., persisted=...)`, which sends the
  flat `{workflow_id, caller_node_id, operations, persisted?}` frame;
  `apply_graph_additions` calls it for every batch it saves. Two
  frontend listeners subscribe through
  `addEventListener('workflow_ops_apply', ...)`:
  - `useWorkflowOpsListener` (mounted in `Dashboard`) applies a batch
    for the open workflow to the canvas: a persisted one by adopting it
    (`addSavedNodes` / `addSavedEdges`), any other through
    `applyOperations`. A batch for another workflow shows a toast,
    "{Name} updated their tools" (the name from the workflow list).
  - `useSavedGraphSync` (mounted in the app shell, so it also runs while
    Home is showing) adopts a persisted batch into the app store's
    working copy of the workflow the editor holds
    (`adoptSavedOperations`), leaving the unsaved flag alone, and puts
    its parameter rows in the parameter cache. A workflow left open in
    Dev while an employee changes it from Talk then keeps the server's
    additions, and the editor's next save cannot drop them.

## Adding a new consumer

1. Write a backend module that builds a workflow-ops batch using
   the helpers in `services/workflow_ops`.
2. Choose a delivery mode:
   * Request/response: add a thin `@ws_handler` that returns
     `{success: True, operations: [...]}`. On the frontend send the
     WS request and pipe the result into `applyOperations`.
   * Push broadcast, for a change to a saved workflow: describe it as
     `services.graph_build.GraphAdditions` and call
     `apply_graph_additions`, which saves it and pushes the persisted
     batch. No frontend code -- the existing listeners handle it.
   * Push broadcast of a batch the server did not save: call
     `broadcast_workflow_ops(...)`; the editor applies it and saves the
     parameter rows.
3. If you need a new op type, follow the steps in the docstring of
   `server/services/workflow_ops.py` (mirror the TypedDict in TS,
   add an apply branch, document here).

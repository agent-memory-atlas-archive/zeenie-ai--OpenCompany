/**
 * Workflow Operations protocol -- frontend applier.
 *
 * The standard wire format any backend service uses to mutate the
 * React Flow canvas. Backend services return `{operations: [...]}`
 * (see server/services/workflow_ops.py); this module walks the list,
 * generating real React Flow node ids, resolving cross-op references
 * via `client_ref` placeholders, and persisting parameter changes
 * through `saveNodeParameters`.
 *
 * A batch the server already saved (`workflow_ops_apply` with
 * `persisted: true`) goes through `addSavedNodes` / `addSavedEdges`
 * instead: it carries the server's ids and full parameter rows, and
 * nothing is saved again.
 *
 * Full protocol spec: docs-internal/workflow_ops_protocol.md
 *
 * Adding a new operation type:
 *   1. Mirror the TypedDict from server/services/workflow_ops.py
 *      below.
 *   2. Add the apply branch to applyOperations.
 *   3. Document the new op in workflow_ops_protocol.md.
 */

import type { Node, Edge } from 'reactflow';
import { NEW_WORKFLOW_ID, nextNodeInstanceId } from '../utils/workflow';

// ---------------------------------------------------------------------------
// Wire types -- mirror server/services/workflow_ops.py 1:1
// ---------------------------------------------------------------------------

export type AbsolutePosition = { x: number; y: number };
export type AnchoredPosition = {
  anchor_node_id: string;
  offset?: { x?: number; y?: number };
  fallback?: AbsolutePosition;
};
export type PositionSpec = AbsolutePosition | AnchoredPosition;

export type NodeRef = string | { client_ref: string };

export interface AddNodeOp {
  type: 'add_node';
  client_ref: string;
  node_type: string;
  parameters: Record<string, any>;
  label?: string;
  position?: PositionSpec;
  /**
   * Optional BE-minted node id. When the backend hot-spawns a node
   * mid-execution (agentBuilder + the agent-loop rebind path) it needs
   * to dispatch status broadcasts against the same id the canvas
   * renders under. The applier adopts this id verbatim when present,
   * falling back to `newId()` otherwise so frontend-initiated spawns
   * keep their existing semantics.
   */
  minted_id?: string;
  /** Node data beyond the label (a Context's link to its agent). */
  data?: Record<string, any>;
}

export interface AddEdgeOp {
  type: 'add_edge';
  source: NodeRef;
  target: NodeRef;
  source_handle?: string;
  target_handle?: string;
  /** The server's id for an edge it already saved; adopted as is. */
  edge_id?: string;
  /** Follow the edge only when this holds (kept as `edge.data.condition`). */
  condition?: Record<string, any>;
}

export interface SetNodeParametersOp {
  type: 'set_node_parameters';
  node_id: string;
  parameters: Record<string, any>;
}

export interface DeleteNodeOp {
  type: 'delete_node';
  node_id: string;
}

export interface DeleteEdgeOp {
  type: 'delete_edge';
  edge_id: string;
}

export interface MoveNodeOp {
  type: 'move_node';
  node_id: string;
  position: PositionSpec;
}

export interface ReplaceNodeOp {
  type: 'replace_node';
  node_id: string;
  node_type: string;
  parameters: Record<string, any>;
  label?: string;
  preserve_edges?: boolean;
}

export type WorkflowOperation =
  | AddNodeOp
  | AddEdgeOp
  | SetNodeParametersOp
  | DeleteNodeOp
  | DeleteEdgeOp
  | MoveNodeOp
  | ReplaceNodeOp;

/** The `workflow_ops_apply` push (services/workflow_ops.py
 *  `broadcast_workflow_ops`). `persisted`: the server already saved the
 *  batch's nodes, edges and parameter rows. */
export interface WorkflowOpsApplyEvent {
  workflow_id?: string | null;
  caller_node_id?: string | null;
  operations?: WorkflowOperation[];
  persisted?: boolean;
}

// ---------------------------------------------------------------------------
// Apply context + result
// ---------------------------------------------------------------------------

export interface ApplyContext {
  /** Canonical workflow identity used when minting plugin node instances. */
  workflowId?: string;
  /** Current React Flow nodes (read-only -- used for anchor resolution). */
  nodes: Node[];
  /** Current React Flow edges (read-only -- used for delete-cascade). */
  edges: Edge[];
  setNodes: (updater: (ns: Node[]) => Node[]) => void;
  setEdges: (updater: (es: Edge[]) => Edge[]) => void;
  /** Save a node's whole parameter row (it replaces the row); backend WS round-trip. */
  saveNodeParameters: (nodeId: string, parameters: Record<string, any>) => Promise<boolean>;
  /** Optional fallback position when an anchor node is missing. */
  defaultPosition?: AbsolutePosition;
}

export interface OperationError {
  op: WorkflowOperation;
  message: string;
}

export interface ApplyResult {
  applied: number;
  errors: OperationError[];
  /** client_ref -> generated React Flow node id, populated by add_node ops. */
  refMap: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function resolveNodeRef(ref: NodeRef, refMap: Record<string, string>): string | null {
  if (typeof ref === 'string') return ref;
  return refMap[ref.client_ref] ?? null;
}

function resolvePosition(
  spec: PositionSpec | undefined,
  liveNodes: Node[],
  defaultPosition?: AbsolutePosition,
): AbsolutePosition {
  if (!spec) return defaultPosition ?? { x: 200, y: 200 };
  if ('x' in spec && 'y' in spec) return { x: spec.x, y: spec.y };
  const anchor = liveNodes.find(n => n.id === spec.anchor_node_id);
  if (anchor) {
    return {
      x: anchor.position.x + (spec.offset?.x ?? 0),
      y: anchor.position.y + (spec.offset?.y ?? 0),
    };
  }
  return spec.fallback ?? defaultPosition ?? { x: 200, y: 200 };
}

function nodeFromOp(op: AddNodeOp, id: string, position: AbsolutePosition): Node {
  const label = op.label ?? op.parameters?.label ?? op.node_type;
  return { id, type: op.node_type, position, data: { label, ...op.data } };
}

function edgeFromOp(op: AddEdgeOp, source: string, target: string): Edge {
  return {
    id: op.edge_id ?? `e-${source}-${target}-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
    source,
    target,
    sourceHandle: op.source_handle ?? null,
    targetHandle: op.target_handle ?? null,
    ...(op.condition ? { data: { condition: op.condition } } : {}),
  };
}

// ---------------------------------------------------------------------------
// Batches the server already saved
// ---------------------------------------------------------------------------

/**
 * `nodes` with the new nodes of a saved batch (`persisted: true`), under
 * the server's ids, positions and data. An id already there is skipped:
 * the same batch can arrive twice. Returns `nodes` itself when nothing is
 * new. Nothing is saved; the server wrote the parameter rows.
 */
export function addSavedNodes(nodes: Node[], ops: WorkflowOperation[]): Node[] {
  let next = nodes;
  for (const op of ops) {
    if (op.type !== 'add_node' || !op.minted_id || next.some(n => n.id === op.minted_id)) continue;
    next = next.concat(nodeFromOp(op, op.minted_id, resolvePosition(op.position, next)));
  }
  return next;
}

/**
 * `edges` with the new edges of a saved batch, under the server's ids. An
 * edge already there (the same id, or the same ends and handles) is
 * skipped. Returns `edges` itself when nothing is new.
 */
export function addSavedEdges(edges: Edge[], ops: WorkflowOperation[]): Edge[] {
  let next = edges;
  for (const op of ops) {
    if (op.type !== 'add_edge' || typeof op.source !== 'string' || typeof op.target !== 'string') continue;
    const edge = edgeFromOp(op, op.source, op.target);
    const known = next.some(e => e.id === edge.id || (
      e.source === edge.source
      && e.target === edge.target
      && (e.sourceHandle ?? null) === edge.sourceHandle
      && (e.targetHandle ?? null) === edge.targetHandle
    ));
    if (!known) next = next.concat(edge);
  }
  return next;
}

// ---------------------------------------------------------------------------
// Apply
// ---------------------------------------------------------------------------

/**
 * Apply a workflow-ops batch to the canvas.
 *
 * Operations apply in order. `client_ref` placeholders in `add_node`
 * ops produce real ids that subsequent ops can reference via
 * `{client_ref: "..."}`. Failures are collected per op; the function
 * does not throw -- callers inspect the returned `errors` array.
 */
export async function applyOperations(
  ops: WorkflowOperation[],
  ctx: ApplyContext,
): Promise<ApplyResult> {
  const result: ApplyResult = { applied: 0, errors: [], refMap: {} };

  // Local mutable copies so anchor-resolution sees nodes added
  // earlier in the batch (setNodes is async w.r.t. React).
  let liveNodes = [...ctx.nodes];
  let liveEdges = [...ctx.edges];

  for (const op of ops) {
    try {
      switch (op.type) {
        case 'add_node': {
          // Prefer the BE-minted id when present (agentBuilder hot-spawn
          // path) so status broadcasts dispatched against the same id
          // land on this React Flow node and the canvas glows.
          const id = op.minted_id || nextNodeInstanceId(
            ctx.workflowId ?? NEW_WORKFLOW_ID,
            op.node_type,
            liveNodes,
          );
          result.refMap[op.client_ref] = id;
          const node = nodeFromOp(op, id, resolvePosition(op.position, liveNodes, ctx.defaultPosition));
          liveNodes = liveNodes.concat(node);
          ctx.setNodes(ns => ns.concat(node));
          await ctx.saveNodeParameters(id, { label: node.data.label, ...op.parameters });
          break;
        }

        case 'add_edge': {
          const source = resolveNodeRef(op.source, result.refMap);
          const target = resolveNodeRef(op.target, result.refMap);
          if (!source || !target) {
            throw new Error('add_edge: unresolved client_ref');
          }
          const edge = edgeFromOp(op, source, target);
          liveEdges = liveEdges.concat(edge);
          ctx.setEdges(es => es.concat(edge));
          break;
        }

        case 'set_node_parameters': {
          const ok = await ctx.saveNodeParameters(op.node_id, op.parameters);
          if (!ok) throw new Error(`saveNodeParameters returned false for ${op.node_id}`);
          break;
        }

        case 'delete_node': {
          liveNodes = liveNodes.filter(n => n.id !== op.node_id);
          liveEdges = liveEdges.filter(e => e.source !== op.node_id && e.target !== op.node_id);
          ctx.setNodes(ns => ns.filter(n => n.id !== op.node_id));
          ctx.setEdges(es => es.filter(e => e.source !== op.node_id && e.target !== op.node_id));
          break;
        }

        case 'delete_edge': {
          liveEdges = liveEdges.filter(e => e.id !== op.edge_id);
          ctx.setEdges(es => es.filter(e => e.id !== op.edge_id));
          break;
        }

        case 'move_node': {
          const position = resolvePosition(op.position, liveNodes, ctx.defaultPosition);
          liveNodes = liveNodes.map(n => (n.id === op.node_id ? { ...n, position } : n));
          ctx.setNodes(ns => ns.map(n => (n.id === op.node_id ? { ...n, position } : n)));
          break;
        }

        case 'replace_node': {
          const existing = liveNodes.find(n => n.id === op.node_id);
          if (!existing) throw new Error(`replace_node: node ${op.node_id} not found`);
          const newNodeId = nextNodeInstanceId(
            ctx.workflowId ?? NEW_WORKFLOW_ID,
            op.node_type,
            liveNodes,
          );
          const label = op.label ?? op.parameters?.label ?? op.node_type;
          const replacement: Node = {
            id: newNodeId,
            type: op.node_type,
            position: existing.position,
            data: { label },
          };
          // Drop old, add new.
          liveNodes = liveNodes.filter(n => n.id !== op.node_id).concat(replacement);
          ctx.setNodes(ns => ns.filter(n => n.id !== op.node_id).concat(replacement));
          // Edges: rewire to the new id (preserve_edges defaults to true).
          if (op.preserve_edges !== false) {
            liveEdges = liveEdges.map(e => {
              if (e.source === op.node_id) return { ...e, source: newNodeId };
              if (e.target === op.node_id) return { ...e, target: newNodeId };
              return e;
            });
            ctx.setEdges(es => es.map(e => {
              if (e.source === op.node_id) return { ...e, source: newNodeId };
              if (e.target === op.node_id) return { ...e, target: newNodeId };
              return e;
            }));
          } else {
            liveEdges = liveEdges.filter(e => e.source !== op.node_id && e.target !== op.node_id);
            ctx.setEdges(es => es.filter(e => e.source !== op.node_id && e.target !== op.node_id));
          }
          await ctx.saveNodeParameters(newNodeId, { label, ...op.parameters });
          break;
        }

        default: {
          // Exhaustiveness check -- TS will yell if a new op type is
          // added without an apply branch.
          const _exhaustive: never = op;
          throw new Error(`Unknown op type: ${(_exhaustive as any)?.type}`);
        }
      }
      result.applied += 1;
    } catch (err) {
      result.errors.push({
        op,
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return result;
}

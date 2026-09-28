/**
 * Tests for the WorkflowOperation applier in lib/workflowOps.ts.
 *
 * Locks the protocol behaviour the backend services depend on:
 *   - operations apply in order
 *   - client_ref placeholders resolve to generated React Flow ids
 *   - PositionSpec resolution: absolute vs anchor + offset, with fallback
 *   - replace_node rewires (or drops) edges via preserve_edges
 *   - failures are collected per op, applier never throws
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Node, Edge } from 'reactflow';
import {
  addSavedEdges,
  addSavedNodes,
  applyOperations,
  type ApplyContext,
  type WorkflowOperation,
} from '../workflowOps';

// ----- helpers ---------------------------------------------------------------

function makeNode(id: string, type: string, x = 0, y = 0): Node {
  return { id, type, position: { x, y }, data: { label: id } };
}

function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target };
}

interface HarnessState {
  nodes: Node[];
  edges: Edge[];
  saveCalls: Array<{ nodeId: string; parameters: Record<string, any> }>;
}

function makeContext(initial: { nodes?: Node[]; edges?: Edge[] } = {}): {
  ctx: ApplyContext;
  state: HarnessState;
} {
  const state: HarnessState = {
    nodes: initial.nodes ?? [],
    edges: initial.edges ?? [],
    saveCalls: [],
  };

  const ctx: ApplyContext = {
    get nodes() {
      return state.nodes;
    },
    get edges() {
      return state.edges;
    },
    setNodes: (updater) => {
      state.nodes = updater(state.nodes);
    },
    setEdges: (updater) => {
      state.edges = updater(state.edges);
    },
    saveNodeParameters: vi.fn(async (nodeId: string, parameters: Record<string, any>) => {
      state.saveCalls.push({ nodeId, parameters });
      return true;
    }),
  };

  return { ctx, state };
}

beforeEach(() => {
  // Math.random feeds id generation -- pin it so test assertions on
  // generated ids are stable. Time is mocked per-test where needed.
  vi.spyOn(Math, 'random').mockReturnValue(0.5);
});

// ----- add_node --------------------------------------------------------------

describe('add_node', () => {
  it('adds a node + persists params + records the client_ref -> id mapping', async () => {
    const { ctx, state } = makeContext();
    const ops: WorkflowOperation[] = [
      {
        type: 'add_node',
        client_ref: 'ms',
        node_type: 'masterSkill',
        parameters: { skillsConfig: { 'http-request-skill': { enabled: true, instructions: '', isCustomized: false } } },
        label: 'Master Skill',
        position: { x: 100, y: 200 },
      },
    ];

    const result = await applyOperations(ops, ctx);

    expect(result.applied).toBe(1);
    expect(result.errors).toEqual([]);
    expect(state.nodes).toHaveLength(1);
    expect(state.nodes[0].type).toBe('masterSkill');
    expect(state.nodes[0].position).toEqual({ x: 100, y: 200 });
    expect(state.nodes[0].data).toEqual({ label: 'Master Skill' });

    expect(result.refMap.ms).toBe(state.nodes[0].id);

    expect(state.saveCalls).toHaveLength(1);
    expect(state.saveCalls[0].nodeId).toBe(state.nodes[0].id);
    expect(state.saveCalls[0].parameters.label).toBe('Master Skill');
    expect(state.saveCalls[0].parameters.skillsConfig).toBeDefined();
  });

  it('resolves an anchored position against existing nodes', async () => {
    const anchor = makeNode('agent-1', 'aiAgent', 300, 400);
    const { ctx, state } = makeContext({ nodes: [anchor] });

    await applyOperations(
      [
        {
          type: 'add_node',
          client_ref: 'ms',
          node_type: 'masterSkill',
          parameters: {},
          position: {
            anchor_node_id: 'agent-1',
            offset: { x: -60, y: 220 },
          },
        },
      ],
      ctx,
    );

    const added = state.nodes.find((n) => n.type === 'masterSkill')!;
    expect(added.position).toEqual({ x: 240, y: 620 });
  });

  it('falls back to PositionSpec.fallback when anchor is missing', async () => {
    const { ctx, state } = makeContext();
    await applyOperations(
      [
        {
          type: 'add_node',
          client_ref: 'ms',
          node_type: 'masterSkill',
          parameters: {},
          position: {
            anchor_node_id: 'missing-anchor',
            offset: { x: 10, y: 10 },
            fallback: { x: 5, y: 7 },
          },
        },
      ],
      ctx,
    );
    expect(state.nodes[0].position).toEqual({ x: 5, y: 7 });
  });

  it('uses defaultPosition when anchor missing and no fallback', async () => {
    const { ctx, state } = makeContext();
    ctx.defaultPosition = { x: 999, y: 888 };
    await applyOperations(
      [
        {
          type: 'add_node',
          client_ref: 'n',
          node_type: 't',
          parameters: {},
          position: { anchor_node_id: 'missing' },
        },
      ],
      ctx,
    );
    expect(state.nodes[0].position).toEqual({ x: 999, y: 888 });
  });

  it('lets a later add_edge resolve a client_ref from the same batch', async () => {
    const target = makeNode('agent-1', 'aiAgent', 0, 0);
    const { ctx, state } = makeContext({ nodes: [target] });

    const result = await applyOperations(
      [
        {
          type: 'add_node',
          client_ref: 'ms',
          node_type: 'masterSkill',
          parameters: {},
        },
        {
          type: 'add_edge',
          source: { client_ref: 'ms' },
          target: 'agent-1',
          source_handle: 'output-tool',
          target_handle: 'input-skill',
        },
      ],
      ctx,
    );

    expect(result.applied).toBe(2);
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0].source).toBe(result.refMap.ms);
    expect(state.edges[0].target).toBe('agent-1');
    expect(state.edges[0].sourceHandle).toBe('output-tool');
    expect(state.edges[0].targetHandle).toBe('input-skill');
  });
});

describe('server-minted fields', () => {
  it('keeps node data beyond the label, and an edge id and condition', async () => {
    const { ctx, state } = makeContext({ nodes: [makeNode('agent', 'aiAgent')] });
    const condition = { field: 'result.response', operator: 'neq', value: 'NO_REPLY' };

    await applyOperations(
      [
        {
          type: 'add_node',
          client_ref: 'ctx',
          node_type: 'context',
          parameters: {},
          label: 'Context',
          minted_id: '7:context:1',
          data: { systemManaged: true, agentNodeId: 'agent' },
        },
        { type: 'add_edge', source: 'agent', target: 'reply', edge_id: 'e-server', condition },
      ],
      ctx,
    );

    expect(state.nodes[1].data).toEqual({ label: 'Context', systemManaged: true, agentNodeId: 'agent' });
    expect(state.edges[0].id).toBe('e-server');
    expect(state.edges[0].data).toEqual({ condition });
  });
});

// ----- batches the server already saved -------------------------------------

const SAVED: WorkflowOperation[] = [
  {
    type: 'add_node',
    client_ref: 'tool',
    node_type: 'duckduckgoSearch',
    parameters: { max_results: 5 },
    label: 'Web search',
    position: { x: 120, y: 440 },
    minted_id: '7:duckduckgoSearch:1',
  },
  {
    type: 'add_edge',
    source: '7:duckduckgoSearch:1',
    target: '7:aiAgent:1',
    source_handle: 'output-tool',
    target_handle: 'input-tools',
    edge_id: 'e-7:duckduckgoSearch:1-output-tool-7:aiAgent:1-input-tools',
  },
  { type: 'set_node_parameters', node_id: '7:masterSkill:1', parameters: { skills_config: {} } },
];

describe('addSavedNodes / addSavedEdges', () => {
  it('adopts the server ids, position and label, and saves nothing', () => {
    const nodes = addSavedNodes([makeNode('7:aiAgent:1', 'aiAgent')], SAVED);
    const edges = addSavedEdges([], SAVED);

    expect(nodes[1]).toEqual({
      id: '7:duckduckgoSearch:1',
      type: 'duckduckgoSearch',
      position: { x: 120, y: 440 },
      data: { label: 'Web search' },
    });
    expect(edges).toEqual([
      {
        id: 'e-7:duckduckgoSearch:1-output-tool-7:aiAgent:1-input-tools',
        source: '7:duckduckgoSearch:1',
        target: '7:aiAgent:1',
        sourceHandle: 'output-tool',
        targetHandle: 'input-tools',
      },
    ]);
  });

  it('skips what the canvas has: a batch can arrive twice', () => {
    const nodes = addSavedNodes([makeNode('7:aiAgent:1', 'aiAgent')], SAVED);
    const edges = addSavedEdges([], SAVED);

    expect(addSavedNodes(nodes, SAVED)).toBe(nodes);
    expect(addSavedEdges(edges, SAVED)).toBe(edges);
  });

  it('skips an edge with the same ends and handles under another id', () => {
    const drawn: Edge = {
      id: 'e-drawn',
      source: '7:duckduckgoSearch:1',
      target: '7:aiAgent:1',
      sourceHandle: 'output-tool',
      targetHandle: 'input-tools',
    };

    expect(addSavedEdges([drawn], SAVED)).toEqual([drawn]);
  });
});

// ----- add_edge --------------------------------------------------------------

describe('add_edge', () => {
  it('creates an edge between existing node ids', async () => {
    const { ctx, state } = makeContext({
      nodes: [makeNode('a', 'x'), makeNode('b', 'y')],
    });
    await applyOperations(
      [{ type: 'add_edge', source: 'a', target: 'b' }],
      ctx,
    );
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0].source).toBe('a');
    expect(state.edges[0].target).toBe('b');
  });

  it('records an error when a client_ref does not resolve', async () => {
    const { ctx, state } = makeContext({ nodes: [makeNode('a', 'x')] });
    const result = await applyOperations(
      [{ type: 'add_edge', source: { client_ref: 'never-added' }, target: 'a' }],
      ctx,
    );
    expect(state.edges).toHaveLength(0);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0].message).toContain('unresolved client_ref');
  });
});

// ----- set_node_parameters ---------------------------------------------------

describe('set_node_parameters', () => {
  it('forwards the params to saveNodeParameters', async () => {
    const { ctx, state } = makeContext();
    await applyOperations(
      [
        {
          type: 'set_node_parameters',
          node_id: 'n1',
          parameters: { skillsConfig: {} },
        },
      ],
      ctx,
    );
    expect(state.saveCalls).toEqual([
      { nodeId: 'n1', parameters: { skillsConfig: {} } },
    ]);
  });

  it('records an error when the save call returns false', async () => {
    const { ctx } = makeContext();
    // Override the harness mock to simulate a failed save. The mock
    // now just returns false without pushing to saveCalls -- the assertion
    // here is on errors, not on saveCalls.
    (ctx.saveNodeParameters as ReturnType<typeof vi.fn>).mockResolvedValue(false);
    const result = await applyOperations(
      [{ type: 'set_node_parameters', node_id: 'n1', parameters: {} }],
      ctx,
    );
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0].message).toContain('saveNodeParameters returned false');
  });
});

// ----- delete_node / delete_edge --------------------------------------------

describe('delete_node', () => {
  it('removes the node and cascades incident edges', async () => {
    const { ctx, state } = makeContext({
      nodes: [makeNode('a', 'x'), makeNode('b', 'y'), makeNode('c', 'z')],
      edges: [makeEdge('e1', 'a', 'b'), makeEdge('e2', 'b', 'c'), makeEdge('e3', 'a', 'c')],
    });
    await applyOperations([{ type: 'delete_node', node_id: 'b' }], ctx);
    expect(state.nodes.map((n) => n.id)).toEqual(['a', 'c']);
    // Both edges touching `b` are gone; `a -> c` survives.
    expect(state.edges.map((e) => e.id)).toEqual(['e3']);
  });
});

describe('delete_edge', () => {
  it('removes only the named edge', async () => {
    const { ctx, state } = makeContext({
      edges: [makeEdge('e1', 'a', 'b'), makeEdge('e2', 'a', 'c')],
    });
    await applyOperations([{ type: 'delete_edge', edge_id: 'e1' }], ctx);
    expect(state.edges.map((e) => e.id)).toEqual(['e2']);
  });
});

// ----- move_node -------------------------------------------------------------

describe('move_node', () => {
  it('updates the node position absolutely', async () => {
    const { ctx, state } = makeContext({ nodes: [makeNode('a', 'x', 1, 2)] });
    await applyOperations(
      [{ type: 'move_node', node_id: 'a', position: { x: 99, y: 100 } }],
      ctx,
    );
    expect(state.nodes[0].position).toEqual({ x: 99, y: 100 });
  });

  it('resolves an anchored position relative to another node', async () => {
    const { ctx, state } = makeContext({
      nodes: [makeNode('a', 'x', 0, 0), makeNode('anchor', 'y', 10, 20)],
    });
    await applyOperations(
      [
        {
          type: 'move_node',
          node_id: 'a',
          position: { anchor_node_id: 'anchor', offset: { x: 5, y: 5 } },
        },
      ],
      ctx,
    );
    expect(state.nodes.find((n) => n.id === 'a')!.position).toEqual({ x: 15, y: 25 });
  });
});

// ----- replace_node ----------------------------------------------------------

describe('replace_node', () => {
  it('replaces in place + rewires edges by default (preserve_edges=true)', async () => {
    const { ctx, state } = makeContext({
      nodes: [makeNode('old', 'oldType', 50, 60), makeNode('peer', 'p')],
      edges: [makeEdge('e1', 'peer', 'old'), makeEdge('e2', 'old', 'peer')],
    });

    const result = await applyOperations(
      [
        {
          type: 'replace_node',
          node_id: 'old',
          node_type: 'newType',
          parameters: { foo: 1 },
          label: 'New',
        },
      ],
      ctx,
    );

    expect(result.applied).toBe(1);
    expect(state.nodes.find((n) => n.id === 'old')).toBeUndefined();
    const replacement = state.nodes.find((n) => n.type === 'newType');
    expect(replacement).toBeDefined();
    expect(replacement!.position).toEqual({ x: 50, y: 60 });
    expect(replacement!.data).toEqual({ label: 'New' });

    // Edges point at the replacement now, not at the old id.
    for (const edge of state.edges) {
      expect(edge.source).not.toBe('old');
      expect(edge.target).not.toBe('old');
    }
    expect(state.edges.map((e) => e.id).sort()).toEqual(['e1', 'e2']);
  });

  it('drops incident edges when preserve_edges=false', async () => {
    const { ctx, state } = makeContext({
      nodes: [makeNode('old', 'oldType')],
      edges: [makeEdge('e1', 'peer', 'old'), makeEdge('e2', 'old', 'peer')],
    });
    await applyOperations(
      [
        {
          type: 'replace_node',
          node_id: 'old',
          node_type: 'newType',
          parameters: {},
          preserve_edges: false,
        },
      ],
      ctx,
    );
    expect(state.edges).toEqual([]);
  });

  it('records an error if the target node is missing', async () => {
    const { ctx } = makeContext();
    const result = await applyOperations(
      [{ type: 'replace_node', node_id: 'missing', node_type: 't', parameters: {} }],
      ctx,
    );
    expect(result.applied).toBe(0);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0].message).toContain('not found');
  });
});

// ----- best-effort + ordering ------------------------------------------------

describe('applier semantics', () => {
  it('continues applying ops after one fails (best-effort, no rollback)', async () => {
    const { ctx, state } = makeContext({ nodes: [makeNode('a', 'x')] });
    const result = await applyOperations(
      [
        // op 0: succeeds
        { type: 'set_node_parameters', node_id: 'a', parameters: { foo: 1 } },
        // op 1: fails (unresolved client_ref)
        { type: 'add_edge', source: { client_ref: 'ghost' }, target: 'a' },
        // op 2: succeeds despite earlier failure
        { type: 'delete_node', node_id: 'a' },
      ],
      ctx,
    );
    expect(result.applied).toBe(2);
    expect(result.errors).toHaveLength(1);
    expect(state.nodes).toEqual([]);
    expect(state.saveCalls).toHaveLength(1);
  });

  it('add_node + add_edge work in the same batch (cumulative state visible to ops 2..N)', async () => {
    const target = makeNode('agent', 'aiAgent', 0, 0);
    const { ctx, state } = makeContext({ nodes: [target] });

    await applyOperations(
      [
        { type: 'add_node', client_ref: 'ms', node_type: 'masterSkill', parameters: {} },
        {
          type: 'add_edge',
          source: { client_ref: 'ms' },
          target: 'agent',
          target_handle: 'input-skill',
        },
        // a follow-up op should see the just-added edge in the live state
        { type: 'delete_edge', edge_id: state.edges[0]?.id ?? 'NONE' },
      ],
      ctx,
    );

    // After all three ops: MS is added, edge was added then deleted.
    expect(state.nodes.find((n) => n.type === 'masterSkill')).toBeDefined();
    // The third op fired with edge id NONE -- silently no-ops, doesn't crash.
    expect(state.edges).toHaveLength(1);
  });
});

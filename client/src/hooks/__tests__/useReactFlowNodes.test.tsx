/**
 * Locks the node-delete path the canvas context menu uses.
 *
 * `deleteNodeById` must behave like the Delete key: remove the node AND
 * its edges, and report the removed edges through `onEdgesDelete` (which
 * is where auto-skill learns that a tool was disconnected). The context
 * menu used to call `onNodesDelete` directly, which removed only the node
 * and left its edges in the graph, where they were saved and later failed
 * Run / Start with DANGLING_EDGE.
 */

import { describe, it, expect, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import ReactFlow, {
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
} from 'reactflow';
import { useReactFlowNodes } from '../useReactFlowNodes';

const initialNodes: Node[] = [
  { id: 'agent', position: { x: 0, y: 0 }, data: { label: 'Agent' } },
  { id: 'tool', position: { x: 0, y: 200 }, data: { label: 'Tool' } },
  { id: 'other', position: { x: 300, y: 0 }, data: { label: 'Other' } },
];
const initialEdges: Edge[] = [
  { id: 'tool-agent', source: 'tool', target: 'agent', targetHandle: 'input-tools' },
  { id: 'agent-other', source: 'agent', target: 'other' },
];

interface Exposed {
  deleteNodeById: (nodeId: string) => void;
  nodes: Node[];
  edges: Edge[];
}

function Harness({ exposed, onEdgesDelete }: { exposed: { current: Exposed | null }; onEdgesDelete: (edges: Edge[]) => void }) {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const { onNodesDelete, deleteNodeById } = useReactFlowNodes({ setNodes, setEdges });
  exposed.current = { deleteNodeById, nodes, edges };
  return (
    <div style={{ width: 800, height: 600 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
      />
    </div>
  );
}

describe('useReactFlowNodes.deleteNodeById', () => {
  it('removes the node together with its edges and reports the removed edges', () => {
    const exposed: { current: Exposed | null } = { current: null };
    const onEdgesDelete = vi.fn();
    render(
      <ReactFlowProvider>
        <Harness exposed={exposed} onEdgesDelete={onEdgesDelete} />
      </ReactFlowProvider>,
    );

    act(() => exposed.current!.deleteNodeById('tool'));

    expect(exposed.current!.nodes.map(n => n.id)).toEqual(['agent', 'other']);
    expect(exposed.current!.edges.map(e => e.id)).toEqual(['agent-other']);
    expect(onEdgesDelete).toHaveBeenCalledTimes(1);
    expect(onEdgesDelete.mock.calls[0][0].map((e: Edge) => e.id)).toEqual(['tool-agent']);
  });

  it('leaves no edge pointing at a deleted node with several connections', () => {
    const exposed: { current: Exposed | null } = { current: null };
    render(
      <ReactFlowProvider>
        <Harness exposed={exposed} onEdgesDelete={vi.fn()} />
      </ReactFlowProvider>,
    );

    act(() => exposed.current!.deleteNodeById('agent'));

    const ids = new Set(exposed.current!.nodes.map(n => n.id));
    expect(ids.has('agent')).toBe(false);
    for (const edge of exposed.current!.edges) {
      expect(ids.has(edge.source) && ids.has(edge.target)).toBe(true);
    }
    expect(exposed.current!.edges).toEqual([]);
  });
});

import type { Node } from 'reactflow';
import { resolveNodeDescription } from '@/lib/nodeSpec';

export interface WorkspaceNode { kind: string; node_id: string; label: string }

/** NodeSpec owns discovery; the client only maps known renderer kinds. */
export function workspaceNodes(nodes: Node[]): WorkspaceNode[] {
  return nodes.flatMap((node) => {
    const definition = resolveNodeDescription(node.type || '');
    const kind = definition?.uiHints?.workspace?.kind;
    return kind ? [{ kind, node_id: node.id, label: String(node.data?.label || definition?.displayName || kind) }] : [];
  });
}

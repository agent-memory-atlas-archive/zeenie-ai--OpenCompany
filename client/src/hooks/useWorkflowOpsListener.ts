/**
 * The two listeners for backend-pushed `workflow_ops_apply` batches
 * (server/services/workflow_ops.py `broadcast_workflow_ops`).
 *
 * `useWorkflowOpsListener`, mounted in Dashboard, applies a batch for the
 * open workflow to the live React Flow canvas:
 *   - a batch the server already saved (`persisted: true`: the Agent
 *     Builder, Turn on Talk) is adopted with the server's ids, and nothing
 *     is saved again (a re-save would replace the rows the server merged);
 *   - any other goes through `applyOperations`, which saves its rows;
 *   - a batch for another workflow shows a toast naming who changed.
 *
 * `useSavedGraphSync`, mounted once in the app shell, adopts saved batches
 * into the app store's copy of the workflow the editor holds, on either
 * screen, and puts their parameter rows in the parameter cache. Without it
 * a workflow left open in Dev while Home is showing keeps its old graph,
 * and the editor's next save replaces the server's additions.
 */

import { useEffect } from 'react';
import type { Node, Edge } from 'reactflow';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';

import { useWebSocket, useWebSocketActions, type NodeParameters } from '../contexts/WebSocketContext';
import { useAppStore } from '../store/useAppStore';
import {
  addSavedEdges,
  addSavedNodes,
  applyOperations,
  type WorkflowOpsApplyEvent,
} from '../lib/workflowOps';
import { WORKFLOWS_QUERY_KEY, type SavedWorkflow } from './useWorkflowsQuery';
import { nodeParamsQueryKey } from './useNodeParamsQuery';

interface UseWorkflowOpsListenerProps {
  nodes: Node[];
  edges: Edge[];
  setNodes: (updater: (ns: Node[]) => Node[]) => void;
  setEdges: (updater: (es: Edge[]) => Edge[]) => void;
}

export function useWorkflowOpsListener({
  nodes,
  edges,
  setNodes,
  setEdges,
}: UseWorkflowOpsListenerProps) {
  const { addEventListener, saveNodeParameters } = useWebSocket();
  const currentWorkflowId = useAppStore(s => s.currentWorkflow?.id);
  const queryClient = useQueryClient();

  useEffect(() => {
    const unsubscribe = addEventListener('workflow_ops_apply', (raw: WorkflowOpsApplyEvent) => {
      const ops = raw?.operations ?? [];
      if (ops.length === 0) return;

      // Another workflow changed (an employee adding a tool from Talk).
      if (raw.workflow_id && raw.workflow_id !== currentWorkflowId) {
        const name = queryClient
          .getQueryData<SavedWorkflow[]>(WORKFLOWS_QUERY_KEY)
          ?.find(workflow => workflow.id === raw.workflow_id)?.name;
        toast.message(`${name ?? 'An employee'} updated their tools`);
        return;
      }

      if (raw.persisted) {
        setNodes(ns => addSavedNodes(ns, ops));
        setEdges(es => addSavedEdges(es, ops));
        return;
      }

      void applyOperations(ops, {
        workflowId: currentWorkflowId,
        nodes,
        edges,
        setNodes,
        setEdges,
        saveNodeParameters,
      }).then(result => {
        if (result.errors.length > 0) {
          console.warn('[workflow_ops_apply] some ops failed:', result.errors);
        }
      });
    });

    return unsubscribe;
  }, [
    addEventListener, currentWorkflowId, queryClient,
    nodes, edges, setNodes, setEdges, saveNodeParameters,
  ]);
}

export function useSavedGraphSync(): void {
  const { addEventListener } = useWebSocketActions();
  const queryClient = useQueryClient();

  useEffect(() => addEventListener('workflow_ops_apply', (raw: WorkflowOpsApplyEvent) => {
    if (!raw?.persisted || !raw.workflow_id) return;
    const ops = raw.operations ?? [];
    for (const op of ops) {
      if (op.type !== 'add_node' && op.type !== 'set_node_parameters') continue;
      // Either carries the node's whole row as saved: a new node's, or a merged one.
      const nodeId = op.type === 'add_node' ? op.minted_id : op.node_id;
      if (!nodeId) continue;
      queryClient.setQueryData<NodeParameters | null>(nodeParamsQueryKey(nodeId), prev => ({
        parameters: op.parameters,
        version: (prev?.version ?? 0) + 1,
      }));
    }
    useAppStore.getState().adoptSavedOperations(raw.workflow_id, ops);
  }), [addEventListener, queryClient]);
}

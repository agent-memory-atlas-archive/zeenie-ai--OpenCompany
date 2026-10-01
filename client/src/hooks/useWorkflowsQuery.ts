/**
 * Workflow list + single-workflow queries, and the save mutation.
 *
 * Server-owned data (the workflow list and individual workflow records)
 * is read from the backend database through shared Query options. Zustand
 * keeps only the mutable edit buffer (currentWorkflow). Deletion goes through
 * useAppStore.deleteWorkflow so both modes share confirmation-result cleanup.
 *
 * Mirrors the ownership boundary established by `useCatalogueQuery.ts`.
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  queryOptions,
  type UseQueryResult,
} from '@tanstack/react-query';
import {
  workflowApi,
  type WorkflowSummary,
  type WorkflowData as ApiWorkflowData,
} from '../services/workflowApi';

export const WORKFLOWS_QUERY_KEY = ['workflows'] as const;
export const workflowQueryKey = (id: string) => ['workflow', id] as const;

export interface SavedWorkflow {
  id: string;
  name: string;
  nodeCount: number;
  createdAt: Date;
  lastModified: Date;
}

function toSavedWorkflow(w: WorkflowSummary): SavedWorkflow {
  return {
    id: w.id,
    name: w.name,
    nodeCount: w.nodeCount,
    createdAt: new Date(w.createdAt),
    lastModified: new Date(w.lastModified),
  };
}

export function workflowsQueryOptions() {
  return queryOptions({
    queryKey: WORKFLOWS_QUERY_KEY,
    queryFn: async () => {
      const list = await workflowApi.getAllWorkflows();
      return list.map(toSavedWorkflow);
    },
    staleTime: 30_000,
  });
}

export function useWorkflowsQuery(): UseQueryResult<SavedWorkflow[], Error> {
  return useQuery(workflowsQueryOptions());
}

export interface SaveWorkflowInput {
  id: string;
  name: string;
  data: { nodes: any[]; edges: any[] };
}

export function useSaveWorkflowMutation() {
  const qc = useQueryClient();
  return useMutation<void, Error, SaveWorkflowInput>({
    mutationFn: async ({ id, name, data }) => {
      const ok = await workflowApi.saveWorkflow(id, name, data);
      if (!ok) throw new Error('Failed to save workflow');
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
    },
  });
}

export function workflowQueryOptions(id: string | null | undefined) {
  return queryOptions<ApiWorkflowData | null, Error>({
    queryKey: id ? workflowQueryKey(id) : ['workflow', 'none'],
    queryFn: () => (id ? workflowApi.getWorkflow(id) : Promise.resolve(null)),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useWorkflowQuery(id: string | null | undefined) {
  return useQuery(workflowQueryOptions(id));
}

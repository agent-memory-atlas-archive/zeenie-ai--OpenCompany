/**
 * Per-node parameter query + save mutation.
 *
 * Wraps the WebSocket get_node_parameters / save_node_parameters request
 * pair in TanStack Query so:
 *  - sibling components reading the same nodeId share one network call
 *  - cache invalidation after save is automatic
 *  - loading / error states are first-class
 *
 * Replaces the hand-rolled useState+useEffect pattern in useParameterPanel.
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useWebSocket, type NodeParameters } from '../contexts/WebSocketContext';

export type NodeParametersResponse = NodeParameters;

export const nodeParamsQueryKey = (nodeId: string) =>
  ['nodeParams', nodeId] as const;

/**
 * Read a node's saved row for the `nodeParams` cache. Every query on that
 * key uses this, so the cache holds real rows only.
 *
 * A node with no saved row comes back as `{parameters: {}}`, so `null` from
 * `getNodeParameters` means the request failed, and this throws. The
 * parameter panel seeds its edit buffer from the cached row and saves the
 * whole row back; a failed read cached as an empty row would let the next
 * Save replace the node's settings with defaults.
 */
export async function fetchSavedNodeParams(
  getNodeParameters: (nodeId: string) => Promise<NodeParametersResponse | null>,
  nodeId: string,
): Promise<NodeParametersResponse> {
  const stored = await getNodeParameters(nodeId);
  if (stored === null) throw new Error(`Failed to load saved parameters for ${nodeId}`);
  return stored;
}

export function useNodeParamsQuery(
  nodeId: string | null | undefined,
): UseQueryResult<NodeParametersResponse | null, Error> {
  const { getNodeParameters, isReady } = useWebSocket();
  return useQuery<NodeParametersResponse | null, Error>({
    queryKey: nodeId ? nodeParamsQueryKey(nodeId) : ['nodeParams', 'none'],
    queryFn: () => (nodeId ? fetchSavedNodeParams(getNodeParameters, nodeId) : Promise.resolve(null)),
    enabled: !!nodeId && isReady,
    staleTime: 60_000,
  });
}

export interface SaveNodeParamsInput {
  nodeId: string;
  parameters: Record<string, any>;
  version?: number;
}

export function useSaveNodeParamsMutation() {
  const { saveNodeParameters } = useWebSocket();
  const qc = useQueryClient();
  return useMutation<boolean, Error, SaveNodeParamsInput>({
    mutationFn: async ({ nodeId, parameters, version }) => {
      const ok = await saveNodeParameters(nodeId, parameters, version);
      if (!ok) throw new Error('Failed to save node parameters');
      return ok;
    },
    onSuccess: (_ok, { nodeId, parameters }) => {
      // Optimistic-ish update: stamp the new params into the query cache so
      // dependents read the freshest value without an extra WS roundtrip.
      qc.setQueryData<NodeParametersResponse | null>(
        nodeParamsQueryKey(nodeId),
        (prev) => ({
          parameters,
          version: (prev?.version ?? 0) + 1,
          timestamp: Date.now(),
        }),
      );
    },
  });
}

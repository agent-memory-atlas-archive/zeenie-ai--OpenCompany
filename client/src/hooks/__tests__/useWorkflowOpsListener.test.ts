/**
 * Tests for the two `workflow_ops_apply` listeners.
 *
 * useWorkflowOpsListener (Dashboard):
 *   - subscribes via the WS context addEventListener API, unsubscribes on unmount
 *   - a batch for the open workflow goes through applyOperations...
 *   - ...unless the server already saved it (`persisted`): then its ids are
 *     adopted onto the canvas and nothing is saved again
 *   - a batch for another workflow shows "{Name} updated their tools"
 *
 * useSavedGraphSync (app shell, the stale-editor guard):
 *   - a saved batch reaches the app store's copy of the workflow and the
 *     parameter cache, whichever screen shows
 */

import { createElement, type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Node, Edge } from 'reactflow';

import { useSavedGraphSync, useWorkflowOpsListener } from '../useWorkflowOpsListener';
import { WORKFLOWS_QUERY_KEY } from '../useWorkflowsQuery';
import { nodeParamsQueryKey } from '../useNodeParamsQuery';

// --- mocks (must come before importing modules that read them) ----------

const listeners = new Map<string, (data: any) => void>();
const unsubscribeMock = vi.fn();
const addEventListener = vi.fn((type: string, handler: (data: any) => void) => {
  listeners.set(type, handler);
  return unsubscribeMock;
});
const saveNodeParameters = vi.fn().mockResolvedValue(true);

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ addEventListener, saveNodeParameters }),
  useWebSocketActions: () => ({ addEventListener }),
}));

const storeMock = {
  currentWorkflowId: 'wf-current',
  adoptSavedOperations: vi.fn(),
};

vi.mock('../../store/useAppStore', () => {
  const useAppStore = (selector: any) => selector({ currentWorkflow: { id: storeMock.currentWorkflowId } });
  useAppStore.getState = () => ({ adoptSavedOperations: storeMock.adoptSavedOperations });
  return { useAppStore };
});

const applyOpsMock = vi.fn().mockResolvedValue({ applied: 0, errors: [], refMap: {} });
vi.mock('../../lib/workflowOps', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/workflowOps')>()),
  applyOperations: (...args: any[]) => applyOpsMock(...args),
}));

const toastMessageMock = vi.fn();
vi.mock('sonner', () => ({
  toast: { message: (...args: any[]) => toastMessageMock(...args) },
}));

// --- test scaffolding ---------------------------------------------------

function canvas(initial: { nodes?: Node[]; edges?: Edge[] } = {}) {
  const state = { nodes: initial.nodes ?? [], edges: initial.edges ?? [] };
  return {
    state,
    props: {
      nodes: state.nodes,
      edges: state.edges,
      setNodes: vi.fn((update: (ns: Node[]) => Node[]) => { state.nodes = update(state.nodes); }),
      setEdges: vi.fn((update: (es: Edge[]) => Edge[]) => { state.edges = update(state.edges); }),
    },
  };
}

let queryClient: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: queryClient }, children);

function push(data: any) {
  act(() => {
    listeners.get('workflow_ops_apply')!(data);
  });
}

const SAVED = [
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
  { type: 'set_node_parameters', node_id: '7:masterSkill:1', parameters: { skills_config: { a: { enabled: true } } } },
];

beforeEach(() => {
  vi.clearAllMocks();
  listeners.clear();
  storeMock.currentWorkflowId = 'wf-current';
  queryClient = new QueryClient();
});

// ---------------------------------------------------------------------------

describe('useWorkflowOpsListener', () => {
  it('subscribes on mount and unsubscribes on unmount', () => {
    const { unmount } = renderHook(() => useWorkflowOpsListener(canvas().props), { wrapper });
    expect(addEventListener).toHaveBeenCalledWith('workflow_ops_apply', expect.any(Function));
    unmount();
    expect(unsubscribeMock).toHaveBeenCalledTimes(1);
  });

  it('applies a batch for the open workflow through applyOperations', () => {
    renderHook(() => useWorkflowOpsListener(canvas().props), { wrapper });
    const ops = [{ type: 'add_node', client_ref: 'n', node_type: 'x', parameters: {} }];

    push({ workflow_id: 'wf-current', caller_node_id: 'agent-1', operations: ops });

    expect(applyOpsMock).toHaveBeenCalledTimes(1);
    expect(applyOpsMock.mock.calls[0][0]).toEqual(ops);
    expect(toastMessageMock).not.toHaveBeenCalled();
  });

  it('adopts a saved batch with the server ids and saves nothing', () => {
    storeMock.currentWorkflowId = '7';
    const { state, props } = canvas({ nodes: [{ id: '7:aiAgent:1', type: 'aiAgent', position: { x: 0, y: 0 }, data: { label: 'Maya' } }] });
    renderHook(() => useWorkflowOpsListener(props), { wrapper });

    push({ workflow_id: '7', caller_node_id: '7:aiAgent:1', operations: SAVED, persisted: true });
    push({ workflow_id: '7', caller_node_id: '7:aiAgent:1', operations: SAVED, persisted: true });

    expect(state.nodes.map((n) => n.id)).toEqual(['7:aiAgent:1', '7:duckduckgoSearch:1']);
    expect(state.edges.map((e) => e.id)).toEqual(['e-7:duckduckgoSearch:1-output-tool-7:aiAgent:1-input-tools']);
    expect(applyOpsMock).not.toHaveBeenCalled();
    expect(saveNodeParameters).not.toHaveBeenCalled();
  });

  it('names the employee whose workflow changed, from the workflow list', () => {
    queryClient.setQueryData(WORKFLOWS_QUERY_KEY, [{ id: 'wf-other', name: 'Maya' }]);
    renderHook(() => useWorkflowOpsListener(canvas().props), { wrapper });

    push({ workflow_id: 'wf-other', operations: SAVED, persisted: true });

    expect(toastMessageMock).toHaveBeenCalledWith('Maya updated their tools');
    expect(applyOpsMock).not.toHaveBeenCalled();
  });

  it('falls back to a generic name when the workflow list is not loaded', () => {
    renderHook(() => useWorkflowOpsListener(canvas().props), { wrapper });

    push({ workflow_id: 'wf-other', operations: SAVED });

    expect(toastMessageMock).toHaveBeenCalledWith('An employee updated their tools');
  });

  it('ignores a batch without operations', () => {
    renderHook(() => useWorkflowOpsListener(canvas().props), { wrapper });

    push({ workflow_id: 'wf-current', operations: [] });

    expect(applyOpsMock).not.toHaveBeenCalled();
    expect(toastMessageMock).not.toHaveBeenCalled();
  });
});

describe('useSavedGraphSync', () => {
  it('adopts a saved batch into the store and puts its rows in the parameter cache', () => {
    queryClient.setQueryData(nodeParamsQueryKey('7:masterSkill:1'), { parameters: { skills_config: {} }, version: 2 });
    renderHook(() => useSavedGraphSync(), { wrapper });

    push({ workflow_id: '7', caller_node_id: '7:aiAgent:1', operations: SAVED, persisted: true });

    expect(storeMock.adoptSavedOperations).toHaveBeenCalledWith('7', SAVED);
    expect(queryClient.getQueryData(nodeParamsQueryKey('7:duckduckgoSearch:1'))).toEqual({ parameters: { max_results: 5 }, version: 1 });
    expect(queryClient.getQueryData(nodeParamsQueryKey('7:masterSkill:1'))).toEqual({
      parameters: { skills_config: { a: { enabled: true } } },
      version: 3,
    });
  });

  it('leaves a batch the server did not save to the editor', () => {
    renderHook(() => useSavedGraphSync(), { wrapper });

    push({ workflow_id: '7', operations: SAVED });

    expect(storeMock.adoptSavedOperations).not.toHaveBeenCalled();
    expect(queryClient.getQueryData(nodeParamsQueryKey('7:duckduckgoSearch:1'))).toBeUndefined();
  });
});

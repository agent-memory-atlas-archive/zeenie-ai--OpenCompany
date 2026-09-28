/**
 * useAppStore.adoptSavedOperations: the stale-editor guard.
 *
 * The editor's working copy of a workflow stays in the store while Home is
 * showing. A batch the server saved meanwhile (an employee adding a tool
 * from Talk) must reach that copy, or the editor's next save replaces the
 * graph without it. Adopting it marks nothing unsaved and leaves
 * `lastModified` alone, so an open canvas is not reset from the store.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const saveWorkflowMock = vi.fn();
vi.mock('../../services/workflowApi', () => ({
  workflowApi: {
    getAllWorkflows: vi.fn(),
    getWorkflow: vi.fn(),
    saveWorkflow: (...args: unknown[]) => saveWorkflowMock(...args),
    deleteWorkflow: vi.fn(),
  },
}));

import { useAppStore, type WorkflowData } from '../useAppStore';
import type { WorkflowOperation } from '../../lib/workflowOps';

const lastModified = new Date('2026-09-28T10:00:00Z');

function workflow(id: string): WorkflowData {
  return {
    id,
    name: 'Maya',
    slug: 'Maya_1',
    nodes: [{ id: '7:aiAgent:1', type: 'aiAgent', position: { x: 0, y: 0 }, data: { label: 'Maya' } }],
    edges: [],
    createdAt: lastModified,
    lastModified,
  };
}

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
];

beforeEach(() => {
  saveWorkflowMock.mockReset();
  useAppStore.setState({ currentWorkflow: workflow('7'), hasUnsavedChanges: false });
});

describe('adoptSavedOperations', () => {
  it('adds the saved nodes and edges without marking anything unsaved', () => {
    useAppStore.getState().adoptSavedOperations('7', SAVED);

    const { currentWorkflow, hasUnsavedChanges } = useAppStore.getState();
    expect(currentWorkflow!.nodes.map((n) => n.id)).toEqual(['7:aiAgent:1', '7:duckduckgoSearch:1']);
    expect(currentWorkflow!.edges.map((e) => e.id)).toEqual(['e-7:duckduckgoSearch:1-output-tool-7:aiAgent:1-input-tools']);
    expect(hasUnsavedChanges).toBe(false);
    expect(currentWorkflow!.lastModified).toBe(lastModified);
  });

  it('keeps unsaved editor work unsaved, and adds nothing twice', () => {
    useAppStore.setState({ hasUnsavedChanges: true });

    useAppStore.getState().adoptSavedOperations('7', SAVED);
    const once = useAppStore.getState().currentWorkflow;
    useAppStore.getState().adoptSavedOperations('7', SAVED);

    expect(useAppStore.getState().currentWorkflow).toBe(once);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('leaves another workflow alone', () => {
    const before = useAppStore.getState().currentWorkflow;

    useAppStore.getState().adoptSavedOperations('8', SAVED);

    expect(useAppStore.getState().currentWorkflow).toBe(before);
  });

  it('the next save keeps what the server added', async () => {
    saveWorkflowMock.mockResolvedValue({ id: '7', slug: 'Maya_1' });
    useAppStore.getState().adoptSavedOperations('7', SAVED);

    await useAppStore.getState().saveWorkflow();

    const [, , data] = saveWorkflowMock.mock.calls[0];
    expect(data.nodes.map((n: { id: string }) => n.id)).toContain('7:duckduckgoSearch:1');
    expect(data.edges.map((e: { id: string }) => e.id)).toContain('e-7:duckduckgoSearch:1-output-tool-7:aiAgent:1-input-tools');
  });
});

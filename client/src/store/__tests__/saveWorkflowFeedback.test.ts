/**
 * A workflow save the server refuses, or never answers, tells the user why
 * and keeps the changes marked unsaved. It used to log to the console only,
 * so the Save button just stayed lit.
 *
 * Start shows the graph it admitted without clearing `hasUnsavedChanges`.
 * Start stores that graph on the new generation, not on the workflow, so
 * clearing the flag hid edits that a reload would lose.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Node } from 'reactflow';
import { toast } from 'sonner';

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
import type { SaveWorkflowFailure } from '../../services/workflowApi';

function workflow(id: string): WorkflowData {
  return { id, name: 'Maya', slug: 'Maya_1', nodes: [], edges: [], createdAt: new Date(), lastModified: new Date() };
}

/** The next save is not saved, and the API reports `failure`. */
function refuseWith(failure: SaveWorkflowFailure) {
  saveWorkflowMock.mockImplementation(
    async (_id: string, _name: string, _data: unknown, onFailure?: (reason: SaveWorkflowFailure) => void) => {
      onFailure?.(failure);
      return null;
    },
  );
}

let toastError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  saveWorkflowMock.mockReset();
  toastError = vi.spyOn(toast, 'error').mockImplementation(() => 'toast');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  useAppStore.setState({ currentWorkflow: workflow('7'), hasUnsavedChanges: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('saveWorkflow feedback', () => {
  it('names the first validation issue when the server refuses the graph', async () => {
    refuseWith({
      error: 'invalid_context_topology',
      messages: ['A Context node cannot serve multiple agents', 'An agent cannot be connected to multiple Context nodes'],
    });

    await expect(useAppStore.getState().saveWorkflow()).resolves.toBe(false);

    expect(toastError).toHaveBeenCalledWith(
      'Could not save "Maya": A Context node cannot serve multiple agents (and 1 more)',
    );
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('says the workflow is gone when it was deleted meanwhile', async () => {
    refuseWith({ error: 'workflow_not_found', messages: [] });

    await useAppStore.getState().saveWorkflow();

    expect(toastError).toHaveBeenCalledWith('"Maya" no longer exists, so it was not saved.');
  });

  it('says the server did not answer when the request never completed', async () => {
    refuseWith({ error: 'unreachable', messages: [] });

    await useAppStore.getState().saveWorkflow();

    expect(toastError).toHaveBeenCalledWith(
      'Could not save "Maya": the server did not answer. Your changes are still in the editor.',
    );
  });

  it('answers true and clears the flag when the server saved it', async () => {
    saveWorkflowMock.mockResolvedValue({ success: true, id: '7', slug: 'Maya_1' });

    await expect(useAppStore.getState().saveWorkflow()).resolves.toBe(true);

    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe('createNewWorkflow feedback', () => {
  it('tells the user when no workflow could be created', async () => {
    saveWorkflowMock.mockResolvedValue(null);

    await useAppStore.getState().createNewWorkflow();

    expect(toastError).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().currentWorkflow?.id).toBe('7');
  });
});

describe('adoptStartedGraph', () => {
  const admitted: Node[] = [{ id: '7:start:1', type: 'start', position: { x: 0, y: 0 }, data: {} }];

  it('shows the admitted graph and keeps unsaved edits marked unsaved', () => {
    useAppStore.getState().adoptStartedGraph('7', admitted, []);

    const { currentWorkflow, hasUnsavedChanges } = useAppStore.getState();
    expect(currentWorkflow?.nodes).toBe(admitted);
    expect(hasUnsavedChanges).toBe(true);
  });

  it('leaves a saved workflow saved', () => {
    useAppStore.setState({ hasUnsavedChanges: false });

    useAppStore.getState().adoptStartedGraph('7', admitted, []);

    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('ignores a Start answer for a workflow that is no longer open', () => {
    useAppStore.getState().adoptStartedGraph('8', admitted, []);

    expect(useAppStore.getState().currentWorkflow?.nodes).toEqual([]);
  });
});

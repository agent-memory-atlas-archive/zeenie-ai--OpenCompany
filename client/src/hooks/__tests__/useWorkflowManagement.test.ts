/**
 * Choosing another workflow in the sidebar, or New, replaces the editor's
 * working copy. Both used to do it without a word, so unsaved changes were
 * simply dropped; they now settle them first, as the mode switch does.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const settle = vi.hoisted(() => vi.fn());
vi.mock('../../app/unsavedWork', () => ({ settleUnsavedWork: settle }));
vi.mock('../useWorkflowsQuery', () => ({ useWorkflowsQuery: () => ({ data: [] }) }));

import { useWorkflowManagement } from '../useWorkflowManagement';
import { useAppStore, type WorkflowData } from '../../store/useAppStore';
import type { SavedWorkflow } from '../useWorkflowsQuery';

function workflow(id: string): WorkflowData {
  return { id, name: `Workflow ${id}`, slug: `${id}_1`, nodes: [], edges: [], createdAt: new Date(), lastModified: new Date() };
}

const saved = (id: string) => ({ id, name: `Workflow ${id}` }) as SavedWorkflow;

const loadWorkflow = vi.fn(async () => {});
const createNewWorkflow = vi.fn(async () => {});

beforeEach(() => {
  settle.mockReset();
  loadWorkflow.mockClear();
  createNewWorkflow.mockClear();
  useAppStore.setState({ currentWorkflow: workflow('a'), hasUnsavedChanges: true, loadWorkflow, createNewWorkflow });
});

describe('useWorkflowManagement', () => {
  it('settles unsaved work before opening another workflow', async () => {
    settle.mockResolvedValue(true);
    const { result } = renderHook(() => useWorkflowManagement());

    await act(() => result.current.handleSelectWorkflow(saved('b')));

    expect(settle).toHaveBeenCalledWith('b');
    expect(loadWorkflow).toHaveBeenCalledWith('b');
  });

  it('opens nothing when the unsaved work is kept', async () => {
    settle.mockResolvedValue(false);
    const { result } = renderHook(() => useWorkflowManagement());

    await act(() => result.current.handleSelectWorkflow(saved('b')));

    expect(loadWorkflow).not.toHaveBeenCalled();
  });

  it('does not reload the workflow already open', async () => {
    const { result } = renderHook(() => useWorkflowManagement());

    await act(() => result.current.handleSelectWorkflow(saved('a')));

    expect(settle).not.toHaveBeenCalled();
    expect(loadWorkflow).not.toHaveBeenCalled();
  });

  it('settles unsaved work before creating a workflow', async () => {
    settle.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { result } = renderHook(() => useWorkflowManagement());

    await act(() => result.current.handleNew());
    expect(createNewWorkflow).not.toHaveBeenCalled();

    await act(() => result.current.handleNew());
    expect(createNewWorkflow).toHaveBeenCalledTimes(1);
  });
});

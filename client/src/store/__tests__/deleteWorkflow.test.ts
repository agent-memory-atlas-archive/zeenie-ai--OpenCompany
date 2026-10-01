import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { workflowApi } from '../../services/workflowApi';
import { queryClient } from '../../lib/queryClient';
import { WORKFLOWS_QUERY_KEY, workflowQueryKey } from '../../hooks/useWorkflowsQuery';
import { EMPLOYEES_QUERY_KEY, employeeDetailKey } from '../../features/home/data/employeeCache';
import { useHomeStore } from '../../features/home/state/homeStore';
import { useAppStore, type WorkflowData } from '../useAppStore';

vi.mock('../../services/workflowApi', () => ({
  workflowApi: { getAllWorkflows: vi.fn(), getWorkflow: vi.fn(), saveWorkflow: vi.fn(), deleteWorkflow: vi.fn() },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

let sequence = 0;
let employee: WorkflowData;
const record = (workflow: WorkflowData) => ({
  ...workflow, data: { nodes: workflow.nodes, edges: workflow.edges },
  createdAt: workflow.createdAt.toISOString(), lastModified: workflow.lastModified.toISOString(),
});

beforeEach(() => {
  vi.resetAllMocks();
  queryClient.clear();
  employee = {
    id: `delete-test-${++sequence}`, name: 'Maya', slug: 'maya',
    nodes: [{ id: 'agent', type: 'aiAgent', position: { x: 0, y: 0 }, data: {} }],
    edges: [], createdAt: new Date(0), lastModified: new Date(0),
  };
  useAppStore.setState({
    currentWorkflow: employee, hasUnsavedChanges: true, selectedNode: employee.nodes[0],
    workflowUIStates: {}, renamingNodeId: 'agent',
  });
  useHomeStore.setState({ view: { kind: 'hire' }, workspaceFor: null, hireNotice: null, glow: null });
  vi.mocked(workflowApi.deleteWorkflow).mockResolvedValue(true);
});

afterEach(() => {
  queryClient.clear();
  vi.restoreAllMocks();
});

describe('workflow deletion', () => {
  it('removes the current employee from both modes without creating an Untitled replacement', async () => {
    useHomeStore.getState().showEmployee(employee.id);
    useAppStore.getState().setWorkflowViewport(employee.id, { x: 10, y: 20, zoom: 1 });
    queryClient.setQueryData(WORKFLOWS_QUERY_KEY, [employee]);
    queryClient.setQueryData(workflowQueryKey(employee.id), record(employee));
    queryClient.setQueryData(EMPLOYEES_QUERY_KEY, [{ workflow_id: employee.id, name: employee.name }]);
    queryClient.setQueryData(employeeDetailKey(employee.id), { workflow_id: employee.id });

    expect(await useAppStore.getState().deleteWorkflow(employee.id)).toBe(true);
    expect(workflowApi.saveWorkflow).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({ currentWorkflow: null, hasUnsavedChanges: false, selectedNode: null, renamingNodeId: null });
    expect(useAppStore.getState().workflowUIStates[employee.id]).toBeUndefined();
    expect(queryClient.getQueryData(WORKFLOWS_QUERY_KEY)).toEqual([]);
    expect(queryClient.getQueryData(EMPLOYEES_QUERY_KEY)).toEqual([]);
    expect(queryClient.getQueryData(workflowQueryKey(employee.id))).toBeUndefined();
    expect(queryClient.getQueryData(employeeDetailKey(employee.id))).toBeUndefined();
    expect(useHomeStore.getState()).toMatchObject({ view: { kind: 'hire' }, workspaceFor: null });
  });

  it('keeps the graph and team when deletion fails', async () => {
    vi.mocked(workflowApi.deleteWorkflow).mockResolvedValue(false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    queryClient.setQueryData(EMPLOYEES_QUERY_KEY, [{ workflow_id: employee.id }]);
    expect(await useAppStore.getState().deleteWorkflow(employee.id)).toBe(false);
    expect(useAppStore.getState().currentWorkflow).toBe(employee);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
    expect(queryClient.getQueryData(EMPLOYEES_QUERY_KEY)).toEqual([{ workflow_id: employee.id }]);
  });

  it('preserves another open workflow and its unsaved changes', async () => {
    await useAppStore.getState().deleteWorkflow(`other-${sequence}`);
    expect(useAppStore.getState().currentWorkflow).toBe(employee);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('checks the current selection after the delete finishes', async () => {
    const request = deferred<boolean>();
    vi.mocked(workflowApi.deleteWorkflow).mockReturnValue(request.promise);
    const deleting = useAppStore.getState().deleteWorkflow(employee.id);
    const next = { ...employee, id: `next-${sequence}` };
    useAppStore.getState().setCurrentWorkflow(next);
    request.resolve(true);
    await deleting;
    expect(useAppStore.getState().currentWorkflow).toBe(next);
    expect(workflowApi.saveWorkflow).not.toHaveBeenCalled();
  });

  it('ignores a load response that arrives after deletion', async () => {
    const request = deferred<ReturnType<typeof record>>();
    vi.mocked(workflowApi.getWorkflow).mockReturnValue(request.promise);
    const loading = useAppStore.getState().loadWorkflow(employee.id);
    await useAppStore.getState().deleteWorkflow(employee.id);
    request.resolve(record(employee));
    await loading;
    expect(useAppStore.getState().currentWorkflow).toBeNull();
  });

  it('ignores a save response that arrives after deletion', async () => {
    const request = deferred<{ success: boolean; id: string }>();
    vi.mocked(workflowApi.saveWorkflow).mockReturnValue(request.promise);
    const saving = useAppStore.getState().saveWorkflow();
    await useAppStore.getState().deleteWorkflow(employee.id);
    request.resolve({ success: true, id: employee.id });
    await saving;
    expect(useAppStore.getState().currentWorkflow).toBeNull();
  });

  it('ignores a migration response that arrives after deletion', async () => {
    employee.nodes[0].type = 'googleChatModel';
    const request = deferred<{ success: boolean; id: string }>();
    vi.mocked(workflowApi.saveWorkflow).mockReturnValue(request.promise);
    const migrating = useAppStore.getState().migrateCurrentWorkflow();
    await useAppStore.getState().deleteWorkflow(employee.id);
    request.resolve({ success: true, id: employee.id });
    await migrating;
    expect(useAppStore.getState().currentWorkflow).toBeNull();
  });

  it('applies remote deletion without issuing another DELETE or replacement save', () => {
    useAppStore.getState().forgetWorkflow(employee.id);
    expect(useAppStore.getState().currentWorkflow).toBeNull();
    expect(workflowApi.deleteWorkflow).not.toHaveBeenCalled();
    expect(workflowApi.saveWorkflow).not.toHaveBeenCalled();
  });

  it('still creates a workflow when New is explicitly requested', async () => {
    await useAppStore.getState().deleteWorkflow(employee.id);
    vi.mocked(workflowApi.saveWorkflow).mockResolvedValue({ success: true, id: `new-${sequence}` });
    await useAppStore.getState().createNewWorkflow();
    expect(workflowApi.saveWorkflow).toHaveBeenCalledWith('new', 'Untitled Workflow', { nodes: [], edges: [] });
    expect(useAppStore.getState().currentWorkflow?.id).toBe(`new-${sequence}`);
  });
});

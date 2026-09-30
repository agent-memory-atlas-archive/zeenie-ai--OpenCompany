/**
 * Mode switches: enterDev settles the editor's unsaved work before a
 * different workflow replaces it, opens the workflow and warms the editor
 * chunk in parallel, and only then runs the transition.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const events: string[] = [];

vi.mock('../../lib/featureFlags', () => ({ featureFlags: { normalMode: true, nodeSpecBackend: true } }));

vi.mock('../ShellModeSwitch', () => ({
  preloadEditor: vi.fn(async () => {
    events.push('preload');
  }),
  preloadHome: vi.fn(async () => {}),
}));

vi.mock('../shellTransition', () => ({
  transitionShell: vi.fn(async (mode: string) => {
    events.push(`transition:${mode}`);
  }),
}));

vi.mock('../../services/workflowApi', () => ({
  workflowApi: { getAllWorkflows: vi.fn(), getWorkflow: vi.fn(), saveWorkflow: vi.fn(), deleteWorkflow: vi.fn() },
}));

const toastError = vi.fn();
vi.mock('sonner', () => ({ toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

import { useAppStore } from '../../store/useAppStore';
import { useWorkflowSettingsStore } from '../../stores/workflowSettingsStore';
import { defaultSettings } from '../../components/ui/settingsPanel/schema';
import { enterDev, enterNormal, toggleShellMode } from '../useShellActions';
import { useHomeStore } from '../../features/home/state/homeStore';
import { queryClient } from '../../lib/queryClient';
import { EMPLOYEES_QUERY_KEY } from '../../features/home/data/employees';

const workflow = (id: string, name = `Workflow ${id}`) => ({
  id,
  name,
  slug: `${id}_1`,
  nodes: [],
  edges: [],
  createdAt: new Date(),
  lastModified: new Date(),
});

function setEditor({ current = 'a', unsaved = false, saveFails = false } = {}) {
  useAppStore.setState({
    currentWorkflow: workflow(current),
    hasUnsavedChanges: unsaved,
    shellMode: 'normal',
    loadWorkflow: vi.fn(async (id: string) => {
      events.push(`load:${id}`);
      useAppStore.setState({ currentWorkflow: workflow(id), hasUnsavedChanges: false });
    }),
    saveWorkflow: vi.fn(async () => {
      events.push('save');
      if (!saveFails) useAppStore.setState({ hasUnsavedChanges: false });
    }),
  });
}

beforeEach(() => {
  events.length = 0;
  toastError.mockReset();
  useWorkflowSettingsStore.setState({ settings: { ...defaultSettings } });
  useHomeStore.setState({ view: { kind: 'hire' }, workspaceOpen: false, workspaceFor: null });
  queryClient.removeQueries({ queryKey: EMPLOYEES_QUERY_KEY });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('enterDev', () => {
  it('uses the current Home employee when the caller supplies no ID', async () => {
    setEditor();
    useHomeStore.getState().showEmployee('b');
    await enterDev();
    expect(events).toContain('load:b');
    expect(useAppStore.getState().currentWorkflow?.id).toBe('b');
  });

  it('gives an explicit workflow priority over Home selection', async () => {
    setEditor();
    useHomeStore.getState().showEmployee('b');
    await enterDev({ workflowId: 'c' });
    expect(events).toContain('load:c');
    expect(events).not.toContain('load:b');
  });

  it.each([['b', 'b'], [null, 'c'], ['removed', 'c']])(
    'follows the visible Workspace from Hire (selection %s)', async (workspaceFor, expected) => {
      setEditor();
      useHomeStore.setState({ workspaceOpen: true, workspaceFor });
      queryClient.setQueryData(EMPLOYEES_QUERY_KEY, [{ workflow_id: 'c' }, { workflow_id: 'b' }]);
      await enterDev();
      expect(events).toContain(`load:${expected}`);
    },
  );

  it('does not change employees because of a closed Workspace', async () => {
    setEditor();
    useHomeStore.setState({ workspaceFor: 'b' });
    await enterDev();
    expect(events).toEqual(['preload', 'transition:dev']);
  });

  it('stays in Home when the loader resolves without loading the requested employee', async () => {
    setEditor();
    useAppStore.setState({ loadWorkflow: vi.fn(async () => {}) });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await enterDev({ workflowId: 'b' });
    expect(events).not.toContain('transition:dev');
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().currentWorkflow?.id).toBe('a');
  });

  it('warms the editor and switches, opening nothing new', async () => {
    setEditor();
    await enterDev();
    expect(events).toEqual(['preload', 'transition:dev']);
  });

  it('opens the requested workflow before switching', async () => {
    setEditor();
    await enterDev({ workflowId: 'b' });
    expect(events).toContain('load:b');
    expect(events.at(-1)).toBe('transition:dev');
    expect(useAppStore.getState().currentWorkflow?.id).toBe('b');
  });

  it('saves unsaved work before another workflow replaces it (auto-save on)', async () => {
    setEditor({ unsaved: true });
    await enterDev({ workflowId: 'b' });
    expect(events.indexOf('save')).toBeLessThan(events.indexOf('load:b'));
    expect(events.at(-1)).toBe('transition:dev');
  });

  it('asks first with auto-save off, and stays put when refused', async () => {
    useWorkflowSettingsStore.setState({ settings: { ...defaultSettings, autoSave: false } });
    setEditor({ unsaved: true });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await enterDev({ workflowId: 'b' });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(events).toEqual([]);
    expect(useAppStore.getState().currentWorkflow?.id).toBe('a');
  });

  it('saves and continues when the prompt is accepted', async () => {
    useWorkflowSettingsStore.setState({ settings: { ...defaultSettings, autoSave: false } });
    setEditor({ unsaved: true });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await enterDev({ workflowId: 'b' });
    expect(events[0]).toBe('save');
    expect(events.at(-1)).toBe('transition:dev');
  });

  it('keeps the editor on its workflow when the save fails', async () => {
    setEditor({ unsaved: true, saveFails: true });
    await enterDev({ workflowId: 'b' });
    expect(events).toEqual(['save']);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().currentWorkflow?.id).toBe('a');
  });

  it('does not save when reopening the workflow already in the editor', async () => {
    setEditor({ unsaved: true });
    await enterDev({ workflowId: 'a' });
    expect(events).toEqual(['preload', 'transition:dev']);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('reports a workflow that fails to open, without switching', async () => {
    setEditor();
    useAppStore.setState({
      loadWorkflow: vi.fn(async () => {
        throw new Error('offline');
      }),
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await enterDev({ workflowId: 'b' });
    expect(events).not.toContain('transition:dev');
    expect(toastError).toHaveBeenCalledTimes(1);
  });
});

describe('enterNormal and toggle', () => {
  it('keyboard switching opens the employee selected in Home', async () => {
    setEditor();
    useHomeStore.getState().showEmployee('b');
    await toggleShellMode();
    expect(events).toContain('load:b');
    expect(useAppStore.getState().currentWorkflow?.id).toBe('b');
  });

  it('switches to Normal, keeping unsaved editor work in the store', async () => {
    setEditor({ unsaved: true });
    useAppStore.setState({ shellMode: 'dev' });
    await enterNormal();
    expect(events).toEqual(['transition:normal']);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('toggles to the other screen', async () => {
    setEditor();
    useAppStore.setState({ shellMode: 'dev' });
    await toggleShellMode();
    expect(events).toEqual(['transition:normal']);
    events.length = 0;
    useAppStore.setState({ shellMode: 'normal' });
    await toggleShellMode();
    expect(events).toEqual(['preload', 'transition:dev']);
  });
});

/**
 * Switching between Normal mode (Home) and Dev mode (the workflow editor).
 * Every mode switch in the UI goes through these, never `setShellMode`:
 * they guard unsaved editor changes, preload the editor chunk, and run the
 * transition.
 *
 * Unsaved changes: the editor keeps its working copy in the app store, so
 * leaving for Home loses nothing. Opening a different workflow would replace
 * it, so `enterDev` settles it first (`unsavedWork.ts`).
 */

import { useMemo } from 'react';
import { toast } from 'sonner';
import { SPIKE, spikeOrb } from '../features/home/orb/orb';
import { useHomeStore } from '../features/home/state/homeStore';
import { EMPLOYEES_QUERY_KEY } from '../features/home/data/employees';
import type { EmployeeSummary } from '../features/home/data/schemas';
import { queryClient } from '../lib/queryClient';
import { featureFlags } from '../lib/featureFlags';
import { useAppStore } from '../store/useAppStore';
import { preloadEditor, preloadHome } from './ShellModeSwitch';
import { transitionShell } from './shellTransition';
import { settleUnsavedWork } from './unsavedWork';

export interface EnterDevOptions {
  /** Open this workflow in the editor (an employee's "Open workflow"). */
  workflowId?: string;
}

/** Match the employee Home actually displays, including its Workspace dock. */
function homeWorkflowId(): string | undefined {
  if (useAppStore.getState().shellMode !== 'normal') return undefined;
  const { view, workspaceOpen, workspaceFor } = useHomeStore.getState();
  if (view.kind === 'employee') return view.workflowId;
  if (!workspaceOpen) return undefined;
  const team = queryClient.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY);
  return team?.find((employee) => employee.workflow_id === workspaceFor)?.workflow_id
    ?? team?.[0]?.workflow_id ?? workspaceFor ?? undefined;
}

export async function enterDev(options: EnterDevOptions = {}): Promise<void> {
  if (!featureFlags.normalMode) return;
  const workflowId = options.workflowId ?? homeWorkflowId();
  if (workflowId && !(await settleUnsavedWork(workflowId))) return;
  const opening = workflowId && useAppStore.getState().currentWorkflow?.id !== workflowId
    ? useAppStore.getState().loadWorkflow(workflowId)
    : Promise.resolve();
  try {
    await Promise.all([opening, preloadEditor()]);
    // The store's loader also resolves on a null API response. Opening the
    // old employee in that case would make a failed selection look successful.
    if (workflowId && useAppStore.getState().currentWorkflow?.id !== workflowId) {
      throw new Error('The requested employee workflow did not load');
    }
  } catch (error) {
    console.error('[Shell] Could not open the editor:', error);
    toast.error('Could not open the workflow. Check the connection and try again.');
    return;
  }
  await transitionShell('dev');
}

export async function enterNormal(): Promise<void> {
  if (!featureFlags.normalMode) return;
  // Loaded before the swap, so Home's entrance never plays on a placeholder.
  try {
    await preloadHome();
  } catch (error) {
    console.error('[Shell] Could not load Home:', error);
  }
  await transitionShell('normal');
  // Home's orb flares as it arrives (it fades out within a second).
  spikeOrb(SPIKE.mode);
}

export function toggleShellMode(): Promise<void> {
  return useAppStore.getState().shellMode === 'dev' ? enterNormal() : enterDev();
}

export function useShellActions() {
  return useMemo(() => ({ enterDev, enterNormal, toggleShellMode }), []);
}

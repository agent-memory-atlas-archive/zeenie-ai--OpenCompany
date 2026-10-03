/**
 * The editor keeps its working copy of one workflow in the app store.
 * Everything that replaces it with another workflow (the mode switch, the
 * workflow sidebar, New) settles its unsaved changes first: they are saved
 * when the auto-save preference is on (the default) and asked about
 * otherwise.
 */

import { toast } from 'sonner';
import { useAppStore } from '../store/useAppStore';
import { useWorkflowSettingsStore } from '../stores/workflowSettingsStore';

/** Keep, save, or refuse the editor's unsaved work before another workflow
 *  replaces it. False means the replacement must not happen. Pass the
 *  workflow about to open; reopening the current one settles nothing. */
export async function settleUnsavedWork(nextWorkflowId?: string): Promise<boolean> {
  const { currentWorkflow, hasUnsavedChanges, saveWorkflow } = useAppStore.getState();
  if (!hasUnsavedChanges || !currentWorkflow || currentWorkflow.id === nextWorkflowId) return true;
  const autoSave = useWorkflowSettingsStore.getState().settings.autoSave;
  if (!autoSave && !window.confirm(`Save your changes to "${currentWorkflow.name}" before opening another workflow?`)) {
    return false;
  }
  let saved: boolean;
  try {
    saved = await saveWorkflow();
  } catch (error) {
    console.error('[Shell] Failed to save before switching workflows:', error);
    toast.error(`Could not save "${currentWorkflow.name}". Your changes are still open in the editor.`);
    return false;
  }
  // A save that did not happen has already told the user why.
  if (!saved) return false;
  // Edits made while the save was in flight are not in it.
  if (useAppStore.getState().hasUnsavedChanges) {
    toast.error(`"${currentWorkflow.name}" changed while it was saving. Save it again before opening another workflow.`);
    return false;
  }
  return true;
}

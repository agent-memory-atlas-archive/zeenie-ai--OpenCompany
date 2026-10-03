import { useCallback } from 'react';
import { useAppStore } from '../store/useAppStore';
import { settleUnsavedWork } from '../app/unsavedWork';
import { useWorkflowsQuery, type SavedWorkflow } from './useWorkflowsQuery';

export const useWorkflowManagement = () => {
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const hasUnsavedChanges = useAppStore((s) => s.hasUnsavedChanges);
  const updateWorkflow = useAppStore((s) => s.updateWorkflow);
  const saveWorkflow = useAppStore((s) => s.saveWorkflow);
  const loadWorkflow = useAppStore((s) => s.loadWorkflow);
  const createNewWorkflow = useAppStore((s) => s.createNewWorkflow);

  const { data: savedWorkflows = [] } = useWorkflowsQuery();

  const handleWorkflowNameChange = useCallback((name: string) => {
    updateWorkflow({ name });
  }, [updateWorkflow]);

  const handleSave = useCallback(() => {
    saveWorkflow();
  }, [saveWorkflow]);

  // New and opening another workflow replace the editor's working copy, so
  // its unsaved changes are settled first, as the mode switch does.
  const handleNew = useCallback(async () => {
    if (!(await settleUnsavedWork())) return;
    await createNewWorkflow();
  }, [createNewWorkflow]);

  const handleOpen = useCallback(() => {
    // Workflow selection is handled by sidebar
  }, []);

  const handleSelectWorkflow = useCallback(async (workflow: SavedWorkflow) => {
    // The editor already holds this workflow; reloading it would only
    // throw away its unsaved changes.
    if (useAppStore.getState().currentWorkflow?.id === workflow.id) return;
    if (!(await settleUnsavedWork(workflow.id))) return;
    await loadWorkflow(workflow.id);
  }, [loadWorkflow]);


  return {
    currentWorkflow,
    hasUnsavedChanges,
    savedWorkflows,
    handleWorkflowNameChange,
    handleSave,
    handleNew,
    handleOpen,
    handleSelectWorkflow,
  };
};

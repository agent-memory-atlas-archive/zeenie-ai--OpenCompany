/**
 * Home's side of talking to an employee. The conversation itself is the
 * shared chat (features/chat); this is what only an employee has:
 *
 * - `useEnableTalk()` / `useApplyChanges()`: Turn on Talk and Apply. Both
 *   restart the employee, so they wait as long as Start does, then refresh
 *   the team from the database.
 * - `useRetryNote(...)`: while the talk agent waits to retry after a failed
 *   attempt, what it said, for the chat's status line. It reads the agent's
 *   node status (`useNodeStatusStore` directly, like `useLiveTask`: the
 *   editor's hooks see only the workflow open in Dev mode).
 */

import { useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { WORKFLOW_CONTROL_REQUEST_TIMEOUT, useWebSocketActions } from '@/contexts/WebSocketContext';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { refreshEmployee } from './employees';

/** Both restart the employee on the server. Refresh the queries afterwards,
 *  including failures that may have changed the employee before failing.
 *  Every click sends its own idempotency key. Errors carry the server's code. */
function useEmployeeChange(type: 'enable_employee_talk' | 'apply_employee_changes') {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (workflowId) => {
      const response = await sendRequest<{ success?: boolean; error?: string }>(
        type,
        { workflow_id: workflowId, idempotency_key: crypto.randomUUID() },
        WORKFLOW_CONTROL_REQUEST_TIMEOUT,
      );
      if (response?.success === false) throw new Error(response.error || 'failed');
    },
    onSettled: (_data, _error, workflowId) => refreshEmployee(queryClient, workflowId),
  });
}

/** Adds a talk line to the employee and restarts it. */
export function useEnableTalk() {
  return useEmployeeChange('enable_employee_talk');
}

/** Restarts the employee on its latest saved graph. */
export function useApplyChanges() {
  return useEmployeeChange('apply_employee_changes');
}

/** "{why} Retrying automatically…" while the talk agent waits to try again;
 *  null otherwise, and whenever no run of theirs is going (`live`). */
export function useRetryNote(workflowId: string, agentNodeId: string | null, live: boolean): string | null {
  const status = useNodeStatusStore(
    useCallback(
      (state) => (agentNodeId ? state.allStatuses[workflowId]?.[agentNodeId] : undefined),
      [workflowId, agentNodeId],
    ),
  );
  if (!live || status?.status !== 'executing' || status.data?.phase !== 'retry_wait') return null;
  const message = status.data.retry_message;
  return typeof message === 'string' && message ? `${message} Retrying automatically…` : null;
}

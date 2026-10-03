/**
 * Home's side of drafts waiting for the owner: a toast when an employee
 * drafts something in its own work (`approval_lifecycle` "requested"), so the
 * owner hears about it whichever page is open. The drafts themselves, and
 * deciding them, live in the chat (features/chat/data/approvals.ts); a draft
 * made answering the owner in the chat (it names a chat run) shows there at
 * once, so it gets no toast.
 */

import { useEffect } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';
import { pillToast } from '../ui/pillToast';

interface ApprovalEnvelope {
  specversion?: string;
  type?: string;
  data?: { workflow_id?: string; approval_id?: string; run_id?: string } | null;
}

/** Apply one `approval_lifecycle` envelope. Exported for tests. */
export function applyApprovalLifecycle(queryClient: QueryClient, event: ApprovalEnvelope): void {
  if (!event || event.specversion !== '1.0') return;
  const workflowId = event.data?.workflow_id;
  if (!workflowId || !event.type?.endsWith('.requested') || event.data?.run_id) return;
  const employee = queryClient.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.find((item) => item.workflow_id === workflowId);
  pillToast(`${employee?.name ?? 'An employee'} has a draft for you to check`, { tone: 'info' });
}

export function useApprovalLifecycle(): void {
  const queryClient = useQueryClient();
  const { addEventListener } = useWebSocketActions();
  useEffect(() => addEventListener('approval_lifecycle', (data) => applyApprovalLifecycle(queryClient, data)), [addEventListener, queryClient]);
}

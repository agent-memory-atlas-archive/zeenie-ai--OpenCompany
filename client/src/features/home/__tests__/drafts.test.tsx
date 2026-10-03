/**
 * Home hears about drafts an employee made in its own work: a toast names
 * the employee; a draft made answering the owner in the chat (it names a
 * chat run) shows in the chat instead, so it gets none.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';

vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { applyApprovalLifecycle } from '../approvals/data';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { pillToast } from '../ui/pillToast';

beforeEach(() => {
  vi.mocked(pillToast).mockClear();
});

describe('applyApprovalLifecycle', () => {
  it('announces a new draft by the employee name, once', () => {
    const client = new QueryClient();
    client.setQueryData(EMPLOYEES_QUERY_KEY, [{ workflow_id: 'w1', name: 'Maya' }]);
    applyApprovalLifecycle(client, { specversion: '1.0', type: 'com.opencompany.approval.requested', data: { workflow_id: 'w1', approval_id: 'a1' } });
    expect(pillToast).toHaveBeenCalledWith('Maya has a draft for you to check', { tone: 'info' });
    applyApprovalLifecycle(client, { specversion: '1.0', type: 'com.opencompany.approval.decided', data: { workflow_id: 'w1' } });
    expect(pillToast).toHaveBeenCalledTimes(1);
  });

  it('leaves a draft made in the chat to the chat', () => {
    const client = new QueryClient();
    applyApprovalLifecycle(client, {
      specversion: '1.0',
      type: 'com.opencompany.approval.requested',
      data: { workflow_id: 'w1', approval_id: 'a1', run_id: 'r_1' },
    });
    applyApprovalLifecycle(client, { specversion: '0.3', type: 'com.opencompany.approval.requested', data: { workflow_id: 'w1' } });
    expect(pillToast).not.toHaveBeenCalled();
  });
});

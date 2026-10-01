import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ isReady: true, sendRequest, addEventListener: () => () => {} }),
}));

import { EMPLOYEES_QUERY_KEY, employeeDetailKey, useEmployeeDetailQuery, useEmployeesQuery } from '../data/employees';
import { parseEmployee, parseEmployeeDetail } from '../data/schemas';

let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  sendRequest.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchOnMount: false } } });
});

afterEach(() => {
  client.clear();
});

describe('returning to Home', () => {
  it('replaces a warm employee list with database state when deletion broadcasts were missed in Dev', async () => {
    let backendTeam = [
      { workflow_id: 'a', name: 'Maya' },
      { workflow_id: 'b', name: 'Theo' },
    ];
    client.setQueryData(EMPLOYEES_QUERY_KEY, backendTeam.map(parseEmployee));
    sendRequest.mockImplementation(async () => ({ success: true, employees: backendTeam }));

    const first = renderHook(() => useEmployeesQuery(), { wrapper });
    await waitFor(() => expect(first.result.current.isFetching).toBe(false));
    expect(sendRequest).toHaveBeenCalledTimes(1);
    first.unmount();

    backendTeam = backendTeam.filter((employee) => employee.workflow_id !== 'a');
    const second = renderHook(() => useEmployeesQuery(), { wrapper });
    await waitFor(() => expect(second.result.current.data?.map((employee) => employee.workflow_id)).toEqual(['b']));
    expect(sendRequest).toHaveBeenCalledTimes(2);
    expect(sendRequest).toHaveBeenLastCalledWith('list_employees', {});
    second.unmount();
  });

  it('rechecks a warm employee detail and clears it when the database reports deletion', async () => {
    const employee = { workflow_id: 'a', name: 'Maya' };
    client.setQueryData(employeeDetailKey('a'), parseEmployeeDetail(employee));
    sendRequest.mockResolvedValueOnce({ success: true, employee }).mockResolvedValueOnce({ success: false, error: 'not_found' });

    const first = renderHook(() => useEmployeeDetailQuery('a'), { wrapper });
    await waitFor(() => expect(first.result.current.isFetching).toBe(false));
    expect(first.result.current.data?.workflow_id).toBe('a');
    first.unmount();

    const second = renderHook(() => useEmployeeDetailQuery('a'), { wrapper });
    await waitFor(() => expect(second.result.current.data).toBeNull());
    expect(sendRequest).toHaveBeenCalledTimes(2);
    expect(sendRequest).toHaveBeenLastCalledWith('get_employee', { workflow_id: 'a' });
    second.unmount();
  });
});

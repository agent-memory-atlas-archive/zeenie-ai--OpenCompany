import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { WorkflowControlStatus } from '@/contexts/WebSocketContext';
import { emptyRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import { useChatRunStore } from '@/stores/chatRunStore';
import { useStopChatRun } from '../data/stop';

const actions = vi.hoisted(() => ({
  sendRequest: vi.fn(),
  getWorkflowControlStatus: vi.fn(),
  stopChatRun: vi.fn(),
}));
vi.mock('@/contexts/WebSocketContext', () => ({ useWebSocketActions: () => actions }));

const control = {
  workflow_id: 'workflow', revision: 3, execution_control_version: 1,
} as WorkflowControlStatus;

function mount(onFailed: () => void) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return renderHook(() => useStopChatRun('session', onFailed, control), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

describe('controlled chat Stop recovery', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useChatRunStore.setState({ sessions: {} });
  });

  it('does not report a Stop failure or discard partial output when Reset supersedes it', async () => {
    actions.stopChatRun.mockRejectedValue(new Error('workflow_control_superseded'));
    const run: RunSnapshot = {
      ...emptyRun('run', 'session'), workflowId: 'workflow', state: 'running',
      segments: [{ messageId: 'reply', text: 'Partial browser answer', final: null }],
    };
    useChatRunStore.getState().upsertRun(run);
    const before = useChatRunStore.getState().sessions.session.runs.run;
    const onFailed = vi.fn();
    const { result } = mount(onFailed);

    await act(async () => {
      await expect(result.current.mutateAsync(run)).rejects.toThrow('workflow_control_superseded');
    });

    expect(onFailed).not.toHaveBeenCalled();
    expect(useChatRunStore.getState().sessions.session.runs.run).toBe(before);
    expect(actions.sendRequest).not.toHaveBeenCalled();
  });

  it('still reports an actual Stop failure', async () => {
    actions.stopChatRun.mockRejectedValue(new Error('temporal_control_unavailable'));
    const onFailed = vi.fn();
    const { result } = mount(onFailed);
    const run = { ...emptyRun('run', 'session'), workflowId: 'workflow' };

    await act(async () => {
      await expect(result.current.mutateAsync(run)).rejects.toThrow('temporal_control_unavailable');
    });

    expect(onFailed).toHaveBeenCalledOnce();
  });
});

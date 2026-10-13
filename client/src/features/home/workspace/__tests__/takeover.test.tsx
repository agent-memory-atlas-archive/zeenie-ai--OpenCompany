import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  pillToast: vi.fn(),
}));
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => mocks,
}));
vi.mock('../../ui/pillToast', () => ({ pillToast: mocks.pillToast }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { parseEmployee } from '../../data/schemas';
import { useHomeStore } from '../../state/homeStore';
import { useTakeOver } from '../takeover';

beforeEach(() => {
  vi.clearAllMocks();
  useHomeStore.setState({ takeovers: {} });
  mocks.pauseWorkflow.mockResolvedValue(normalizeWorkflowControlStatus({
    generation: 1, state: 'paused', revision: 5,
  }, 'w1'));
});

describe('Hand back lifecycle recovery', () => {
  it.each(['workflow_control_superseded', 'transport'])(
    'releases the screen after Resume ends with %s', async (error) => {
      const employee = parseEmployee({
        workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: 'working',
        control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1'),
      })!;
      const giveBack = vi.fn();
      const screen = { claim: vi.fn(async () => giveBack) };
      mocks.resumeWorkflow.mockRejectedValue(new Error(error));
      const { result } = renderHook(() => useTakeOver(employee, () => screen));
      await act(async () => result.current.takeOver());
      expect(result.current.active).toBe(true);
      mocks.pillToast.mockClear();

      await act(async () => result.current.handBack());

      expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 4);
      expect(giveBack).toHaveBeenCalledOnce();
      expect(result.current).toMatchObject({ active: false, busy: false });
      expect(useHomeStore.getState().takeovers).toEqual({});
      if (error === 'workflow_control_superseded') {
        expect(mocks.pillToast).not.toHaveBeenCalled();
      } else {
        expect(mocks.pillToast).toHaveBeenCalledWith('That did not work. Try again.', { tone: 'error' });
      }
    },
  );
});

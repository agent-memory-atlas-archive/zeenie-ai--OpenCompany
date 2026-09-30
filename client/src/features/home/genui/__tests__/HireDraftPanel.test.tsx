/**
 * The draft panel end to end: the screen renders, a double-clicked Hire
 * sends one request, a finished hire clears the draft and opens the new
 * employee's page (with what the hire said, and the AI connect dialog when
 * there is no model), "Change something" hands the composer the draft,
 * changing when they work reaches the hire, the wait is shown honestly with
 * a way to cancel, and the layout JSON is for developers only.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { setReducedMotion } from '@/test/waapi';
import { useShellDialogsStore } from '@/stores/shellDialogsStore';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/WebSocketContext')>();
  return {
    ...actual,
    useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
  };
});

vi.mock('../../data/connectors', () => ({
  useConnectors: () => ({
    providers: [{ id: 'whatsapp', name: 'WhatsApp', consumer_category: 'messages', connected: false }],
    categories: [],
    connectedApps: [],
    hasAi: true,
    isLoading: false,
  }),
  isConnected: (provider: { connected?: boolean }) => Boolean(provider.connected),
}));

vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

import corpus from '../__fixtures__/replies.json';
import { EMPLOYEES_QUERY_KEY } from '../../data/employees';
import { useHomeStore } from '../../state/homeStore';
import { pillToast } from '../../ui/pillToast';
import { HireDraftPanel } from '../HireDraftPanel';
import { resetDraftForTests, useDraftStore } from '../draftStore';
import { normalizeSpec } from '../normalize';
import { parseReply } from '../parse';

const reply = (corpus as unknown as { name: string; reply: string }[]).find((c) => c.name === 'clean minified reply')!.reply;

function seedReadyDraft() {
  const parsed = parseReply(reply);
  const spec = normalizeSpec(parsed.spec)!;
  useDraftStore.setState({
    status: 'ready',
    job: 'Answer WhatsApp',
    turns: [{ change: null, reply }],
    spec,
    intro: parsed.text,
    uiState: spec.state,
    version: 1,
  });
}

function renderPanel(onConnect = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(EMPLOYEES_QUERY_KEY, []);
  render(
    <QueryClientProvider client={queryClient}>
      <HireDraftPanel onConnect={onConnect} />
    </QueryClientProvider>,
  );
  return { queryClient, onConnect };
}

function hired(patch: Record<string, unknown> = {}) {
  return {
    success: true,
    started: true,
    warnings: [],
    needs_ai: false,
    employee: { workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: 'working', control: {}, revision: 1 },
    ...patch,
  };
}

let restoreMotion: () => void;

beforeEach(() => {
  restoreMotion = setReducedMotion(true);
  sendRequest.mockReset();
  vi.mocked(pillToast).mockClear();
  resetDraftForTests();
  seedReadyDraft();
  useHomeStore.setState({ view: { kind: 'hire' }, hireNotice: null });
  useShellDialogsStore.setState({ credentialsOpen: false });
});

afterEach(() => {
  restoreMotion();
  resetDraftForTests();
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

describe('HireDraftPanel', () => {
  it('shows the introduction and the setup screen', () => {
    renderPanel();
    expect(screen.getByText('Meet Maya, your new receptionist.')).toBeInTheDocument();
    expect(screen.getByText('Their routine')).toBeInTheDocument();
    expect(screen.getByText('When something new arrives in WhatsApp')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Ask me before sending anything' })).toBeChecked();
  });

  it('sends one hire for a double click, then clears the draft and opens the employee', async () => {
    let finish!: (value: unknown) => void;
    sendRequest.mockImplementation((type: string) =>
      type === 'hire_employee' ? new Promise((resolve) => (finish = resolve)) : Promise.resolve({}),
    );
    const { queryClient } = renderPanel();
    const hire = screen.getByRole('button', { name: 'Hire Maya' });
    fireEvent.click(hire);
    fireEvent.click(hire);
    const hires = sendRequest.mock.calls.filter(([type]) => type === 'hire_employee');
    expect(hires).toHaveLength(1);
    expect(hires[0][1]).toMatchObject({ name: 'Maya', rules: { ask_first: true } });

    await act(async () => {
      finish(hired());
    });
    expect(useDraftStore.getState().status).toBe('idle');
    expect(queryClient.getQueryData<{ workflow_id: string }[]>(EMPLOYEES_QUERY_KEY)?.[0]?.workflow_id).toBe('w1');
    expect(useHomeStore.getState().glow?.workflowId).toBe('w1');
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w1' });
    expect(useHomeStore.getState().hireNotice).toBeNull();
  });

  it('keeps what the hire said for the new employee’s page, and asks for an AI model when there is none', async () => {
    const warnings = ['Google Calendar is left out while they ask before sending anything'];
    sendRequest.mockResolvedValue(hired({ started: false, needs_ai: true, warnings }));
    renderPanel();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Hire Maya' }));
    });
    const home = useHomeStore.getState();
    expect(home.view).toEqual({ kind: 'employee', workflowId: 'w1' });
    expect(home.hireNotice).toEqual({ workflowId: 'w1', name: 'Maya', warnings });
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
  });

  it('tells the owner in plain words when the same hire is still going through', async () => {
    sendRequest.mockResolvedValue({ success: false, error: 'busy' });
    renderPanel();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Hire Maya' }));
    });
    expect(pillToast).toHaveBeenCalledWith('They are still being set up. Give it a moment, then press Hire again.', {
      tone: 'error',
    });
    expect(useDraftStore.getState().hiring).toBe(false);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
  });

  it('hands the draft to the composer for a change', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Change something' }));
    expect(useDraftStore.getState().refining).toBe(true);
  });

  it('writes a toggle back into the screen state', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('switch', { name: 'Only reply 9 to 6' }));
    expect(useDraftStore.getState().uiState).toMatchObject({ rules: { hours: true } });
  });

  it('hires on the schedule the owner picked, and the routine follows it', async () => {
    sendRequest.mockResolvedValue(hired());
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('radio', { name: 'On a schedule' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Weekdays' }));
    // The sentence and the routine's first step both say it.
    expect(screen.getAllByText('Every weekday at 09:00')).toHaveLength(2);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Hire Maya' }));
    });
    const payload = sendRequest.mock.calls.find(([type]) => type === 'hire_employee')![1];
    expect(payload.trigger).toEqual({ kind: 'schedule', every: 'weekday', at: '09:00' });
    expect(payload.steps[0]).toEqual({ title: 'Every weekday at 09:00', detail: '', role: 'trigger' });
  });

  it('opens the guided AI connect dialog from a setup that found no AI model', () => {
    useDraftStore.setState({ status: 'failed', failure: { code: 'no_ai_provider', text: 'Answer WhatsApp', refine: false } });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Connect an AI model' }));
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
  });

  it('shows how long the setup has taken, and Cancel hands the words back', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    resetDraftForTests();
    useDraftStore.setState({
      status: 'working',
      job: 'Answer WhatsApp',
      token: 't1',
      request: { text: 'Answer WhatsApp', refine: false },
    });
    sendRequest.mockResolvedValue({ success: true });
    renderPanel();
    expect(screen.getByText('Writing their setup…')).toBeInTheDocument();
    expect(screen.getByRole('timer')).toHaveTextContent('0:00');
    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByRole('timer')).toHaveTextContent('1:05');
    expect(screen.getByText(/Some AI models take a few minutes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(sendRequest).toHaveBeenCalledWith('cancel_employee_setup', { draft_token: 't1' });
    expect(useDraftStore.getState()).toMatchObject({ status: 'idle', input: 'Answer WhatsApp' });
  });

  it('shows the layout JSON in development builds only', () => {
    vi.stubEnv('DEV', true);
    renderPanel();
    expect(screen.getByRole('button', { name: /Layout JSON/ })).toBeInTheDocument();
  });

  it('keeps the layout JSON out of a release build', () => {
    vi.stubEnv('DEV', false);
    renderPanel();
    expect(screen.queryByRole('button', { name: /Layout JSON/ })).not.toBeInTheDocument();
  });
});

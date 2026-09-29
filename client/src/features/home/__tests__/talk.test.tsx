/**
 * Talking to an employee on their page: the thread (every generation, with a
 * divider at each restart), sending (shown at once, taken back when the
 * server refuses), "Thinking…" until the answer lands, one message queued
 * while paused, suggestions, Turn on Talk, and Apply. The reply wait's
 * fallbacks are checked on the hook itself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { WORKFLOW_CONTROL_REQUEST_TIMEOUT, normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { presentEmployee } from '../data/presentation';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import {
  PICKUP_WAIT_MS,
  REPLY_WAIT_MS,
  SETTLE_WAIT_MS,
  THREAD_LIMIT,
  threadKey,
  useReplyWait,
  type ThreadMessage,
} from '../data/talk';
import { EmployeeTalk } from '../employee/EmployeeTalk';
import type { EmployeeControl } from '../employee/useEmployeeControl';
import { pillToast } from '../ui/pillToast';
// Loaded up front so the thread's lazy markdown resolves from the module cache.
import '../employee/ThreadMarkdown';

const AGENT = 'w1:talk';

function employee(patch: Record<string, unknown> = {}, state = 'running'): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    status: state === 'running' ? 'working' : state === 'paused' ? 'paused' : 'ready',
    talk: { state: 'on', agent_node_id: AGENT },
    control: normalizeWorkflowControlStatus({ generation: 1, revision: 4, state }, 'w1'),
    ...patch,
  })!;
}

function controlFor(summary: EmployeeSummary): EmployeeControl {
  const view = presentEmployee(summary);
  return { view, label: view.primary.kind === 'resume' ? 'Resume' : 'Start', busy: false, act: vi.fn() };
}

let server: { messages: Record<string, unknown>[]; send: Record<string, unknown> };
let client: QueryClient;

function renderTalk(summary: EmployeeSummary) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, [summary]);
  const view = (next: EmployeeSummary) => (
    <QueryClientProvider client={client}>
      <EmployeeTalk employee={next} control={controlFor(next)} />
    </QueryClientProvider>
  );
  const utils = render(view(summary));
  return { rerender: (next: EmployeeSummary) => utils.rerender(view(next)) };
}

function row(id: number, role: 'user' | 'assistant', message: string, runKey = 'g1') {
  return { id, role, message, timestamp: new Date(2026, 8, 28, 9, id).toISOString(), run_key: runKey };
}

/** What `chat.updated` does: the thread refetches. */
async function chatUpdated() {
  await act(async () => {
    await client.invalidateQueries({ queryKey: threadKey('w1') });
  });
}

function setAgentStatus(status: 'executing' | 'success') {
  act(() => useNodeStatusStore.getState().setStatus('w1', AGENT, { status }));
}

beforeEach(() => {
  useNodeStatusStore.setState({ allStatuses: {} });
  vi.mocked(pillToast).mockClear();
  server = { messages: [], send: { success: true, delivery: 'now' } };
  sendRequest.mockReset().mockImplementation(async (type: string, data: Record<string, unknown>) => {
    if (type === 'get_chat_messages') return { success: true, messages: server.messages };
    if (type === 'send_chat_message') {
      if (server.send.success !== false) server.messages = [...server.messages, row(server.messages.length + 1, 'user', String(data.message))];
      return server.send;
    }
    return { success: true };
  });
});

describe('the thread', () => {
  it('reads every generation and marks the restart', async () => {
    server.messages = [
      row(1, 'user', 'Morning!'),
      row(2, 'assistant', 'Hi! Two **bookings** today, see [the calendar](https://example.com/cal).'),
      row(3, 'user', 'Still there?', 'g2'),
    ];
    renderTalk(employee());
    expect(await screen.findByText('Still there?')).toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith('get_chat_messages', { session_id: 'w1', limit: THREAD_LIMIT, all_generations: true });
    expect(screen.getByText('Maya restarted — they start fresh from here')).toBeInTheDocument();
    // An answer is markdown, and its links leave the conversation open.
    expect(await screen.findByText('bookings', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'the calendar' })).toHaveAttribute('target', '_blank');
  });

  it('starts as just the message box', async () => {
    renderTalk(employee());
    expect(await screen.findByRole('textbox', { name: 'Message Maya' })).toHaveValue('');
    expect(screen.getByRole('log', { name: 'Conversation with Maya' })).toBeEmptyDOMElement();
    expect(screen.queryByRole('button', { name: 'What are you working on?' })).not.toBeInTheDocument();
  });
});

describe('sending', () => {
  it('shows the message at once and thinks until the answer lands', async () => {
    renderTalk(employee());
    const box = await screen.findByRole('textbox', { name: 'Message Maya' });
    fireEvent.change(box, { target: { value: '  Any bookings today?  ' } });
    fireEvent.keyDown(box, { key: 'Enter' });

    expect(box).toHaveValue('');
    expect(screen.getByText('Thinking…')).toBeInTheDocument();
    expect(await screen.findByText('Any bookings today?')).toBeInTheDocument();
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'send_chat_message',
        expect.objectContaining({ message: 'Any bookings today?', role: 'user', session_id: 'w1', timestamp: expect.any(String) }),
      ),
    );

    // The box holds while the answer is on its way.
    fireEvent.change(box, { target: { value: 'And tomorrow?' } });
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();

    setAgentStatus('executing');
    server.messages = [...server.messages, row(9, 'assistant', 'Two, at 10 and at 3.')];
    await chatUpdated();
    expect(await screen.findByText('Two, at 10 and at 3.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Thinking…')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
    expect(screen.getAllByText('Any bookings today?')).toHaveLength(1);
  });

  it('takes a refused message back and keeps the text', async () => {
    server.send = { success: false, error: 'not_running' };
    renderTalk(employee());
    const box = await screen.findByRole('textbox', { name: 'Message Maya' });
    fireEvent.change(box, { target: { value: 'Hello?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Maya isn’t running. Start them first.', { tone: 'error' }));
    expect(screen.getByRole('log', { name: 'Conversation with Maya' })).not.toHaveTextContent('Hello?');
    expect(box).toHaveValue('Hello?');
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument();
  });

  it('lets one message wait while the employee is paused', async () => {
    server.send = { success: true, delivery: 'queued' };
    const paused = employee({}, 'paused');
    const { rerender } = renderTalk(paused);
    expect(await screen.findByText('Maya is paused. They’ll read your message when you resume them.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();

    const box = screen.getByRole('textbox', { name: 'Message Maya' });
    fireEvent.change(box, { target: { value: 'Call me back' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(await screen.findByText('Your message is waiting. Maya will read it when you resume them.')).toBeInTheDocument();
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'One more' } });
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();

    // Resumed: the waiting message goes to them now.
    rerender(employee({}, 'running'));
    expect(await screen.findByText('Thinking…')).toBeInTheDocument();
  });

  it('gives the box to Start while the employee is not running', async () => {
    renderTalk(employee({}, 'never_started'));
    expect(await screen.findByText('Maya isn’t running, so they can’t read messages right now.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();
    // No box to fill, so no suggestions.
    expect(screen.queryByRole('button', { name: 'What are you working on?' })).not.toBeInTheDocument();
  });
});

describe('Turn on Talk', () => {
  it('confirms, mentioning the drafts a restart throws away, then turns Talk on', async () => {
    const user = userEvent.setup();
    const off = employee({ talk: { state: 'off', agent_node_id: null }, pending_approvals: 2 });
    sendRequest.mockImplementation(async (type: string) =>
      type === 'enable_employee_talk'
        ? { success: true, employee: { ...off, control: undefined, talk: { state: 'on', agent_node_id: AGENT }, revision: 9 } }
        : { success: true, messages: [] },
    );
    renderTalk(off);
    expect(sendRequest).not.toHaveBeenCalledWith('get_chat_messages', expect.anything());

    await user.click(screen.getByRole('button', { name: 'Turn on Talk' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('Maya has 2 drafts waiting for you. Restarting throws them away, so check them first.');
    await user.click(screen.getByRole('button', { name: 'Turn on Talk' }));

    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Talk is on. Say hello to Maya.'));
    const [, payload, timeout] = sendRequest.mock.calls.find(([type]) => type === 'enable_employee_talk')!;
    expect(payload).toEqual({ workflow_id: 'w1', idempotency_key: expect.any(String) });
    expect(timeout).toBe(WORKFLOW_CONTROL_REQUEST_TIMEOUT);
    expect(client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.[0]).toMatchObject({ revision: 9, talk: { state: 'on' } });
  });
});

describe('Apply', () => {
  it('restarts the employee on the latest setup', async () => {
    const summary = employee({ pending_changes: true });
    sendRequest.mockImplementation(async (type: string) =>
      type === 'apply_employee_changes'
        ? { success: true, employee: { ...summary, control: undefined, pending_changes: false, revision: 12 } }
        : { success: true, messages: [] },
    );
    renderTalk(summary);
    expect(await screen.findByText(/Maya has new abilities for this conversation\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Maya restarted with the new abilities.'));
    expect(sendRequest).toHaveBeenCalledWith(
      'apply_employee_changes',
      { workflow_id: 'w1', idempotency_key: expect.any(String) },
      WORKFLOW_CONTROL_REQUEST_TIMEOUT,
    );
    expect(client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.[0]).toMatchObject({ revision: 12, pending_changes: false });
  });

  it('keeps the employee it gets back when the restart fails', async () => {
    const summary = employee({ pending_changes: true });
    sendRequest.mockImplementation(async (type: string) =>
      type === 'apply_employee_changes'
        ? { success: false, error: 'restart_failed', employee: { ...summary, control: { state: 'ready' }, revision: 13 } }
        : { success: true, messages: [] },
    );
    renderTalk(summary);
    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Maya couldn’t restart. Try starting them again.', { tone: 'error' }));
    expect(client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.[0]).toMatchObject({ revision: 13 });
  });
});

describe('useReplyWait', () => {
  const reply: ThreadMessage = { id: '7', role: 'assistant', message: 'Done', timestamp: null, run_key: 'g1' };

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('gives up when the agent never picks the message up', () => {
    const { result } = renderHook(() => useReplyWait('w1', AGENT, []));
    act(() => result.current.begin());
    act(() => vi.advanceTimersByTime(PICKUP_WAIT_MS - 1));
    expect(result.current.waiting).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toMatchObject({ waiting: false, unanswered: true });
  });

  it('waits as long as the agent works, and ends with the answer', () => {
    let messages: ThreadMessage[] = [];
    const { result, rerender } = renderHook(() => useReplyWait('w1', AGENT, messages));
    act(() => result.current.begin());
    setAgentStatus('executing');
    act(() => vi.advanceTimersByTime(PICKUP_WAIT_MS * 2));
    expect(result.current.waiting).toBe(true);

    // Finished: the answer has a moment to reach the thread.
    setAgentStatus('success');
    act(() => vi.advanceTimersByTime(SETTLE_WAIT_MS - 1));
    expect(result.current.waiting).toBe(true);
    messages = [reply];
    rerender();
    expect(result.current).toMatchObject({ waiting: false, unanswered: false });
  });

  it('stops soon after the agent finishes without an answer', () => {
    const { result } = renderHook(() => useReplyWait('w1', AGENT, [reply]));
    act(() => result.current.begin());
    setAgentStatus('executing');
    setAgentStatus('success');
    act(() => vi.advanceTimersByTime(SETTLE_WAIT_MS));
    expect(result.current).toMatchObject({ waiting: false, unanswered: true });
  });

  it('never waits past the limit, and ignores the answer already there', () => {
    const { result } = renderHook(() => useReplyWait('w1', AGENT, [reply]));
    act(() => result.current.begin());
    setAgentStatus('executing');
    act(() => vi.advanceTimersByTime(REPLY_WAIT_MS - 1));
    expect(result.current.waiting).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.waiting).toBe(false);
  });
});

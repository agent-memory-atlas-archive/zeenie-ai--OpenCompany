/**
 * The employee page: the card's main button (Connect goes to the provider's
 * connect dialog, Pause sends the summary's revision and says "Pausing…"
 * until the summary shows the pause, never flashing "Pause" again in
 * between), Watch live, what the card leaves out when the page already says
 * it, and the conversation under the card, which offers the same main
 * action while the employee cannot read messages.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const actions = {
  isReady: true,
  sendRequest: vi.fn(),
  addEventListener: () => () => {},
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  startEmployee: vi.fn(),
};

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { enterDev } from '../../../app/useShellActions';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { EmployeeView } from '../employee/EmployeeView';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

function summary(patch: Record<string, unknown>, control: Record<string, unknown>): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    control: normalizeWorkflowControlStatus({ generation: 1, ...control }, 'w1'),
    ...patch,
  })!;
}

function renderCard(employee: EmployeeSummary, onConnect = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, [employee]);
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <EmployeeView workflowId="w1" onConnect={onConnect} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { client, onConnect };
}

beforeEach(() => {
  actions.sendRequest.mockReset();
  actions.pauseWorkflow.mockReset();
  actions.resumeWorkflow.mockReset();
  actions.startEmployee.mockReset();
  vi.mocked(enterDev).mockClear();
  vi.mocked(pillToast).mockClear();
});

describe('EmployeeView', () => {
  it('connects the missing app through its provider', () => {
    const whatsapp = { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: false, supported: true };
    const { onConnect } = renderCard(
      summary({ status: 'ready', apps: [whatsapp], missing_apps: [whatsapp] }, { state: 'never_started' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Connect WhatsApp' }));
    expect(onConnect).toHaveBeenCalledWith('whatsapp');
  });

  it('pauses with the summary’s revision and waits for the summary to catch up', async () => {
    let done!: (status: unknown) => void;
    actions.pauseWorkflow.mockReturnValue(new Promise((resolve) => (done = resolve)));
    const { client } = renderCard(summary({ status: 'working' }, { state: 'running', revision: 7 }));

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 7);
    expect(screen.getByRole('button', { name: 'Pausing…' })).toBeDisabled();

    await act(async () => done(normalizeWorkflowControlStatus({ state: 'paused', revision: 8, generation: 1 }, 'w1')));
    // The summary still says working: keep the in-flight label.
    expect(screen.getByRole('button', { name: 'Pausing…' })).toBeInTheDocument();

    await act(async () => {
      client.setQueryData(EMPLOYEES_QUERY_KEY, [summary({ status: 'paused' }, { state: 'paused', revision: 8 })]);
    });
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeEnabled();
  });

  it('says to connect an AI model, and opens that dialog, when Start is refused for want of one', async () => {
    useHomeStore.setState({ connectAIOpen: false });
    actions.startEmployee.mockRejectedValue(new Error('needs_ai'));
    renderCard(summary({ status: 'ready' }, { state: 'never_started' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Connect an AI model first.', { tone: 'error' }));
    expect(useHomeStore.getState().connectAIOpen).toBe(true);
  });

  it('opens the "Connect an AI model" dialog from the card', () => {
    useHomeStore.setState({ connectAIOpen: false });
    renderCard(summary({ status: 'ready', needs_ai: true }, { state: 'never_started' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect an AI model' })[0]);
    expect(useHomeStore.getState().connectAIOpen).toBe(true);
  });

  it('opens the Workspace on this employee', () => {
    useHomeStore.setState({ workspaceOpen: false, workspaceFor: null });
    renderCard(summary({ status: 'working' }, { state: 'running' }));
    fireEvent.click(screen.getByRole('button', { name: 'Watch live' }));
    expect(useHomeStore.getState()).toMatchObject({ workspaceOpen: true, workspaceFor: 'w1' });
  });

  it('says only what the page does not already say', () => {
    // Waiting for the owner's messages (no task line), a role that repeats
    // the name, nothing done yet: none of it shows.
    renderCard(summary({ status: 'working', role: 'maya', task: null, done_today: 0, apps: [] }, { state: 'running' }));
    expect(screen.queryByText('maya')).not.toBeInTheDocument();
    expect(screen.queryByText(/done today/)).not.toBeInTheDocument();
    expect(screen.queryByText('Now')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More' })).not.toBeInTheDocument();
  });

  it('shows the role, the task and the count once they say something', () => {
    renderCard(summary({ status: 'working', task: { label: 'Now', text: 'Waiting for new WhatsApp messages' }, done_today: 2 }, { state: 'running' }));
    expect(screen.getByText('Receptionist')).toBeInTheDocument();
    expect(screen.getByText('Waiting for new WhatsApp messages')).toBeInTheDocument();
    expect(screen.getByText(/done today/)).toHaveTextContent('2 done today');
  });

  it('makes Open in Dev mode the main button when nothing else is possible', () => {
    renderCard(summary({ status: 'attention' }, { state: 'failed', can_resume: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Open in Dev mode' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: 'w1' });
  });

  it('offers Start under the conversation while the employee is not running', async () => {
    actions.sendRequest.mockResolvedValue({ success: true, messages: [] });
    actions.startEmployee.mockReturnValue(new Promise(() => {}));
    renderCard(summary({ status: 'ready', talk: { state: 'on', agent_node_id: 'w1:talk' } }, { state: 'never_started', revision: 3 }));
    const talk = await screen.findByRole('region', { name: 'Talk with Maya' });
    expect(talk).toHaveTextContent('Maya isn’t running, so they can’t read messages right now.');
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();

    const starts = screen.getAllByRole('button', { name: 'Start' });
    expect(starts).toHaveLength(2);
    fireEvent.click(starts[1]);
    expect(actions.startEmployee).toHaveBeenCalledWith('w1', 3);
    // One control for the page: both buttons follow the same request.
    expect(screen.getAllByRole('button', { name: 'Starting…' })).toHaveLength(2);
  });

  it('notes when the employee cannot take messages', () => {
    renderCard(summary({ status: 'working' }, { state: 'running' }));
    expect(screen.getByText('You can’t message Maya here. Their setup has no way to answer you.')).toBeInTheDocument();
    expect(actions.sendRequest).not.toHaveBeenCalledWith('get_chat_messages', expect.anything());
  });

  it('says so when the employee is gone', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(EMPLOYEES_QUERY_KEY, []);
    actions.sendRequest.mockResolvedValue({ success: false, error: 'not_found' });
    render(
      <QueryClientProvider client={client}>
        <EmployeeView workflowId="gone" onConnect={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('This employee is no longer on the team.')).toBeInTheDocument();
  });
});

/**
 * The Home header: on an employee's page it says who they are (name, role
 * and apps, status) and offers New conversation, which asks first; the
 * Normal/Dev switch opens that employee's workflow there, and elsewhere
 * what the editor last had.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn(), enterNormal: vi.fn() }));
vi.mock('../../../app/ShellModeSwitch', () => ({ useShellMode: () => 'normal' }));
vi.mock('../workspace/WorkspaceButton', () => ({ WorkspaceButton: () => null }));
vi.mock('../header/ThemeButton', () => ({ ThemeButton: () => null }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { enterDev } from '../../../app/useShellActions';
import { parseEmployee } from '../data/schemas';
import { HomeHeader } from '../header/HomeHeader';
import { useHomeStore } from '../state/homeStore';

const maya = parseEmployee({
  workflow_id: 'w1',
  name: 'Maya',
  role: 'Receptionist',
  status: 'working',
  talk: { state: 'on', agent_node_id: 'w1:talk' },
  apps: [
    { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: true },
    { app_id: 'calendar', provider_id: 'google', name: 'Google Calendar', connected: true },
  ],
  control: normalizeWorkflowControlStatus({ generation: 1, revision: 2, state: 'running' }, 'w1'),
})!;

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
}

describe('HomeHeader', () => {
  beforeEach(() => {
    vi.mocked(enterDev).mockClear();
    sendRequest.mockReset().mockResolvedValue({ success: true });
    useHomeStore.setState({ sidebarOpen: true });
  });

  it('says who is on screen', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    expect(screen.getByRole('heading', { name: 'Maya' })).toBeInTheDocument();
    expect(screen.getByText('Receptionist · WhatsApp, Google Calendar')).toBeInTheDocument();
    expect(screen.getByText('Working')).toBeInTheDocument();
  });

  it('starts a new conversation once the owner confirms', async () => {
    const user = userEvent.setup();
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent('This clears the conversation, and Maya forgets it too.');
    expect(sendRequest).not.toHaveBeenCalledWith('clear_chat_messages', expect.anything());
    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('clear_chat_messages', { session_id: 'w1' }));
  });

  it('offers no new conversation without a talk line', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={{ ...maya, talk: { state: 'off', agent_node_id: null } }} scrolled={false} />);
    expect(screen.queryByRole('button', { name: 'New conversation' })).not.toBeInTheDocument();
  });

  it('opens the employee on screen in Dev mode', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: 'w1' });
  });

  it('opens what the editor last had from the hire view', () => {
    useHomeStore.setState({ view: { kind: 'hire' } });
    wrap(<HomeHeader title="New employee" employee={null} scrolled={false} />);
    expect(screen.getByRole('heading', { name: 'New employee' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: undefined });
  });
});

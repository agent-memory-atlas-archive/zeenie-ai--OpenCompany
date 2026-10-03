/**
 * The shared chat against a fake server: it follows the session's runs
 * (subscribing while mounted), shows a message at once and the employee
 * working until the run ends, holds the next message until then, says why a
 * run failed, waits for Resume, and puts a message that did not go back in
 * the box, where it survives leaving the conversation.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

import { resetChatRunStore, useChatRunStore } from '@/stores/chatRunStore';
import { ChatPane } from '../ChatPane';
import type { ChatHost } from '../host';
import { useComposerStore } from '../state/composerStore';
// Loaded up front so the turns' lazy markdown resolves from the module cache.
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;
let server: { messages: Wire[]; send: Wire; activeRuns: Wire[] };
let client: QueryClient;

function row(id: string, role: 'user' | 'assistant', text: string, patch: Wire = {}): Wire {
  return { id, role, text, message: text, timestamp: '2026-10-03T09:00:00Z', run_key: 'g1', ...patch };
}

function frame(seq: number, suffix: string, data: Wire = {}, runId = 'r1') {
  return {
    specversion: '1.0',
    id: `${runId}:${seq}`,
    source: 'opencompany://services/chat',
    type: `com.opencompany.chat.run.${suffix}`,
    subject: runId,
    data: { workflow_id: 'w1', session_id: 'w1', run_id: runId, seq, hub_epoch: 'e1', ...data },
  };
}

function runEvents(...frames: unknown[]) {
  act(() => {
    const store = useChatRunStore.getState();
    for (const item of frames) store.receive(item);
    store.flush();
  });
}

async function threadUpdated() {
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['chatThread'] });
  });
}

function host(patch: Partial<ChatHost> = {}): ChatHost {
  return {
    kind: 'home',
    sessionId: 'w1',
    scope: 'all',
    persona: { name: 'Maya', colorRole: 'agent' },
    composer: 'send',
    notify: vi.fn(),
    ...patch,
  };
}

function renderPane(chat: ChatHost = host()) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (next: ChatHost) => (
    <QueryClientProvider client={client}>
      <ChatPane host={next} />
    </QueryClientProvider>
  );
  const utils = render(view(chat));
  return { ...utils, rerender: (next: ChatHost) => utils.rerender(view(next)) };
}

function box() {
  return screen.getByRole('textbox', { name: 'Message Maya' });
}

function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
}

/** Write a message once the conversation has loaded (until then it cannot go). */
async function write(text: string) {
  type(text);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
}

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  server = { messages: [], send: { success: true, message_id: 'm1', run_id: 'r1', delivery: 'now' }, activeRuns: [] };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: server.activeRuns };
      case 'get_chat_messages':
        return { success: true, messages: server.messages, thread: { active_leaf_id: null, revision: 1 }, active_runs: server.activeRuns };
      case 'send_chat_message':
        if (server.send.success !== false) {
          server.messages = [...server.messages, row(String(server.send.message_id), 'user', String(data.message), {
            run_id: server.send.run_id,
            client_message_id: data.client_message_id,
          })];
        }
        return server.send;
      default:
        return { success: true };
    }
  });
});

describe('ChatPane', () => {
  it('follows the session while it is open and reads the thread for its scope', async () => {
    const { unmount } = renderPane(host({ scope: 'live' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('chat_subscribe', { session_id: 'w1' }));
    expect(sendRequest).toHaveBeenCalledWith('get_chat_messages', { session_id: 'w1', limit: 200, all_generations: false });
    await waitFor(() => expect(useChatRunStore.getState().sessions.w1?.subscribed).toBe(true));
    unmount();
    expect(sendRequest).toHaveBeenCalledWith('chat_unsubscribe', { session_id: 'w1' });
  });

  it('shows a message at once and the employee working until the run ends', async () => {
    renderPane();
    await waitFor(() => expect(useChatRunStore.getState().sessions.w1?.subscribed).toBe(true));
    await write('  Any bookings today?  ');
    fireEvent.keyDown(box(), { key: 'Enter' });

    expect(box()).toHaveValue('');
    expect(await screen.findByText('Any bookings today?')).toBeInTheDocument();
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'send_chat_message',
        expect.objectContaining({ message: 'Any bookings today?', session_id: 'w1', client_message_id: expect.any(String) }),
      ),
    );
    expect(await screen.findByText('Working…')).toBeInTheDocument();
    // The next message waits for this answer.
    type('And tomorrow?');
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();

    runEvents(frame(1, 'started', { kind: 'message', user_message_id: 'm1' }));
    expect(screen.getByText('Working…')).toBeInTheDocument();
    server.messages = [...server.messages, row('a_r1', 'assistant', 'Two, at **10** and at 3.', { run_id: 'r1' })];
    runEvents(frame(2, 'finished', { outcome: { type: 'success' }, result: { reply_message_id: 'a_r1' } }));
    await threadUpdated();

    expect(await screen.findByText('10', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
    expect(screen.getAllByText('Any bookings today?')).toHaveLength(1);
  });

  it('says why a run failed, with what to do about it', async () => {
    server.messages = [row('m1', 'user', 'Book Priya in', { run_id: 'r1' })];
    server.activeRuns = [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }];
    renderPane();
    expect(await screen.findByText('Working…')).toBeInTheDocument();
    runEvents(frame(2, 'failed', { message: 'Calendar said no', code: 'run_failed', hint: 'Reconnect Google' }));
    expect(screen.getByText('Maya couldn’t answer.')).toBeInTheDocument();
    expect(screen.getByText('Calendar said no')).toBeInTheDocument();
    expect(screen.getByText('Reconnect Google')).toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();
  });

  it('shows how an earlier run ended after a reload', async () => {
    server.messages = [
      row('m1', 'user', 'Anyone there?', {
        run_id: 'r1',
        run: { run_id: 'r1', state: 'error', outcome: null, error: { message: 'x', code: 'not_delivered' } },
      }),
    ];
    renderPane();
    expect(await screen.findByText('Maya didn’t pick up this message.')).toBeInTheDocument();
  });

  it('waits for Resume when the message was queued', async () => {
    server.send = { success: true, message_id: 'm1', run_id: 'r1', delivery: 'queued' };
    renderPane(host({ composer: 'queue' }));
    await write('Call me back');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(await screen.findByText('Waiting for you to resume Maya.')).toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();

    runEvents(frame(1, 'started', { kind: 'message' }));
    expect(await screen.findByText('Working…')).toBeInTheDocument();
  });

  it('puts a message that did not go back in the box, and lets the host say why', async () => {
    server.send = { success: false, error: 'not_running' };
    const onSendRefused = vi.fn();
    renderPane(host({ onSendRefused }));
    await write('Hello?');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(onSendRefused).toHaveBeenCalledWith('not_running'));
    expect(box()).toHaveValue('Hello?');
    expect(screen.getByRole('log', { name: 'Conversation with Maya' })).not.toHaveTextContent('Hello?');
  });

  it('tells the owner a send failed when the host does not', async () => {
    sendRequest.mockImplementation(async (kind: string) => {
      if (kind === 'send_chat_message') throw new Error('socket closed');
      if (kind === 'chat_subscribe') return { success: true, hub_epoch: 'e1', active_runs: [] };
      return { success: true, messages: [] };
    });
    const chat = host();
    renderPane(chat);
    await write('Hello?');
    fireEvent.keyDown(box(), { key: 'Enter' });
    await waitFor(() => expect(chat.notify).toHaveBeenCalledWith('Your message didn’t send. Try again.', 'error'));
    // It may have reached the server: sending it again is the same message.
    expect(useComposerStore.getState().drafts.w1).toEqual({ text: 'Hello?', clientMessageId: expect.any(String) });
  });

  it('has no message box while nothing can read messages', async () => {
    renderPane(host({ composer: 'closed', notices: <p>Start Maya first.</p> }));
    expect(await screen.findByText('Start Maya first.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('keeps an unsent message when the conversation is opened again', async () => {
    const { unmount } = renderPane();
    type('Half a thought');
    unmount();
    renderPane();
    expect(box()).toHaveValue('Half a thought');
  });
});

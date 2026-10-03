/**
 * Tests for the ConsolePanel chat-focus effect.
 *
 * Locks the handoff contract: when useAppStore.chatFocusRequest increments
 * while the panel is open, the chat input is focused on the next animation
 * frame. A chatFocusRequest of 0 (initial) or a closed panel never focuses.
 */

import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Full-module replace of the WS context (importActual+spread is broken under
// React 19 — see CredentialsModal.test.tsx). ConsolePanel destructures:
// consoleLogs, clearConsoleLogs, terminalLogs, clearTerminalLogs; the Chat
// pane (the shared chat) reads its thread and runs through
// useWebSocketActions. lib/nodeSpec (imported transitively) also pulls
// useWebSocket but only calls it inside hooks that this test never mounts.
const wsMock = {
  consoleLogs: [] as unknown[],
  terminalLogs: [] as unknown[],
  clearConsoleLogs: vi.fn(),
  clearTerminalLogs: vi.fn(),
};
const actionsMock = {
  isReady: true,
  sendRequest: vi.fn(async (type: string) =>
    type === 'chat_subscribe' ? { success: true, hub_epoch: 'e1', active_runs: [] } : { success: true, messages: [] },
  ),
  addEventListener: () => () => {},
};

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
  useWebSocketActions: () => actionsMock,
}));

import ConsolePanel from '../ui/ConsolePanel';
import { useAppStore } from '../../store/useAppStore';

// --- jsdom shims -------------------------------------------------------------

beforeAll(() => {
  // jsdom has no scrollIntoView; ConsolePanel's auto-scroll effects call it.
  Element.prototype.scrollIntoView = vi.fn();

  // Vitest's jsdom env normally provides rAF (pretendToBeVisual); polyfill
  // defensively so the flush helper below always works.
  if (typeof globalThis.requestAnimationFrame === 'undefined') {
    // `window.setTimeout`, not the bare global: with @types/node in scope the
    // bare one is typed `NodeJS.Timeout`, which does not convert to the
    // `number` that rAF must return (TS2352 under tsc 5.x).
    globalThis.requestAnimationFrame = (cb: FrameRequestCallback) =>
      window.setTimeout(() => cb(performance.now()), 0);
    globalThis.cancelAnimationFrame = (id: number) => window.clearTimeout(id);
  }
});

/** Resolve after the next animation frame — any rAF scheduled before this
 *  call has already run by the time it resolves. */
const flushAnimationFrame = () =>
  new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ chatFocusRequest: 0 });
});

const renderPanel = (isOpen: boolean) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConsolePanel isOpen={isOpen} onToggle={vi.fn()} nodes={[]} />
    </QueryClientProvider>,
  );

/** The Chat pane's message box (no workflow open: it talks to "Workflow"). */
const chatBox = () => screen.getByRole('textbox', { name: 'Message Workflow' });

// ---------------------------------------------------------------------------

describe('ConsolePanel chat focus', () => {
  it('does not focus the chat input on mount (chatFocusRequest is 0)', async () => {
    renderPanel(true);
    await act(async () => {
      await flushAnimationFrame();
    });
    const input = chatBox();
    expect(input).not.toHaveFocus();
  });

  it('focuses the chat input when chatFocusRequest increments while open', async () => {
    renderPanel(true);
    const input = chatBox();
    expect(input).not.toHaveFocus();

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });

    expect(input).toHaveFocus();
  });

  it('focuses again on a subsequent increment after focus moved elsewhere', async () => {
    renderPanel(true);
    const input = chatBox();

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });
    expect(input).toHaveFocus();

    act(() => {
      (input as HTMLTextAreaElement).blur();
    });
    expect(input).not.toHaveFocus();

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });
    expect(input).toHaveFocus();
  });

  it('does not focus when the panel is closed', async () => {
    renderPanel(false);
    const input = chatBox();

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });

    expect(input).not.toHaveFocus();
  });
});

// ---------------------------------------------------------------------------
// Tab mode (design-handoff hybrid): splitView=false makes Chat the first
// tab. A focus request must first switch to the Chat tab (the input does
// not exist in the DOM otherwise), then focus the input.
// ---------------------------------------------------------------------------

describe('ConsolePanel chat focus (tab mode)', () => {
  const PREFS_KEY = 'console_panel_prefs_v1';

  beforeEach(() => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ splitView: false }));
  });

  afterEach(() => {
    localStorage.removeItem(PREFS_KEY);
  });

  it('renders Chat as a tab and no chat input while another tab is active', () => {
    renderPanel(true);
    expect(screen.getByRole('button', { name: 'Chat' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Workflow' })).not.toBeInTheDocument();
  });

  it('switches to the Chat tab and focuses the input on a focus request', async () => {
    renderPanel(true);

    act(() => {
      useAppStore.getState().requestChatFocus();
    });
    await act(async () => {
      await flushAnimationFrame();
    });

    const input = chatBox();
    expect(input).toHaveFocus();
  });
});

/**
 * useCredentialPanel's `stored` follows the server: removing a key leaves the
 * panel disconnected (no Connected badge, no Valid button, no status line)
 * instead of flipping back from the pre-removal cache entry.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
const removeApiKey = vi.fn();

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sendRequest }),
}));

vi.mock('../../../hooks/useApiKeys', () => ({
  useApiKeys: () => ({
    validateApiKey: vi.fn(),
    saveApiKey: vi.fn(),
    removeApiKey,
    getProviderDefaults: vi.fn(),
    saveProviderDefaults: vi.fn(),
    getProviderUsageSummary: vi.fn(),
    getAPIUsageSummary: vi.fn(),
    getStoredModels: vi.fn(),
    getModelConstraints: vi.fn(),
    isConnected: true,
  }),
}));

import { useCredentialPanel } from '../useCredentialPanel';
import type { ProviderConfig } from '../types';

const CONFIG: ProviderConfig = {
  id: 'opencompany_gateway',
  name: 'Gateway',
  category: 'ai',
  categoryLabel: 'AI models',
  color: '',
  kind: 'apiKey',
  iconRef: 'lucide:Sparkles',
  fields: [{ key: 'apiKey', label: 'Gateway key', secret: true }],
  ws: { login: '', logout: '', status: 'gateway_status' },
};

let client: QueryClient;
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

describe('useCredentialPanel', () => {
  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    sendRequest.mockReset();
    removeApiKey.mockReset();
  });

  it('stays disconnected after its key is removed', async () => {
    let hasKey = true;
    // The post-removal refetch waits until released, so the panel renders in
    // between, as it does over a real socket.
    let release: () => void = () => {};
    let gate: Promise<void> | null = null;
    sendRequest.mockImplementation(async (type: string) => {
      if (type === 'get_stored_api_key') {
        if (gate) await gate;
        return { hasKey, apiKey: hasKey ? 'sk-gateway' : undefined };
      }
      if (type === 'gateway_status') return { summary: '$0.00 of $50.00 used' };
      return {};
    });
    removeApiKey.mockImplementation(async () => {
      hasKey = false;
      gate = new Promise((resolve) => {
        release = resolve;
      });
    });

    const { result } = renderHook(() => useCredentialPanel(CONFIG, true), { wrapper });
    await waitFor(() => expect(result.current.stored).toBe(true));
    await waitFor(() => expect(result.current.statusSummary).toBe('$0.00 of $50.00 used'));

    let removal: Promise<unknown> = Promise.resolve();
    act(() => {
      removal = result.current.actions.remove('opencompany_gateway');
    });
    await waitFor(() => expect(removeApiKey).toHaveBeenCalledWith('opencompany_gateway'));
    await act(async () => {
      release();
      await removal;
    });

    expect(result.current.stored).toBe(false);
  });
});

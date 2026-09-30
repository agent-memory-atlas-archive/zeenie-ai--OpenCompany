import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ThemeProvider } from '@/contexts/ThemeContext';
import type { CredentialsCatalogue, ConsumerProvider } from '../catalogue';

const sendRequest = vi.fn();
const useCatalogue = vi.fn();
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => ({ sendRequest, isReady: true }),
}));
vi.mock('../catalogue', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../catalogue')>()),
  useCredentialsCatalogue: () => useCatalogue(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { CredentialsBrowser } from '../CredentialsBrowser';

const ENDPOINT: ConsumerProvider = {
  id: 'openai_compatible', name: 'Model server', category: 'ai', category_label: 'AI',
  consumer_category: 'ai', color: '', kind: 'apiKey', connected: true,
  endpoints: [{ ref: 'openai_compatible:lab', label: 'Lab', base_url: 'http://gpu:8000', kind: 'generic', model_count: 1 }],
};

function catalogue(providers: ConsumerProvider[] = [ENDPOINT]): CredentialsCatalogue {
  return {
    catalogue: { data: { providers } }, providers, categories: [],
    isLoading: false, isError: false, error: null, refetch: vi.fn(),
  } as unknown as CredentialsCatalogue;
}

beforeEach(() => {
  sendRequest.mockReset().mockResolvedValue({ success: true });
  useCatalogue.mockReset();
});

describe('CredentialsBrowser', () => {
  it('uses its host catalogue without fetching another copy and can manage in Yours', () => {
    const onConnect = vi.fn();
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue()} onConnect={onConnect} /></ThemeProvider>);
    expect(useCatalogue).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('radio', { name: /Yours/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Manage Model server' }));
    expect(onConnect).toHaveBeenCalledWith('openai_compatible', 'manage');
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('hands named-endpoint removal to the panel instead of deleting the family id', async () => {
    const onConnect = vi.fn();
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue()} onConnect={onConnect} /></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect Model server' }));
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith('openai_compatible', 'manage'));
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('offers a retry when no catalogue could be loaded', () => {
    const state = catalogue([]);
    state.catalogue = { ...state.catalogue, data: undefined } as CredentialsCatalogue['catalogue'];
    state.isError = true;
    render(<ThemeProvider><CredentialsBrowser catalogue={state} onConnect={vi.fn()} /></ThemeProvider>);
    expect(screen.getByText("Couldn't load connectors")).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(state.refetch).toHaveBeenCalledTimes(1);
  });
});

/**
 * The guided "Connect an AI model" dialog: the featured providers with
 * their hint and whether each is connected; picking one opens its own panel
 * with the link to where its key is made; the dialog closes once that
 * provider connects; and every other model is one click away in Settings.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { CatalogueResponse, ServerProviderConfig } from '@/hooks/useCatalogueQuery';

let catalogue: CatalogueResponse | undefined;

vi.mock('../data/connectors', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../data/connectors')>()),
  useHomeCatalogue: () => ({ data: catalogue, isLoading: !catalogue }),
}));
vi.mock('@/components/credentials/PanelRenderer', () => ({
  default: ({ config }: { config: { name: string } | null }) => <div>Panel for {config?.name}</div>,
}));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { FEATURED_AI_PROVIDERS } from '@/components/onboarding/aiProviderLinks';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { ConnectAIDialog } from '../connectAI/ConnectAIDialog';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

function provider(id: string, name: string, connected = false): ServerProviderConfig {
  return {
    id,
    name,
    category: 'ai',
    category_label: 'AI',
    color: '',
    kind: 'apiKey',
    consumer_category: 'ai',
    stored: connected,
    connected,
  };
}

function withProviders(...providers: ServerProviderConfig[]): CatalogueResponse {
  return { providers, categories: [], version: 'v1' };
}

// A fresh element each time, so a rerender reads the catalogue again.
const ui = () => (
  <ThemeProvider>
    <ConnectAIDialog />
  </ThemeProvider>
);

beforeEach(() => {
  vi.mocked(pillToast).mockClear();
  catalogue = withProviders(provider('openai', 'OpenAI'), provider('anthropic', 'Anthropic', true), provider('gemini', 'Gemini'));
  useHomeStore.setState({ connectAIOpen: true, settingsOpen: false });
});

describe('ConnectAIDialog', () => {
  it('lists the featured providers with a hint, and which are connected', () => {
    render(ui());
    expect(screen.getByRole('dialog', { name: 'Connect an AI model' })).toBeInTheDocument();
    for (const entry of FEATURED_AI_PROVIDERS) expect(screen.getByText(entry.hint)).toBeInTheDocument();
    const anthropic = screen.getByRole('button', { name: /Anthropic/ });
    expect(anthropic).toHaveTextContent('Connected');
    expect(screen.getByRole('button', { name: /OpenAI/ })).not.toHaveTextContent('Connected');
  });

  it('opens a provider’s own panel with the page where its key is made, and goes back', () => {
    render(ui());
    fireEvent.click(screen.getByRole('button', { name: /OpenAI/ }));
    expect(screen.getByText('Panel for OpenAI')).toBeInTheDocument();
    const openai = FEATURED_AI_PROVIDERS.find((entry) => entry.id === 'openai')!;
    expect(screen.getByRole('link', { name: /Get one from OpenAI/ })).toHaveAttribute('href', openai.keyUrl);
    fireEvent.click(screen.getByRole('button', { name: 'All AI models' }));
    expect(screen.queryByText('Panel for OpenAI')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Gemini/ })).toBeInTheDocument();
  });

  it('closes once the picked provider connects', () => {
    const { rerender } = render(ui());
    fireEvent.click(screen.getByRole('button', { name: /OpenAI/ }));
    expect(pillToast).not.toHaveBeenCalled();
    catalogue = withProviders(provider('openai', 'OpenAI', true), provider('anthropic', 'Anthropic', true));
    rerender(ui());
    expect(pillToast).toHaveBeenCalledWith('OpenAI is connected');
    expect(useHomeStore.getState().connectAIOpen).toBe(false);
  });

  it('stays open on a provider that was connected already', () => {
    const { rerender } = render(ui());
    fireEvent.click(screen.getByRole('button', { name: /Anthropic/ }));
    rerender(ui());
    expect(useHomeStore.getState().connectAIOpen).toBe(true);
    expect(pillToast).not.toHaveBeenCalled();
  });

  it('sends the owner to every AI model in Settings', () => {
    render(ui());
    fireEvent.click(screen.getByRole('button', { name: 'See all AI models' }));
    const home = useHomeStore.getState();
    expect(home.connectAIOpen).toBe(false);
    expect(home).toMatchObject({ settingsOpen: true, settingsTab: 'connectors', settingsCategory: 'ai' });
  });
});

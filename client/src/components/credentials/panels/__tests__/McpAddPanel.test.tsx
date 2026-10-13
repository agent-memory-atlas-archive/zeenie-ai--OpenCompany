import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { CredentialsCatalogue, ConsumerProvider } from '../../catalogue';

const mocks = vi.hoisted(() => ({ sendRequest: vi.fn() }));
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => ({ sendRequest: mocks.sendRequest, isReady: true }),
  CREDENTIAL_PROBE_REQUEST_TIMEOUT: 60_000,
}));
vi.mock('@/app/ShellModeSwitch', () => ({ useShellMode: () => 'normal' }));
vi.mock('sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }));

import { ProviderPage } from '../../ProviderPage';

// The page's panel loads lazily; importing it first keeps that import out of
// the first test's wait.
beforeAll(async () => {
  await import('../McpAddPanel');
});

/** The MCP server card as the catalogue serves it (credential_providers.json `mcp`). */
const MCP_SERVER: ConsumerProvider = {
  id: 'mcp', name: 'MCP server', category: 'custom', category_label: 'Custom', consumer_category: 'custom',
  color: '', kind: 'mcpAdd', icon_ref: 'lobehub:mcp', publisher: 'OpenCompany', verified: true,
  stored: false, connected: false, fields: [],
  description: 'Connect any MCP server: its tools become an app your employees can use.',
  instructions: 'Give the address of a server that speaks MCP (the Model Context Protocol), and how it signs in.',
};

function mount() {
  const refetch = vi.fn().mockResolvedValue({});
  const view = {
    catalogue: { data: { providers: [MCP_SERVER] } }, providers: [MCP_SERVER], categories: [],
    isLoading: false, isError: false, error: null, refetch,
  } as unknown as CredentialsCatalogue;
  const onBack = vi.fn();
  const onOpen = vi.fn();
  render(
    <ProviderPage view={view} selection={{ id: 'mcp', intent: 'connect' }} backLabel="All connectors" onBack={onBack} visible onOpen={onOpen} />,
  );
  return { refetch, onBack, onOpen };
}

beforeEach(() => {
  mocks.sendRequest.mockReset();
});

describe('The MCP server card’s page', () => {
  it('is the custom connector form, and opens the connector it added', async () => {
    mocks.sendRequest.mockResolvedValue({ success: true, ref: 'mcp:orders' });
    const { refetch, onBack, onOpen } = mount();
    const form = within(await screen.findByRole('form', { name: 'Custom connector' }));
    expect(screen.getByText(/Give the address of a server that speaks MCP/)).toBeInTheDocument();
    fireEvent.change(form.getByLabelText('Server URL'), { target: { value: 'https://orders.example.com/mcp' } });
    fireEvent.click(form.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith('mcp:orders'));
    expect(mocks.sendRequest).toHaveBeenCalledWith(
      'mcp_connector_add',
      { name: '', url: 'https://orders.example.com/mcp', sign_in: { kind: 'none' } },
      60_000,
    );
    // Its page opens once the catalogue holds it, straight from this one.
    expect(refetch.mock.invocationCallOrder[0]).toBeLessThan(onOpen.mock.invocationCallOrder[0]);
    expect(onBack).not.toHaveBeenCalled();
  });

  it('goes back to the list on Cancel, and when the sign-in goes on in its own tab', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    mocks.sendRequest.mockResolvedValue({ success: true, ref: 'mcp:orders', sign_in_url: 'https://auth.example.com/authorize?state=s' });
    const { onBack, onOpen } = mount();
    const form = within(await screen.findByRole('form', { name: 'Custom connector' }));
    fireEvent.click(form.getByRole('button', { name: 'Cancel' }));
    expect(onBack).toHaveBeenCalledOnce();

    fireEvent.change(form.getByLabelText('Server URL'), { target: { value: 'https://orders.example.com/mcp' } });
    fireEvent.click(form.getByRole('radio', { name: 'OAuth' }));
    fireEvent.click(form.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(open).toHaveBeenCalledWith('https://auth.example.com/authorize?state=s', '_blank', 'noopener,noreferrer'));
    expect(onBack).toHaveBeenCalledTimes(2);
    expect(onOpen).not.toHaveBeenCalled();
    open.mockRestore();
  });
});

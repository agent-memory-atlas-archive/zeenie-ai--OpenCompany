/**
 * The panel saves a node's whole parameter row, so it must not present or
 * save defaults as the node's settings before that row has loaded. It used
 * to: while the socket was not ready the query was disabled, TanStack
 * reported it as not loading, and the panel seeded the buffer from defaults;
 * a failed read resolved `null` and did the same. Either way the next Save
 * replaced the stored settings with defaults.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { Node } from 'reactflow';
import { toast } from 'sonner';
import { makeTestQueryClient } from '../../test/providers';

const ws = vi.hoisted(() => ({
  getNodeParameters: vi.fn(),
  saveNodeParameters: vi.fn(),
  sendRequest: vi.fn(),
  isReady: true,
  isConnected: true,
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ws,
  useWebSocketActions: () => ws,
}));

vi.mock('../../lib/nodeSpec', () => ({
  resolveNodeDescription: () => ({
    properties: [
      { name: 'prompt', default: 'Hello' },
      { name: 'temperature', default: 0.7 },
    ],
  }),
}));

import { useParameterPanel } from '../useParameterPanel';
import { fetchSavedNodeParams } from '../useNodeParamsQuery';
import { useAppStore } from '../../store/useAppStore';

const NODE_ID = 'wf:aiAgent:1';

function renderPanel() {
  const client = makeTestQueryClient();
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useParameterPanel(), { wrapper });
}

let toastError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.resetAllMocks();
  ws.isReady = true;
  toastError = vi.spyOn(toast, 'error').mockImplementation(() => 'toast');
  useAppStore.setState({
    selectedNode: { id: NODE_ID, type: 'aiAgent', position: { x: 0, y: 0 }, data: {} } as Node,
  });
});

afterEach(() => {
  toastError.mockRestore();
  useAppStore.setState({ selectedNode: null });
});

describe('useParameterPanel loading', () => {
  it('seeds the buffer from the defaults and the saved row', async () => {
    ws.getNodeParameters.mockResolvedValue({ parameters: { prompt: 'Saved' }, version: 3 });

    const { result } = renderPanel();

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.parameters).toEqual({ prompt: 'Saved', temperature: 0.7 });
    expect(result.current.hasUnsavedChanges).toBe(false);
  });

  it('stays loading and saves nothing until the socket is ready', async () => {
    ws.isReady = false;

    const { result } = renderPanel();

    expect(result.current.isLoading).toBe(true);
    expect(result.current.parameters).toEqual({});

    act(() => result.current.handleParameterChange('prompt', 'Typed early'));
    await act(() => result.current.handleSave());

    expect(ws.getNodeParameters).not.toHaveBeenCalled();
    expect(ws.saveNodeParameters).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledTimes(1);
  });

  it('never seeds defaults or saves over the row when the read fails', async () => {
    ws.getNodeParameters.mockResolvedValue(null);

    const { result } = renderPanel();

    await waitFor(() => expect(result.current.error).toBe('Failed to load saved parameters'));
    expect(result.current.isLoading).toBe(true);
    expect(result.current.parameters).toEqual({});
    expect(toastError).toHaveBeenCalledTimes(1);

    act(() => result.current.handleParameterChange('prompt', 'Edited'));
    await act(() => result.current.handleSave());

    expect(ws.saveNodeParameters).not.toHaveBeenCalled();
  });
});

describe('useParameterPanel saving', () => {
  it('saves the loaded row with the edit and the row version', async () => {
    ws.getNodeParameters.mockResolvedValue({ parameters: { prompt: 'Saved', extra: 'kept' }, version: 3 });
    ws.saveNodeParameters.mockResolvedValue(true);

    const { result } = renderPanel();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.handleParameterChange('prompt', 'Edited'));
    await act(() => result.current.handleSave());

    expect(ws.saveNodeParameters).toHaveBeenCalledWith(
      NODE_ID,
      { prompt: 'Edited', temperature: 0.7, extra: 'kept' },
      3,
    );
    expect(result.current.hasUnsavedChanges).toBe(false);
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe('fetchSavedNodeParams', () => {
  it('passes a node without a saved row through as an empty row', async () => {
    const empty = { parameters: {}, version: 0 };
    await expect(fetchSavedNodeParams(async () => empty, NODE_ID)).resolves.toBe(empty);
  });

  it('fails a read that returned nothing, so no query caches it as a row', async () => {
    await expect(fetchSavedNodeParams(async () => null, NODE_ID)).rejects.toThrow(NODE_ID);
  });
});

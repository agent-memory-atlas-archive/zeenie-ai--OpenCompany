/**
 * workflowApi.saveWorkflow answers null when nothing was saved and tells
 * `onFailure` why, so the store can show the reason instead of only logging
 * that the save failed.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { workflowApi } from '../workflowApi';

const graph = { nodes: [], edges: [] };

function answer(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => body })));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('workflowApi.saveWorkflow', () => {
  it('passes the messages of a refused save to onFailure', async () => {
    answer({
      success: false,
      error: 'invalid_context_topology',
      validation_errors: [
        { code: 'SHARED_CONTEXT', message: 'A Context node cannot serve multiple agents' },
        { code: 'WITHOUT_MESSAGE' },
      ],
    });
    const onFailure = vi.fn();

    await expect(workflowApi.saveWorkflow('7', 'Maya', graph, onFailure)).resolves.toBeNull();

    expect(onFailure).toHaveBeenCalledWith({
      error: 'invalid_context_topology',
      messages: ['A Context node cannot serve multiple agents'],
    });
  });

  it('reports a request that never completed as unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const onFailure = vi.fn();

    await expect(workflowApi.saveWorkflow('7', 'Maya', graph, onFailure)).resolves.toBeNull();

    expect(onFailure).toHaveBeenCalledWith({ error: 'unreachable', messages: [] });
  });

  it('reports nothing when the save succeeds', async () => {
    answer({ success: true, id: '7', slug: 'Maya_1', data: graph });
    const onFailure = vi.fn();

    const result = await workflowApi.saveWorkflow('7', 'Maya', graph, onFailure);

    expect(result?.id).toBe('7');
    expect(onFailure).not.toHaveBeenCalled();
  });
});

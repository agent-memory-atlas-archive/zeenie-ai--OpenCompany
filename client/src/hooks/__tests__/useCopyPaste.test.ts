/**
 * Locks what a pasted node is saved with.
 *
 * node.data holds only UI fields (the label), so it is never a node's
 * parameter row. Paste used to save node.data as the copy's parameters,
 * which left every pasted node with its configuration gone. The copy now
 * gets the original's saved parameters, read when it was copied, minus
 * runtime state (conversation, session ids, cached results).
 */

import { describe, it, expect, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { Node } from 'reactflow';
import { useCopyPaste } from '../useCopyPaste';

const original: Node = {
  id: 'wf:httpRequest:1',
  type: 'httpRequest',
  position: { x: 0, y: 0 },
  data: { label: 'Fetch Prices' },
  selected: true,
};

function setup(getNodeParameters: (id: string) => Promise<{ parameters: Record<string, any> } | null>) {
  const saveNodeParameters = vi.fn().mockResolvedValue(true);
  const setNodes = vi.fn();
  const setEdges = vi.fn();
  const hook = renderHook(() => useCopyPaste({
    nodes: [original],
    edges: [],
    setNodes,
    setEdges,
    saveNodeParameters,
    getNodeParameters,
    workflowId: 'wf',
  }));
  return { ...hook, saveNodeParameters, setNodes };
}

describe('useCopyPaste', () => {
  it("saves the original's parameters, without runtime state, under the new id", async () => {
    const getNodeParameters = vi.fn().mockResolvedValue({
      parameters: {
        url: 'https://example.com/prices',
        method: 'POST',
        label: 'Fetch Prices',
        last_result: { status: 200 },
        memory_content: 'a whole conversation',
      },
    });
    const { result, saveNodeParameters, setNodes } = setup(getNodeParameters);

    act(() => result.current.copySelectedNodes());
    await act(async () => {
      await result.current.pasteNodes();
    });

    expect(saveNodeParameters).toHaveBeenCalledTimes(1);
    const [newId, saved] = saveNodeParameters.mock.calls[0];
    expect(newId).not.toBe(original.id);
    expect(saved).toMatchObject({ url: 'https://example.com/prices', method: 'POST' });
    expect(saved).not.toHaveProperty('last_result');
    expect(saved).not.toHaveProperty('memory_content');

    // The saved label matches the pasted node's label.
    const pasted = (setNodes.mock.calls[0][0] as (nodes: Node[]) => Node[])([original]);
    const copy = pasted.find(n => n.id === newId)!;
    expect(saved.label).toBe(copy.data.label);
  });

  it('reads the parameters at copy time, not at paste time', async () => {
    const getNodeParameters = vi.fn().mockResolvedValue({ parameters: { url: 'https://a' } });
    const { result } = setup(getNodeParameters);

    act(() => result.current.copySelectedNodes());
    expect(getNodeParameters).toHaveBeenCalledWith(original.id);
    getNodeParameters.mockClear();

    await act(async () => {
      await result.current.pasteNodes();
    });
    expect(getNodeParameters).not.toHaveBeenCalled();
  });

  it('still saves a row with the label when the original could not be read', async () => {
    const { result, saveNodeParameters } = setup(vi.fn().mockResolvedValue(null));

    act(() => result.current.copySelectedNodes());
    await act(async () => {
      await result.current.pasteNodes();
    });

    const [, saved] = saveNodeParameters.mock.calls[0];
    expect(typeof saved.label).toBe('string');
    expect(saved.label.length).toBeGreaterThan(0);
  });
});

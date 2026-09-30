import { act, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ReactFlowProvider, type NodeProps } from 'reactflow';
import type { NodeData } from '@/types/NodeTypes';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';

vi.mock('@/hooks/useAppTheme', async () => {
  const { theme } = await import('@/styles/theme');
  return { useAppTheme: () => theme };
});
vi.mock('@/contexts/WebSocketContext', async () => {
  const { useNodeStatusForId } = await import('@/stores/nodeStatusStore');
  return { useNodeStatus: useNodeStatusForId };
});
vi.mock('@/lib/nodeSpec', () => ({ useNodeSpec: () => ({ handles: [], subtitle: 'Ready' }) }));
vi.mock('@/components/ui/EditableNodeLabel', () => ({ default: () => <span>Agent</span> }));
vi.mock('@/assets/icons', () => ({ NodeIcon: () => null }));

import AIAgentNode from '../AIAgentNode';

beforeEach(() => {
  useNodeStatusStore.setState({ allStatuses: {}, currentWorkflowId: 'w1' });
});

it('shows provider retry ahead of a sticky tool label, then returns to normal progress', () => {
  useNodeStatusStore.getState().setStatus('w1', 'agent', { status: 'executing', data: {
    phase: 'retry_wait', retry_message: 'Gemini is temporarily unavailable.',
    last_capability: { kind: 'tool', name: 'WriteTodos' }, iteration: 7, max_iterations: 100,
  } });
  const props: NodeProps<NodeData> = {
    id: 'agent', type: 'aiAgent', data: {}, selected: false, isConnectable: true,
    xPos: 0, yPos: 0, zIndex: 0, dragging: false,
  };
  render(<ReactFlowProvider><AIAgentNode {...props} /></ReactFlowProvider>);
  expect(screen.getByRole('status')).toHaveTextContent('Gemini is temporarily unavailable. Retrying automatically…');
  expect(screen.queryByText('tool WriteTodos')).not.toBeInTheDocument();
  act(() => useNodeStatusStore.getState().setStatus('w1', 'agent', {
    status: 'executing', data: { phase: 'llm_step' },
  }));
  expect(screen.queryByText(/Retrying automatically/)).not.toBeInTheDocument();
  expect(screen.getByText('Thinking...')).toBeInTheDocument();
  act(() => useNodeStatusStore.getState().setStatus('w1', 'agent', { status: 'success' }));
  expect(screen.getByText('Ready')).toBeInTheDocument();
});

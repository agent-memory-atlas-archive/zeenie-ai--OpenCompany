/**
 * A double-click rename is a canvas edit, so it obeys the canvas lock the
 * same way F2 and the context menu do. It used to call `onActivate`
 * unconditionally, so a running workflow's nodes could still be renamed.
 */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import EditableNodeLabel from '../EditableNodeLabel';
import { CanvasEditGuardContext } from '../../../contexts/canvasEditGuard';

function renderLabel(onActivate: () => void, guard?: () => boolean) {
  const label = (
    <EditableNodeLabel
      nodeId="wf:aiAgent:1"
      label="Researcher"
      defaultLabel="AI Agent"
      onLabelChange={vi.fn()}
      onActivate={onActivate}
    />
  );
  return render(
    guard ? <CanvasEditGuardContext.Provider value={guard}>{label}</CanvasEditGuardContext.Provider> : label,
  );
}

describe('EditableNodeLabel double-click', () => {
  it('starts a rename when the canvas is editable', () => {
    const onActivate = vi.fn();
    renderLabel(onActivate, () => true);

    fireEvent.doubleClick(screen.getByText('Researcher'));

    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('does not start a rename while the canvas is locked', () => {
    const onActivate = vi.fn();
    const guard = vi.fn(() => false);
    renderLabel(onActivate, guard);

    fireEvent.doubleClick(screen.getByText('Researcher'));

    expect(guard).toHaveBeenCalledTimes(1);
    expect(onActivate).not.toHaveBeenCalled();
  });

  it('allows a rename where no editor provides a guard', () => {
    const onActivate = vi.fn();
    renderLabel(onActivate);

    fireEvent.doubleClick(screen.getByText('Researcher'));

    expect(onActivate).toHaveBeenCalledTimes(1);
  });
});

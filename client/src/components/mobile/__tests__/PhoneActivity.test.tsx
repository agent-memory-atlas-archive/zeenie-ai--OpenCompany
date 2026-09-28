import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PhoneActivity } from '../PhoneActivity';

describe('Android activity', () => {
  it('shows a quiet model wait with timing and actual operations', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', phase: 'Waiting for model', started_at: now - 65,
      phase_started_at: now - 40, updated_at: now - 40, model: 'test-model', provider: 'openai',
      activity: [{ at: now - 42, message: 'Reading screen and accessibility tree: completed (0.2s)', step: 1 }] }} />);
    expect(screen.getByText(/No new activity for 40s/)).toBeInTheDocument();
    expect(screen.getByText(/Reading screen and accessibility tree/)).toBeInTheDocument();
    expect(screen.getByText(/openai test-model/)).toBeInTheDocument();
  });
  it('retains the failure location after the task ends', () => {
    render(<PhoneActivity task={{ run_id: 'run', status: 'failed', phase: 'Waiting for model',
      started_at: 100, finished_at: 180, error_code: 'timeout' }} />);
    expect(screen.getByText(/Stopped during Waiting for model · timeout/)).toBeInTheDocument();
    expect(screen.getByText(/80s total/)).toBeInTheDocument();
    expect(screen.queryByText(/No new activity/)).toBeNull();
  });
});

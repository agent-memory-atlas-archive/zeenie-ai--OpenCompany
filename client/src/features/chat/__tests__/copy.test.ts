/**
 * What a turn says: when a message was written, why a run did not answer,
 * and what a working run is doing.
 */

import { describe, expect, it } from 'vitest';
import { emptyRun } from '@/lib/agui/reduceRun';
import { timeLabel } from '../thread/timeLabel';
import { failureLines, liveLabel, streamedText } from '../turns/runCopy';

describe('timeLabel', () => {
  const now = new Date(2026, 8, 28, 15, 30);
  const time = (date: Date) => date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

  it('gives the time today and says yesterday', () => {
    const morning = new Date(2026, 8, 28, 9, 5);
    expect(timeLabel(morning.toISOString(), now)).toBe(time(morning));
    const lastNight = new Date(2026, 8, 27, 22, 40);
    expect(timeLabel(lastNight.toISOString(), now)).toBe(`Yesterday, ${time(lastNight)}`);
  });

  it('names the weekday within a week, else the date', () => {
    const monday = new Date(2026, 8, 22, 10, 0);
    expect(timeLabel(monday.toISOString(), now)).toBe(`${monday.toLocaleDateString(undefined, { weekday: 'long' })}, ${time(monday)}`);
    const earlier = new Date(2026, 7, 3, 10, 0);
    expect(timeLabel(earlier.toISOString(), now)).toBe(
      `${earlier.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${time(earlier)}`,
    );
    const lastYear = new Date(2025, 11, 31, 10, 0);
    expect(timeLabel(lastYear.toISOString(), now)).toContain(lastYear.getFullYear().toString());
  });

  it('says nothing for a time it cannot read', () => {
    expect(timeLabel(null, now)).toBe('');
    expect(timeLabel('yesterday-ish', now)).toBe('');
  });
});

describe('failureLines', () => {
  it('names the employee when no answer came', () => {
    expect(failureLines({ message: 'The employee did not pick up this message.', code: 'not_delivered' }, 'Maya')).toEqual({
      headline: 'Maya didn’t pick up this message.',
      detail: null,
    });
    expect(failureLines({ message: 'x', code: 'timed_out' }, 'Maya').headline).toBe('Maya took too long to answer.');
    expect(failureLines({ message: 'x', code: 'interrupted' }, 'Maya').headline).toBe('Maya stopped before answering.');
  });

  it('keeps the server’s message for anything else', () => {
    expect(failureLines({ message: 'Spending cap reached.', code: 'run_failed' }, 'Maya')).toEqual({
      headline: 'Maya couldn’t answer.',
      detail: 'Spending cap reached.',
    });
    expect(failureLines(null, 'Maya')).toEqual({ headline: 'Maya couldn’t answer.', detail: null });
  });
});

describe('a working run', () => {
  it('says what it is doing', () => {
    const working = { ...emptyRun('r1', 'w1'), state: 'running' as const };
    expect(liveLabel(working, false)).toBe('Working…');
    expect(liveLabel(working, true)).toBe('Writing…');
    expect(liveLabel({ ...working, state: 'stopping' }, true)).toBe('Stopping…');
  });

  it('shows the reply it streamed, not its narration', () => {
    const run = {
      ...emptyRun('r1', 'w1'),
      segments: [
        { messageId: 'n', text: 'Let me look.', final: false },
        { messageId: 'a', text: 'Two bookings', final: null },
      ],
    };
    expect(streamedText(run)).toBe('Two bookings');
    expect(streamedText(null)).toBe('');
  });
});

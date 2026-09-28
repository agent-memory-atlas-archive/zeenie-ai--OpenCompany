/**
 * The thread's data: rows the server sends are parsed forgivingly, a
 * divider marks every restart between two messages, the newest answer is
 * what a send waits to change, and times read the way a person says them.
 */

import { describe, expect, it } from 'vitest';
import { latestReplyId, parseThread, threadRows, timeLabel, type ThreadMessage } from '../data/talk';

function message(id: string, role: 'user' | 'assistant', run_key: string | null): ThreadMessage {
  return { id, role, message: `${role} ${id}`, timestamp: null, run_key };
}

describe('parseThread', () => {
  it('keeps the order and drops rows it cannot show', () => {
    const parsed = parseThread([
      { id: 1, role: 'user', message: 'Hi', timestamp: '2026-09-28T09:00:00+00:00', run_key: 'g1' },
      { id: 2, role: 'system', message: 'hidden' },
      { role: 'assistant', message: 'no id' },
      { id: '3', role: 'assistant', message: 'Hello!', timestamp: 42, run_key: 7 },
    ]);
    expect(parsed).toEqual([
      { id: '1', role: 'user', message: 'Hi', timestamp: '2026-09-28T09:00:00+00:00', run_key: 'g1' },
      { id: '3', role: 'assistant', message: 'Hello!', timestamp: null, run_key: null },
    ]);
    expect(parseThread({ messages: [] })).toEqual([]);
  });
});

describe('threadRows', () => {
  it('draws a divider where the generation changes', () => {
    const rows = threadRows([message('1', 'user', 'g1'), message('2', 'assistant', 'g1'), message('3', 'user', 'g2')]);
    expect(rows.map((row) => (row.kind === 'restart' ? '|' : row.message.id))).toEqual(['1', '2', '|', '3']);
  });

  it('never draws one for a message of unknown generation', () => {
    const rows = threadRows([message('1', 'user', null), message('2', 'user', 'g1'), { ...message('3', 'user', null), pending: true }]);
    expect(rows.every((row) => row.kind === 'message')).toBe(true);
    // Unknown in between: the next known generation still compares to the last known one.
    const skipped = threadRows([message('1', 'user', 'g1'), message('2', 'user', null), message('3', 'assistant', 'g2')]);
    expect(skipped.map((row) => row.kind)).toEqual(['message', 'message', 'restart', 'message']);
  });
});

describe('latestReplyId', () => {
  it('is the newest answer, whatever came after it', () => {
    expect(latestReplyId([message('1', 'assistant', 'g1'), message('2', 'user', 'g1')])).toBe('1');
    expect(latestReplyId([message('1', 'user', 'g1')])).toBeNull();
    expect(latestReplyId(undefined)).toBeNull();
  });
});

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

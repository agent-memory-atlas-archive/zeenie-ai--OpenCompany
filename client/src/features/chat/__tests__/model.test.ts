/**
 * The rows a thread draws: which reading of a run wins, where a run's state
 * shows, which failures stay quiet, and the keys that keep one element from
 * the message sent to the saved row, and from the run's turn to its answer.
 */

import { describe, expect, it } from 'vitest';
import { emptyRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import type { ChatMessage, MessageRun } from '../data/schemas';
import { buildTurns, knownRuns } from '../thread/model';

function message(id: string, role: 'user' | 'assistant', patch: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id,
    legacyId: null,
    role,
    kind: 'text',
    text: `${role} ${id}`,
    timestamp: '2026-10-03T09:00:00Z',
    runKey: 'g1',
    runId: null,
    parentId: null,
    status: 'complete',
    attachments: [],
    parts: {},
    clientMessageId: null,
    run: null,
    ...patch,
  };
}

function run(runId: string, patch: Partial<RunSnapshot> = {}): RunSnapshot {
  return { ...emptyRun(runId, 'w1'), ...patch };
}

const messageRun = (runId: string, state: MessageRun['state'], error: MessageRun['error'] = null): MessageRun => ({
  runId,
  state,
  outcome: state === 'finished' ? 'success' : null,
  error,
  steps: [],
  durationMs: null,
});

function shape(turns: ReturnType<typeof buildTurns>) {
  return turns.map((turn) =>
    turn.kind === 'divider'
      ? 'divider'
      : turn.kind === 'user'
        ? `user:${turn.message.id}`
        : `assistant:${turn.message?.id ?? '-'}:${turn.run ? turn.run.state : '-'}`,
  );
}

describe('knownRuns', () => {
  const failed = message('m1', 'user', { runId: 'r1', run: messageRun('r1', 'error', { message: 'No', code: 'run_failed' }) });
  const live = message('m2', 'user', { runId: 'r2', run: messageRun('r2', 'running') });

  it('prefers the run store over what a message said', () => {
    const runs = knownRuns('w1', [failed], { r1: run('r1', { state: 'finished' }) }, true);
    expect(runs.r1.state).toBe('finished');
  });

  it('believes the thread read until the subscription answers', () => {
    const active = run('r3', { state: 'running' });
    const before = knownRuns('w1', [live], {}, false, [active]);
    expect(Object.keys(before).sort()).toEqual(['r2', 'r3']);
    expect(before.r2.userMessageId).toBe('m2');

    // Afterwards a run the store does not hold as live is not live.
    const after = knownRuns('w1', [live, failed], {}, true, [active]);
    expect(Object.keys(after)).toEqual(['r1']);
    expect(after.r1).toMatchObject({ state: 'error', error: { message: 'No' } });
  });
});

describe('buildTurns', () => {
  it('draws a divider where the employee restarted', () => {
    const turns = buildTurns(
      [message('m1', 'user'), message('a1', 'assistant'), message('m2', 'user', { runKey: 'g2' }), message('m3', 'user', { runKey: null })],
      {},
    );
    expect(shape(turns)).toEqual(['user:m1', 'assistant:a1:-', 'divider', 'user:m2', 'user:m3']);
  });

  it('keeps a sent message one element when its saved row arrives', () => {
    const local = message('local:c1', 'user', { clientMessageId: 'c1', pending: true });
    const saved = message('m1', 'user', { clientMessageId: 'c1' });
    expect(buildTurns([local], {})[0].key).toBe('user:c1');
    const both = buildTurns([saved, local], {});
    expect(shape(both)).toEqual(['user:m1']);
    expect(both[0].key).toBe('user:c1');
  });

  it('shows a working run on its own turn, then its answer takes that turn', () => {
    const user = message('m1', 'user', { runId: 'r1' });
    const working = buildTurns([user], { r1: run('r1', { state: 'running' }) });
    expect(shape(working)).toEqual(['user:m1', 'assistant:-:running']);
    expect(working[1].key).toBe('run:r1');

    const answered = buildTurns([user, message('a1', 'assistant', { runId: 'r1' })], { r1: run('r1', { state: 'finished' }) });
    expect(shape(answered)).toEqual(['user:m1', 'assistant:a1:-']);
    expect(answered[1].key).toBe('run:r1');
  });

  it('shows a run still going on its last answer', () => {
    const turns = buildTurns(
      [message('m1', 'user', { runId: 'r1' }), message('a1', 'assistant', { runId: 'r1' }), message('a2', 'assistant', { runId: 'r1' })],
      { r1: run('r1', { state: 'running' }) },
    );
    expect(shape(turns)).toEqual(['user:m1', 'assistant:a1:-', 'assistant:a2:running']);
    expect(turns.map((turn) => turn.key)).toEqual(['user:m1', 'run:r1', 'reply:a2']);
  });

  it('keeps a failure where the answer would be, unless an answer came after all', () => {
    const user = message('m1', 'user', { runId: 'r1' });
    const failed = run('r1', { state: 'error', error: { message: 'No', code: 'run_failed' } });
    expect(shape(buildTurns([user], { r1: failed }))).toEqual(['user:m1', 'assistant:-:error']);

    const timedOut = run('r1', { state: 'error', error: { message: 'Slow', code: 'timed_out' } });
    expect(shape(buildTurns([user], { r1: timedOut }))).toEqual(['user:m1', 'assistant:-:error']);
    expect(shape(buildTurns([user, message('a1', 'assistant', { runId: 'r1' })], { r1: timedOut }))).toEqual(['user:m1', 'assistant:a1:-']);
  });

  it('says nothing for a run its conversation took with it', () => {
    const user = message('m1', 'user', { runId: 'r1' });
    const cleared = run('r1', { state: 'error', error: { message: 'Cleared', code: 'cleared' } });
    expect(shape(buildTurns([user], { r1: cleared }))).toEqual(['user:m1']);
  });

  it('shows a finished run only while its streamed answer is on the way', () => {
    const user = message('m1', 'user', { runId: 'r1' });
    const streamed = run('r1', { state: 'finished', segments: [{ messageId: 's1', text: 'Two bookings.', final: true }] });
    expect(shape(buildTurns([user], { r1: streamed }))).toEqual(['user:m1', 'assistant:-:finished']);
    expect(shape(buildTurns([user], { r1: run('r1', { state: 'finished' }) }))).toEqual(['user:m1']);
    const quiet = run('r1', { state: 'finished', result: { noReply: true }, segments: [{ messageId: 's1', text: 'x', final: true }] });
    expect(shape(buildTurns([user], { r1: quiet }))).toEqual(['user:m1']);
  });

  it('puts a live run whose messages are out of view last, and drops an ended one', () => {
    const turns = buildTurns([message('m9', 'user')], {
      r1: run('r1', { state: 'running', createdAt: '2026-10-03T09:02:00Z' }),
      r0: run('r0', { state: 'queued', createdAt: '2026-10-03T09:01:00Z' }),
      r2: run('r2', { state: 'error', error: { message: 'Old', code: 'run_failed' } }),
    });
    expect(shape(turns)).toEqual(['user:m9', 'assistant:-:queued', 'assistant:-:running']);
  });

  it('keeps a run’s steps on its first turn, working and after', () => {
    const steps = [{ stepId: 'c1', name: 'Searched the web', state: 'done' as const }];
    const user = message('m1', 'user', { runId: 'r1' });
    const working = buildTurns([user], { r1: run('r1', { state: 'running', steps }) });
    const live = working[1];
    expect(live.kind === 'assistant' && live.work).toEqual({ steps, live: true, durationMs: null });

    const answers = [user, message('a1', 'assistant', { runId: 'r1' }), message('a2', 'assistant', { runId: 'r1' })];
    const done = buildTurns(answers, {
      r1: run('r1', { state: 'finished', steps, startedAt: '2026-10-03T09:00:00Z', finishedAt: '2026-10-03T09:00:12Z' }),
    });
    expect(done.map((turn) => (turn.kind === 'assistant' ? turn.work : undefined))).toEqual([
      undefined,
      { steps, live: false, durationMs: 12_000 },
      null,
    ]);
    // A run that took no steps has none to show.
    const plain = buildTurns([user, message('a1', 'assistant', { runId: 'r1' })], { r1: run('r1', { state: 'finished' }) });
    expect(plain[1].kind === 'assistant' && plain[1].work).toBeNull();
  });

  it('reads the steps a thread sent after a reload', () => {
    const withSteps: MessageRun = {
      ...messageRun('r1', 'finished'),
      steps: [{ stepId: 'c1', name: 'Checked Google Calendar', state: 'done', detail: '3 events' }],
      durationMs: 8_000,
    };
    const thread = [message('m1', 'user', { runId: 'r1', run: withSteps }), message('a1', 'assistant', { runId: 'r1', run: withSteps })];
    const turns = buildTurns(thread, knownRuns('w1', thread, {}, true));
    const answer = turns[1];
    expect(answer.kind === 'assistant' && answer.work).toEqual({ steps: withSteps.steps, live: false, durationMs: 8_000 });
  });
});

/**
 * What a turn says about its run, kept apart from the component so the
 * component file exports components only (react-refresh) and the wording is
 * testable on its own.
 */

import type { RunError } from '@/lib/agui/events';
import type { RunSnapshot } from '@/lib/agui/reduceRun';

/** What a run that has not answered streamed so far: its reply text, not
 *  the narration beside its tool calls. */
export function streamedText(run: RunSnapshot | null): string {
  if (!run) return '';
  return run.segments
    .filter((segment) => segment.final !== false)
    .map((segment) => segment.text)
    .join('\n\n')
    .trim();
}

/** A failed run in the owner's words: the server's codes for a message
 *  nobody answered get a sentence naming the employee; any other failure
 *  keeps the server's own message as the detail. */
export function failureLines(error: RunError | null, name: string): { headline: string; detail: string | null } {
  switch (error?.code) {
    case 'not_delivered':
      return { headline: `${name} didn’t pick up this message.`, detail: null };
    case 'timed_out':
      return { headline: `${name} took too long to answer.`, detail: null };
    case 'interrupted':
      return { headline: `${name} stopped before answering.`, detail: null };
    default:
      return { headline: `${name} couldn’t answer.`, detail: error?.message || null };
  }
}

/** The status line's label while a run works. */
export function liveLabel(run: RunSnapshot, hasText: boolean): string {
  if (run.state === 'stopping') return 'Stopping…';
  return hasText ? 'Writing…' : 'Working…';
}

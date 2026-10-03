/**
 * A chat thread as `get_chat_messages` sends it (docs-internal/chat_protocol.md,
 * "Messages"), read into camelCase. Every field is read forgivingly: a reply
 * from a server that predates a field still parses, and a row this client
 * cannot show is dropped rather than failing the thread.
 */

import { z } from 'zod';
import type { RunError, RunState } from '@/lib/agui/events';
import { parseError } from '@/lib/agui/events';
import { snapshotFromWire, stepsFromWire, type RunSnapshot, type RunStep } from '@/lib/agui/reduceRun';

const RUN_STATES = ['queued', 'pending', 'running', 'stopping', 'finished', 'error', 'stopped'] as const;

/** How the run a message started or answers ended (or is going), what the
 *  employee did on the way and how long it took. */
export interface MessageRun {
  runId: string;
  state: RunState;
  outcome: string | null;
  error: RunError | null;
  steps: RunStep[];
  durationMs: number | null;
}

const messageRunSchema = z
  .object({
    run_id: z.string(),
    state: z.enum(RUN_STATES).catch('finished'),
    outcome: z.string().nullable().catch(null),
    error: z.record(z.string(), z.unknown()).nullable().catch(null),
    steps: z.unknown().optional(),
    duration_ms: z.number().nonnegative().nullable().optional().catch(null),
  })
  .transform(
    (run): MessageRun => ({
      runId: run.run_id,
      state: run.state,
      outcome: run.outcome,
      error: run.error ? parseError(run.error) : null,
      steps: stepsFromWire(run.steps),
      durationMs: run.duration_ms ?? null,
    }),
  );

const messageSchema = z
  .object({
    id: z.union([z.number(), z.string()]).transform(String),
    legacy_id: z.number().nullable().catch(null),
    role: z.enum(['user', 'assistant']),
    kind: z.string().catch('text'),
    text: z.string().optional(),
    message: z.string().catch(''),
    timestamp: z.string().nullable().catch(null),
    run_key: z.string().nullable().catch(null),
    run_id: z.string().nullable().catch(null),
    parent_id: z.string().nullable().catch(null),
    status: z.string().catch('complete'),
    attachments: z.array(z.unknown()).catch([]),
    parts: z.record(z.string(), z.unknown()).catch({}),
    client_message_id: z.string().nullable().catch(null),
    run: messageRunSchema.nullable().catch(null),
  })
  .transform((row) => ({
    id: row.id,
    legacyId: row.legacy_id,
    role: row.role,
    kind: row.kind,
    text: row.text ?? row.message,
    timestamp: row.timestamp,
    runKey: row.run_key,
    runId: row.run_id,
    parentId: row.parent_id,
    status: row.status,
    attachments: row.attachments,
    parts: row.parts,
    clientMessageId: row.client_message_id,
    run: row.run,
  }));

export type ChatMessage = z.output<typeof messageSchema> & {
  /** Sent from this tab and not confirmed by the server yet. */
  pending?: boolean;
};

export interface ThreadState {
  activeLeafId: string | null;
  revision: number;
}

export interface ChatThreadData {
  messages: ChatMessage[];
  thread: ThreadState;
  /** The session's live runs when the thread was read. */
  activeRuns: RunSnapshot[];
}

/** The messages of a `get_chat_messages` reply, oldest first, dropping rows
 *  this client cannot show. */
export function parseMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const parsed = messageSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  });
}

const threadStateSchema = z
  .object({
    active_leaf_id: z.string().nullable().catch(null),
    revision: z.number().catch(0),
  })
  .transform((thread): ThreadState => ({ activeLeafId: thread.active_leaf_id, revision: thread.revision }))
  .catch({ activeLeafId: null, revision: 0 });

export function parseThreadReply(reply: { messages?: unknown; thread?: unknown; active_runs?: unknown }): ChatThreadData {
  const activeRuns = Array.isArray(reply.active_runs)
    ? reply.active_runs.flatMap((raw) => {
        const run = snapshotFromWire(raw);
        return run ? [run] : [];
      })
    : [];
  return { messages: parseMessages(reply.messages), thread: threadStateSchema.parse(reply.thread ?? {}), activeRuns };
}

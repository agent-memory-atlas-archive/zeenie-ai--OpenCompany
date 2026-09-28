/**
 * Talking to an employee on its page, over the chat session whose id is the
 * employee's workflow id:
 *
 * - `useTalkThread(workflowId)`: the conversation across restarts
 *   (`get_chat_messages` with `all_generations`), the newest 200 messages.
 *   WebSocketContext invalidates it on `chat.updated`, on a runtime reset
 *   and when the socket reopens.
 * - `useSendTalkMessage(workflowId)`: `send_chat_message`. The message shows
 *   at once and leaves again if the server refuses it (`not_running`: it
 *   saved nothing); the response's `delivery` says whether it went now or
 *   waits for Resume.
 * - `useReplyWait(...)`: "Thinking…" after a send. It follows the talk
 *   agent's node status (`useNodeStatusStore` directly, like `useLiveTask`:
 *   the editor's hooks see only the workflow open in Dev mode), with
 *   fallbacks for an agent that never picks the message up or never ends.
 * - `useEnableTalk()` / `useApplyChanges()`: Turn on Talk and Apply. Both
 *   restart the employee, so they wait as long as Start does; the summary
 *   they return goes into the team cache.
 * - Pure helpers the thread renders from: restart dividers, time labels.
 */

import { useCallback, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { WORKFLOW_CONTROL_REQUEST_TIMEOUT, useWebSocketActions } from '@/contexts/WebSocketContext';
import { STALE_TIME, queryKeys } from '@/lib/queryConfig';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { upsertEmployee } from './employees';
import { parseEmployee, type EmployeeSummary } from './schemas';

/** The newest messages the thread shows. */
export const THREAD_LIMIT = 200;
/** How long the talk agent has to pick a message up. */
export const PICKUP_WAIT_MS = 30_000;
/** The longest "Thinking…" lasts, however long the agent works. */
export const REPLY_WAIT_MS = 180_000;
/** Once the agent stops, how long its answer has to reach the thread. */
export const SETTLE_WAIT_MS = 5_000;

export const threadKey = (workflowId: string) => queryKeys.chatThread.bySession(workflowId).queryKey;

const threadMessageSchema = z.object({
  id: z.union([z.number(), z.string()]).transform((id) => String(id)),
  role: z.enum(['user', 'assistant']),
  message: z.string(),
  timestamp: z.string().nullable().catch(null),
  /** The generation the message was written in; it changes at each restart. */
  run_key: z.string().nullable().catch(null),
});

export interface ThreadMessage extends z.infer<typeof threadMessageSchema> {
  /** Sent from this tab; the server has not confirmed it yet. */
  pending?: boolean;
}

/** A thread from the server, oldest first, dropping rows it cannot show. */
export function parseThread(raw: unknown): ThreadMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const parsed = threadMessageSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  });
}

export type ThreadRow = { kind: 'message'; message: ThreadMessage } | { kind: 'restart'; key: string };

/** The thread with a divider wherever the employee restarted between two
 *  messages. A message whose generation is unknown (written before rows were
 *  stamped, or not saved yet) never draws one. */
export function threadRows(messages: readonly ThreadMessage[]): ThreadRow[] {
  const rows: ThreadRow[] = [];
  let previous: string | null = null;
  for (const message of messages) {
    if (previous && message.run_key && message.run_key !== previous) {
      rows.push({ kind: 'restart', key: `restart-${message.id}` });
    }
    if (message.run_key) previous = message.run_key;
    rows.push({ kind: 'message', message });
  }
  return rows;
}

/** The newest answer's id, or null. Another one after a send means the send
 *  was answered. */
export function latestReplyId(messages: readonly ThreadMessage[] | undefined): string | null {
  if (!messages) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') return messages[i].id;
  }
  return null;
}

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** When a message was written, the way a person says it: the time today,
 *  "Yesterday, 9:41 AM", the weekday within a week, else the date. Empty
 *  when the timestamp cannot be read. */
export function timeLabel(timestamp: string | null, now: Date = new Date()): string {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  const time = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  // Rounded: a day with a clock change is 23 or 25 hours long.
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  if (days <= 0) return time;
  if (days === 1) return `Yesterday, ${time}`;
  if (days < 7) return `${date.toLocaleDateString(undefined, { weekday: 'long' })}, ${time}`;
  const year = date.getFullYear() === now.getFullYear() ? undefined : 'numeric';
  return `${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year })}, ${time}`;
}

// ----- the conversation -----

export function useTalkThread(workflowId: string) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ThreadMessage[], Error>({
    queryKey: threadKey(workflowId),
    queryFn: async () => {
      const response = await sendRequest<{ success?: boolean; messages?: unknown; error?: string }>('get_chat_messages', {
        session_id: workflowId,
        limit: THREAD_LIMIT,
        all_generations: true,
      });
      if (response?.success === false) throw new Error(response.error || 'Could not load the conversation');
      return parseThread(response?.messages);
    },
    enabled: isReady && Boolean(workflowId),
    // Broadcasts keep it current. Opening the page refetches only when one
    // arrived while it was closed (it left the thread invalidated).
    staleTime: STALE_TIME.FOREVER,
    refetchOnMount: true,
  });
}

/** Where a sent message went: to the employee now, or waiting for Resume. */
export type Delivery = 'now' | 'queued';

/** Numbers the messages shown before the server has saved them. */
let localSequence = 0;

export function useSendTalkMessage(workflowId: string) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const key = threadKey(workflowId);
  return useMutation<Delivery, Error, string, { localId: string }>({
    mutationFn: async (text) => {
      const response = await sendRequest<{ success?: boolean; error?: string; delivery?: string }>('send_chat_message', {
        message: text,
        role: 'user',
        session_id: workflowId,
        timestamp: new Date().toISOString(),
      });
      if (response?.success === false) throw new Error(response.error || 'send_failed');
      return response?.delivery === 'queued' ? 'queued' : 'now';
    },
    onMutate: async (text) => {
      await queryClient.cancelQueries({ queryKey: key });
      const localId = `local-${(localSequence += 1)}`;
      const local: ThreadMessage = {
        id: localId,
        role: 'user',
        message: text,
        timestamp: new Date().toISOString(),
        run_key: null,
        pending: true,
      };
      queryClient.setQueryData<ThreadMessage[]>(key, (list) => [...(list ?? []), local]);
      return { localId };
    },
    onSuccess: (_delivery, _text, context) => {
      queryClient.setQueryData<ThreadMessage[]>(key, (list) =>
        list?.map((message) => (message.id === context.localId ? { ...message, pending: false } : message)),
      );
    },
    onError: (_error, _text, context) => {
      if (!context) return;
      queryClient.setQueryData<ThreadMessage[]>(key, (list) => list?.filter((message) => message.id !== context.localId));
    },
    // The refetch swaps the local copy for the saved row.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}

// ----- waiting for the answer -----

interface Wait {
  since: number;
  /** The newest answer when the message went out; another one answers it. */
  repliedTo: string | null;
  /** The agent has been seen working. */
  working: boolean;
  /** The agent stopped; its answer may still be on its way to the thread. */
  settling: boolean;
}

export interface ReplyWait {
  /** "Thinking…": the message box holds until this clears. */
  waiting: boolean;
  /** The last wait ended with no answer. */
  unanswered: boolean;
  /** A message just went to the agent. */
  begin: () => void;
  /** It did not after all (refused, or waiting for Resume). */
  cancel: () => void;
}

export function useReplyWait(
  workflowId: string,
  agentNodeId: string | null,
  messages: readonly ThreadMessage[] | undefined,
): ReplyWait {
  const [wait, setWait] = useState<Wait | null>(null);
  const [unanswered, setUnanswered] = useState(false);
  const status = useNodeStatusStore(
    useCallback(
      (state) => (agentNodeId ? state.allStatuses[workflowId]?.[agentNodeId]?.status : undefined),
      [workflowId, agentNodeId],
    ),
  );
  const replyId = latestReplyId(messages);

  // An answer ends the wait. The agent working, then stopping, settles it.
  useEffect(() => {
    if (!wait) return;
    if (replyId !== wait.repliedTo) {
      setWait(null);
      setUnanswered(false);
    } else if (status === 'executing') {
      if (!wait.working || wait.settling) setWait({ ...wait, working: true, settling: false });
    } else if (wait.working && !wait.settling) {
      setWait({ ...wait, settling: true });
    }
  }, [wait, status, replyId]);

  // Never picked up, never finished, or finished with nothing to say.
  useEffect(() => {
    if (!wait) return;
    const left = wait.settling
      ? SETTLE_WAIT_MS
      : (wait.working ? REPLY_WAIT_MS : PICKUP_WAIT_MS) - (Date.now() - wait.since);
    const timer = window.setTimeout(() => {
      setWait(null);
      setUnanswered(true);
    }, Math.max(0, left));
    return () => window.clearTimeout(timer);
  }, [wait]);

  const begin = useCallback(() => {
    setWait({ since: Date.now(), repliedTo: latestReplyId(messages), working: false, settling: false });
    setUnanswered(false);
  }, [messages]);
  const cancel = useCallback(() => setWait(null), []);
  return { waiting: wait !== null, unanswered, begin, cancel };
}

// ----- Turn on Talk, Apply -----

/** Both restart the employee on the server. The employee they return goes
 *  into the team cache, also on `restart_failed`, which carries it too.
 *  Every click sends its own idempotency key. Errors carry the server's code. */
function useEmployeeChange(type: 'enable_employee_talk' | 'apply_employee_changes') {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<EmployeeSummary | null, Error, string>({
    mutationFn: async (workflowId) => {
      const response = await sendRequest<{ success?: boolean; error?: string; employee?: unknown }>(
        type,
        { workflow_id: workflowId, idempotency_key: crypto.randomUUID() },
        WORKFLOW_CONTROL_REQUEST_TIMEOUT,
      );
      const employee = parseEmployee(response?.employee);
      if (employee) upsertEmployee(queryClient, employee, false);
      if (response?.success === false) throw new Error(response.error || 'failed');
      return employee;
    },
  });
}

/** Adds a talk line to the employee and restarts it. */
export function useEnableTalk() {
  return useEmployeeChange('enable_employee_talk');
}

/** Restarts the employee on its latest saved graph. */
export function useApplyChanges() {
  return useEmployeeChange('apply_employee_changes');
}

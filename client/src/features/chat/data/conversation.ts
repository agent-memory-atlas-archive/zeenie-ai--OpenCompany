/**
 * A conversation as the chat draws it: the thread read, the session's runs
 * at their freshest, the turns (thread/model.ts), and the run holding the
 * lane. Subscribes the session's run events while mounted.
 */

import { useMemo } from 'react';
import type { RunSnapshot } from '@/lib/agui/reduceRun';
import { buildTurns, knownRuns, type ChatTurn } from '../thread/model';
import { laneRun, useChatRunSubscription, useRunsSubscribed, useSessionRuns, useThreadRunReconcile } from './runs';
import type { ChatMessage } from './schemas';
import { useChatThread, type ThreadScope } from './thread';

const NO_MESSAGES: readonly ChatMessage[] = [];

export interface Conversation {
  thread: ReturnType<typeof useChatThread>;
  turns: ChatTurn[];
  /** The live run the owner's last message started: while there is one, the
   *  next message waits for it. */
  lane: RunSnapshot | null;
}

export function useConversation(sessionId: string, scope: ThreadScope): Conversation {
  useChatRunSubscription(sessionId);
  const thread = useChatThread(sessionId, scope);
  const messages = thread.data?.messages ?? NO_MESSAGES;
  const activeRuns = thread.data?.activeRuns;
  useThreadRunReconcile(sessionId, thread.data?.messages);
  const store = useSessionRuns(sessionId);
  const subscribed = useRunsSubscribed(sessionId);

  const runs = useMemo(
    () => knownRuns(sessionId, messages, store, subscribed, activeRuns),
    [sessionId, messages, store, subscribed, activeRuns],
  );
  const turns = useMemo(() => buildTurns(messages, runs), [messages, runs]);
  const lane = useMemo(() => laneRun(runs), [runs]);
  return { thread, turns, lane };
}

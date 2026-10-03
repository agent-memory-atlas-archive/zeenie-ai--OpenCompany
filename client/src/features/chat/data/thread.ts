/**
 * A chat session's thread (`get_chat_messages`), shared by every host.
 *
 * Scope `all` reads every generation (Home's employee page: a message from
 * before a Start still shows); `live` reads the live generation only (the
 * editor's chat pane). WebSocketContext invalidates the session's threads on
 * `chat.updated`, after a runtime reset and when the socket reopens; a
 * failed read is an error, never an empty thread.
 */

import { useQuery } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { STALE_TIME, queryKeys } from '@/lib/queryConfig';
import { parseThreadReply, type ChatThreadData } from './schemas';

export type ThreadScope = 'all' | 'live';

/** The newest messages a thread shows. */
export const THREAD_LIMIT = 200;

export function chatThreadKey(sessionId: string, scope: ThreadScope) {
  return [...queryKeys.chatThread.bySession(sessionId).queryKey, scope] as const;
}

export function useChatThread(sessionId: string | null, scope: ThreadScope) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ChatThreadData, Error>({
    queryKey: chatThreadKey(sessionId ?? '', scope),
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string; messages?: unknown; thread?: unknown; active_runs?: unknown }>(
        'get_chat_messages',
        { session_id: sessionId, limit: THREAD_LIMIT, all_generations: scope === 'all' },
      );
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      return parseThreadReply(reply ?? {});
    },
    enabled: isReady && Boolean(sessionId),
    // Broadcasts keep it current. Opening a thread refetches only when one
    // arrived while it was closed (it left the thread invalidated).
    staleTime: STALE_TIME.FOREVER,
    refetchOnMount: true,
  });
}

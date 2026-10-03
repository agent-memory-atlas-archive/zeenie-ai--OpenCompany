/**
 * The chat shared by Home's employee page and Dev's console Chat pane
 * (design handoff chat/): the conversation, scrolling on its own, and under
 * it the dock with the host's notices, the message box and a footnote. The
 * host (`ChatHost`) says who answers, whether a message can go now and what
 * sits around the conversation; the chat owns the rest.
 *
 * The turns follow the session's runs (docs-internal/chat_protocol.md): the
 * employee's steps and answer stream in while the run a message started is
 * going, whatever else arrives meanwhile, and the next message waits until
 * it ends. Meanwhile Send is Stop, and Esc anywhere in the pane stops the
 * answer too. A message that does not go comes back into the box.
 */

import { useCallback, useImperativeHandle, useRef, type KeyboardEvent, type Ref } from 'react';
import { cn } from '@/lib/utils';
import { useConversation } from './data/conversation';
import { useSendChatMessage, type ChatSendError } from './data/send';
import { useStopChatRun } from './data/stop';
import { Composer } from './composer/Composer';
import type { ChatHost, ChatPaneHandle } from './host';
import { useComposerStore } from './state/composerStore';
import { ChatThread } from './thread/ChatThread';

export function ChatPane({ host, ref }: { host: ChatHost; ref?: Ref<ChatPaneHandle> }) {
  const { sessionId, scope, persona, composer, notify, onSendRefused, compact = false } = host;
  const { thread, turns, lane } = useConversation(sessionId, scope);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focusComposer: () => boxRef.current?.focus() }), []);

  const onRefused = useCallback(
    (error: ChatSendError) => {
      if (onSendRefused) onSendRefused(error.code);
      else notify('Your message didn’t send. Try again.', 'error');
    },
    [notify, onSendRefused],
  );
  const send = useSendChatMessage(sessionId, scope, onRefused);
  const stop = useStopChatRun(sessionId, () => notify('Couldn’t stop the reply. Try again.', 'error'));
  const busy = send.isPending || lane !== null;
  const stopping = lane?.state === 'stopping' || stop.isPending;

  const submit = () => {
    const store = useComposerStore.getState();
    if (busy || !thread.data || !(store.drafts[sessionId]?.text ?? '').trim()) return;
    const draft = store.takeForSend(sessionId);
    send.mutate({ ...draft, text: draft.text.trim() });
  };

  const stopAnswer = () => {
    if (lane && !stopping) stop.mutate(lane);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape' || event.defaultPrevented || !lane || stopping) return;
    event.preventDefault();
    stopAnswer();
  };

  return (
    <section aria-label={`Chat with ${persona.name}`} onKeyDown={onKeyDown} className="flex min-h-0 w-full flex-1 flex-col">
      <ChatThread
        persona={persona}
        turns={turns}
        loading={thread.isPending}
        error={thread.isError && !thread.data}
        onRetry={() => void thread.refetch()}
        top={host.top}
        afterThread={host.afterThread}
        emptyState={host.emptyState}
        liveNote={host.liveNote}
        compact={compact}
        canStop={composer !== 'closed'}
        onScrolledChange={host.onScrolledChange}
      />
      <div className={cn('relative flex-none', compact ? 'border-t border-border-default px-3 py-2' : 'px-6 pb-3')}>
        <div className={cn('mx-auto flex w-full flex-col', compact ? 'gap-2' : 'max-w-(--w-chat-column) gap-2.5')}>
          {host.notices}
          {composer !== 'closed' && (
            <Composer
              sessionId={sessionId}
              name={persona.name}
              ready={Boolean(thread.data)}
              busy={busy}
              onSend={submit}
              onStop={lane ? stopAnswer : undefined}
              stopping={stopping}
              compact={compact}
              boxRef={boxRef}
            />
          )}
          {host.footnote && <p className="m-0 text-center text-xs text-fg-muted">{host.footnote}</p>}
        </div>
      </div>
    </section>
  );
}

export default ChatPane;

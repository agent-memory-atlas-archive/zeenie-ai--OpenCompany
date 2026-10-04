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
 *
 * What the employee wants to send waits for the owner on a card: on the
 * reply that made it, or after the conversation for the employee's own work
 * (data/approvals.ts). Ctrl/Cmd+Enter sends the newest one; the Ask first
 * chip beside the box changes the rule for everything they send.
 */

import { useCallback, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type Ref } from 'react';
import { cn } from '@/lib/utils';
import { ApprovalsContext, type ApprovalsValue } from './approval/context';
import { StandaloneApprovals } from './approval/StandaloneApprovals';
import { AskFirstChip } from './composer/AskFirstChip';
import {
  NO_APPROVALS,
  isOpenApproval,
  useApprovalEvents,
  useAskFirst,
  useChatApprovals,
  useDecideApproval,
  useSetAskFirst,
  type DecideInput,
} from './data/approvals';
import { useConversation } from './data/conversation';
import { liveApprovalIds, savedApprovalIds } from './data/parts';
import { useSendChatMessage, type ChatSendError } from './data/send';
import { useStopChatRun } from './data/stop';
import { useUiStateSync } from './data/uiState';
import { Composer } from './composer/Composer';
import { asksWhatItSays, type ChatUiActions } from './genui/actions';
import type { ChatHost, ChatPaneHandle } from './host';
import { newClientMessageId, useComposerStore } from './state/composerStore';
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

  // What an interface's buttons do. Read through a ref, so the handlers an
  // interface made once keep reaching the current pane.
  const uiState = useUiStateSync(sessionId);
  const live = useRef({ busy, ready: Boolean(thread.data), send: send.mutate, notify, uiState });
  useLayoutEffect(() => {
    live.current = { busy, ready: Boolean(thread.data), send: send.mutate, notify, uiState };
  });
  const uiActions = useMemo<ChatUiActions>(
    () => ({
      ask: (text, label) => {
        const pane = live.current;
        if (asksWhatItSays(text, label) && !pane.busy && pane.ready) {
          pane.send({ text, clientMessageId: newClientMessageId() });
          return;
        }
        // Not what the button says (or not now): the owner reads it first.
        useComposerStore.getState().setText(sessionId, text);
        boxRef.current?.focus();
      },
      event: (event) => {
        const pane = live.current;
        if (pane.busy || !pane.ready) {
          pane.notify(`${persona.name} is still answering. Try that again once they finish.`, 'info');
          return;
        }
        pane.uiState.flush(event.partId);
        pane.send({
          text: event.label || event.action,
          clientMessageId: newClientMessageId(),
          uiEvent: { partId: event.partId, elementId: event.elementId, action: event.action, params: event.params },
        });
      },
    }),
    [sessionId, persona.name],
  );

  // A suggested question goes as written; while the last answer is still
  // coming, it waits in the box instead.
  const followUp = useCallback(
    (text: string) => {
      const pane = live.current;
      if (!pane.busy && pane.ready) {
        pane.send({ text, clientMessageId: newClientMessageId() });
        return;
      }
      useComposerStore.getState().setText(sessionId, text);
      boxRef.current?.focus();
    },
    [sessionId],
  );

  // Drafts waiting for the owner: on the replies that made them, and the
  // rest (the employee's own work) after the conversation.
  const workflowId = sessionId === 'default' ? null : sessionId;
  useApprovalEvents();
  const approvalsQuery = useChatApprovals(workflowId);
  const approvals = approvalsQuery.data ?? NO_APPROVALS;
  const askFirstQuery = useAskFirst(workflowId);
  const setAskFirst = useSetAskFirst(workflowId ?? '', (message) => notify(message, 'error'));
  const [deciding, setDeciding] = useState<ReadonlySet<string>>(() => new Set());
  const decideMutation = useDecideApproval(workflowId ?? '', (error) => notify(error.message, 'error'));
  const decide = useCallback(
    (input: DecideInput) => {
      const id = input.approval.id;
      setDeciding((current) => new Set(current).add(id));
      decideMutation.mutate(input, {
        onSettled: () =>
          setDeciding((current) => {
            const next = new Set(current);
            next.delete(id);
            return next;
          }),
      });
    },
    [decideMutation],
  );
  const linked = useMemo(() => {
    const ids = new Set<string>();
    for (const turn of turns) {
      if (turn.kind !== 'assistant') continue;
      for (const id of savedApprovalIds(turn.message?.parts)) ids.add(id);
      for (const id of liveApprovalIds(turn.run?.activities)) ids.add(id);
    }
    return ids;
  }, [turns]);
  const standalone = useMemo(() => {
    const now = approvals.receivedAt + approvals.offsetMs;
    return [...approvals.order]
      .reverse()
      .filter((id) => !linked.has(id) && isOpenApproval(approvals.byId.get(id)!, now));
  }, [approvals, linked]);
  const approvalsValue = useMemo<ApprovalsValue>(
    () => ({ approvals, name: persona.name, askFirst: askFirstQuery.data?.askFirst ?? null, compact, decide, deciding }),
    [approvals, persona.name, askFirstQuery.data?.askFirst, compact, decide, deciding],
  );

  // Ctrl/Cmd+Enter sends the newest draft still waiting for the owner.
  const sendNewestDraft = () => {
    const id = approvals.order.find((candidate) => approvals.byId.get(candidate)?.status === 'pending');
    const approval = id ? approvals.byId.get(id) : undefined;
    if (!approval || deciding.has(approval.id)) return false;
    decide({ approval, decision: 'send' });
    return true;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey) {
      if (sendNewestDraft()) event.preventDefault();
      return;
    }
    if (event.key !== 'Escape' || !lane || stopping) return;
    event.preventDefault();
    stopAnswer();
  };

  const askFirst = askFirstQuery.data?.askFirst;
  const chips =
    workflowId && askFirstQuery.data ? (
      <AskFirstChip
        on={askFirst === true}
        name={persona.name}
        compact={compact}
        disabled={setAskFirst.isPending}
        onChange={(next) =>
          setAskFirst.mutate(next, {
            onSuccess: (result) => {
              if (result.needsApply) notify(`Apply ${persona.name}’s changes for this to cover everything.`, 'info');
            },
          })
        }
      />
    ) : null;

  return (
    <ApprovalsContext.Provider value={approvalsValue}>
    <section aria-label={`Chat with ${persona.name}`} onKeyDown={onKeyDown} className="flex min-h-0 w-full flex-1 flex-col">
      <ChatThread
        persona={persona}
        turns={turns}
        loading={thread.isPending}
        error={thread.isError && !thread.data}
        onRetry={() => void thread.refetch()}
        top={host.top}
        afterThread={
          standalone.length > 0 || host.afterThread ? (
            <>
              <StandaloneApprovals ids={standalone} />
              {host.afterThread}
            </>
          ) : null
        }
        emptyState={host.emptyState}
        liveNote={host.liveNote}
        compact={compact}
        canStop={composer !== 'closed'}
        uiActions={uiActions}
        onUiStateChange={uiState.change}
        onFollowUp={composer === 'send' ? followUp : undefined}
        onOpenArtifact={host.openArtifact}
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
              chips={chips}
            />
          )}
          {host.footnote && <p className="m-0 text-center text-xs text-fg-muted">{host.footnote}</p>}
        </div>
      </div>
    </section>
    </ApprovalsContext.Provider>
  );
}

export default ChatPane;

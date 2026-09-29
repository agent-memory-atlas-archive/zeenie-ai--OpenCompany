/**
 * Talking to an employee, under their card: the conversation, and a message
 * box pinned to the bottom of the page.
 *
 * - The thread keeps every message across restarts, with a divider where
 *   the employee restarted (they start fresh from there). Replies,
 *   questions and routine reports all land in it.
 * - While the employee runs, a message goes to them at once, and
 *   "Thinking…" holds the box until they answer (useReplyWait); overlapping
 *   runs would each save over the other's conversation. While they are
 *   paused, one message waits for Resume and then the box holds. Otherwise
 *   the box gives way to their main action (Start, or what they are
 *   missing).
 * - Without a talk line, TurnOnTalk offers to add one; an employee whose
 *   setup cannot answer gets a note.
 */

import { ArrowUp } from 'lucide-react';
import { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { prefersReducedMotion } from '@/lib/useReducedMotion';
import { cn } from '@/lib/utils';
import { invalidateEmployees } from '../data/employees';
import { talkMode, talkNoticeText } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { threadRows, timeLabel, useReplyWait, useSendTalkMessage, useTalkThread, type ThreadMessage } from '../data/talk';
import { isSendKey } from '../hire/composerKeys';
import { pillToast } from '../ui/pillToast';
import { Avatar } from '../ui/primitives';
import { useAutoGrow } from '../ui/useAutoGrow';
import { PendingChangesNotice } from './PendingChangesNotice';
import { PrimaryActionButton } from './PrimaryActionButton';
import { TurnOnTalk } from './TurnOnTalk';
import type { EmployeeControl } from './useEmployeeControl';

// Its own chunk: the markdown stack stays out of Home's first load.
const ThreadMarkdown = lazy(() => import('./ThreadMarkdown'));

/** Lines up an answer's time under its text, past the avatar. */
const PAST_AVATAR = 'pl-10.5';

function MessageRow({ message, employee, now }: { message: ThreadMessage; employee: EmployeeSummary; now: Date }) {
  const mine = message.role === 'user';
  const time = message.pending ? 'Sending…' : timeLabel(message.timestamp, now);
  const text = 'm-0 min-w-0 text-md leading-normal break-words whitespace-pre-wrap text-fg-default';
  return (
    <div data-message={message.id} className={cn('flex flex-col gap-1', mine ? 'items-end' : 'items-start')}>
      {mine ? (
        <p className={cn(text, 'max-w-4/5 rounded-card border border-border-default bg-bg-panel px-3.5 py-2.5')}>{message.message}</p>
      ) : (
        <div className="flex w-full gap-3">
          <Avatar name={employee.name} colorRole={employee.color_role} size="sm" />
          <Suspense fallback={<p className={text}>{message.message}</p>}>
            <ThreadMarkdown text={message.message} />
          </Suspense>
        </div>
      )}
      {time && <span className={cn('font-mono text-2xs text-fg-faint', !mine && PAST_AVATAR)}>{time}</span>}
    </div>
  );
}

function RestartDivider({ name }: { name: string }) {
  return (
    <div className="flex items-center gap-3 py-1 text-xs text-fg-muted">
      <span aria-hidden className="h-px flex-1 bg-border-default" />
      <span className="text-center">{name} restarted — they start fresh from here</span>
      <span aria-hidden className="h-px flex-1 bg-border-default" />
    </div>
  );
}

function TalkBox({
  name,
  value,
  onChange,
  onSend,
  canSend,
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  canSend: boolean;
}) {
  const boxRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(boxRef, value);

  // Enter sends, Shift+Enter is a new line; while the box holds, Enter waits.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isSendKey(event)) return;
    event.preventDefault();
    if (canSend) onSend();
  };

  return (
    <div className="flex w-full items-end gap-2 rounded-composer border border-border-default bg-bg-panel py-2 pr-2 pl-4 shadow-float transition-colors duration-(--dur-slow) focus-within:border-border-strong">
      <Textarea
        ref={boxRef}
        variant="bare"
        rows={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        aria-label={`Message ${name}`}
        placeholder={`Message ${name}…`}
        className="max-h-(--h-composer-max) overflow-hidden py-1.5 text-md leading-normal text-fg-default field-sizing-fixed"
      />
      <Button variant="invert" size="icon" disabled={!canSend} onClick={onSend} aria-label="Send" title="Send" className="shrink-0 rounded-full">
        <ArrowUp aria-hidden strokeWidth={2.25} />
      </Button>
    </div>
  );
}

function Conversation({ employee, control }: { employee: EmployeeSummary; control: EmployeeControl }) {
  const { workflow_id: workflowId, name } = employee;
  const queryClient = useQueryClient();
  const thread = useTalkThread(workflowId);
  const messages = thread.data;
  const send = useSendTalkMessage(workflowId);
  const { waiting, unanswered, begin, cancel } = useReplyWait(workflowId, employee.talk.agent_node_id, messages);
  const mode = talkMode(employee.control);
  const [draft, setDraft] = useState('');
  const [queued, setQueued] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // A message that waited for Resume reaches the employee when they run
  // again; a reset drops it.
  useEffect(() => {
    if (!queued || mode === 'queue') return;
    setQueued(false);
    if (mode === 'send') begin();
  }, [queued, mode, begin]);

  // Keep the newest message in view as the conversation grows. Opening the
  // page leaves the card in view.
  const lastId = messages?.at(-1)?.id ?? null;
  const shownLast = useRef<string | null | undefined>(undefined);
  useLayoutEffect(() => {
    if (!messages) return;
    const previous = shownLast.current;
    shownLast.current = lastId;
    if (previous === undefined || previous === lastId) return;
    endRef.current?.scrollIntoView?.({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'end' });
  }, [messages, lastId]);

  const text = draft.trim();
  const holding = waiting || queued || send.isPending;
  const canSend = text.length > 0 && !holding && messages !== undefined;

  const submit = () => {
    if (!canSend) return;
    setDraft('');
    const goesNow = mode === 'send';
    if (goesNow) begin();
    send.mutate(text, {
      onSuccess: (delivery) => {
        if (delivery === 'queued') {
          cancel();
          setQueued(true);
        } else if (!goesNow) {
          begin();
        }
      },
      onError: (error) => {
        cancel();
        setDraft((current) => current || text);
        if (error.message === 'not_running') invalidateEmployees(queryClient);
        pillToast(error.message === 'not_running' ? `${name} isn’t running. Start them first.` : 'Your message didn’t send. Try again.', {
          tone: 'error',
        });
      },
    });
  };

  const rows = messages ? threadRows(messages) : [];
  const notice = talkNoticeText(mode, name, queued);
  const now = new Date();

  return (
    <section aria-label={`Talk with ${name}`} className="flex w-full flex-1 flex-col">
      {thread.isPending ? (
        <div aria-busy className="flex flex-col gap-3 py-2">
          <Skeleton className="h-10 w-3/5 self-end rounded-card" />
          <Skeleton className="h-16 w-4/5 rounded-card" />
        </div>
      ) : (
        thread.isError && (
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <p className="m-0 text-sm text-fg-default">Couldn’t load the conversation.</p>
            <Button variant="quiet" onClick={() => void thread.refetch()} className="border-border-default text-fg-default">
              Try again
            </Button>
          </div>
        )
      )}
      {/* A log: screen readers announce new messages and "Thinking…". */}
      <div role="log" aria-live="polite" aria-label={`Conversation with ${name}`} className="flex flex-col gap-4">
        {rows.map((row) =>
          row.kind === 'restart' ? (
            <RestartDivider key={row.key} name={name} />
          ) : (
            <MessageRow key={row.message.id} message={row.message} employee={employee} now={now} />
          ),
        )}
        {waiting && (
          <div className="flex items-center gap-3">
            <Avatar name={name} colorRole={employee.color_role} size="sm" />
            <span className="text-sm text-fg-muted">Thinking…</span>
          </div>
        )}
        {unanswered && !waiting && <p className={cn('m-0 text-xs text-fg-muted', PAST_AVATAR)}>No answer from {name} yet.</p>}
      </div>
      <div className="sticky bottom-0 z-10 mt-auto flex flex-col gap-2.5 bg-bg-app pt-4 pb-3">
        {employee.pending_changes && <PendingChangesNotice employee={employee} />}
        {notice && (
          <div className="flex flex-wrap items-center gap-3">
            <p className="m-0 min-w-50 flex-1 text-sm text-fg-muted">{notice}</p>
            <PrimaryActionButton control={control} />
          </div>
        )}
        {mode !== 'start' && <TalkBox name={name} value={draft} onChange={setDraft} onSend={submit} canSend={canSend} />}
      </div>
      <div ref={endRef} aria-hidden />
    </section>
  );
}

export function EmployeeTalk({ employee, control }: { employee: EmployeeSummary; control: EmployeeControl }) {
  if (employee.talk.state === 'off') return <TurnOnTalk employee={employee} />;
  if (employee.talk.state === 'unsupported') {
    return (
      <p className="m-0 w-full rounded-card border border-border-default bg-bg-panel px-4 py-3 text-center text-sm text-fg-muted">
        You can’t message {employee.name} here. Their setup has no way to answer you.
      </p>
    );
  }
  return <Conversation employee={employee} control={control} />;
}

export default EmployeeTalk;

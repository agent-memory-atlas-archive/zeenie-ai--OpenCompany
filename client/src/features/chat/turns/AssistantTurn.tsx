/**
 * The employee's side of a turn (design handoff chat, "Assistant row"):
 * their avatar on the left and, with no bubble, what they said in markdown.
 * While their run works the avatar spins its ring and skeleton lines stand
 * in for text that has not come yet, with a status line under; a run that
 * waits for Resume, failed or was stopped says so where its answer would be.
 *
 * `chat-msg chat-msg-bot` (on the text only) is the theme hook the stylized
 * themes paint as a bubble.
 */

import { CircleAlert, Pause, Square } from 'lucide-react';
import { Suspense, lazy, type ReactNode } from 'react';
import { isLiveRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '../data/schemas';
import type { ChatPersona } from '../host';
import { ChatAvatar } from '../thread/ChatAvatar';
import { timeLabel } from '../thread/timeLabel';
import { failureLines, liveLabel, streamedText } from './runCopy';
import { StatusLine } from './StatusLine';
import { TurnMeta } from './TurnMeta';

// Its own chunk: the markdown stack stays out of the first load.
const ReplyMarkdown = lazy(() => import('../markdown/ReplyMarkdown'));

function Thinking() {
  return (
    <div aria-hidden data-thinking className="flex flex-col gap-2.25 pt-0.5">
      <span className="opencompany-shimmer h-3 w-[78%] rounded-md" />
      <span className="opencompany-shimmer h-3 w-[92%] rounded-md [animation-delay:120ms]" />
      <span className="opencompany-shimmer h-3 w-[54%] rounded-md [animation-delay:240ms]" />
    </div>
  );
}

function Note({ icon, tone = 'muted', children }: { icon: 'alert' | 'pause' | 'stop'; tone?: 'muted' | 'alert'; children: ReactNode }) {
  const Icon = icon === 'alert' ? CircleAlert : icon === 'pause' ? Pause : Square;
  return (
    <div className="flex items-start gap-2 text-sm">
      <Icon aria-hidden className={cn('mt-0.75 size-3.5 shrink-0', tone === 'alert' ? 'text-action-stop-ink' : 'text-fg-faint')} />
      <div className="flex min-w-0 flex-col gap-0.5 text-fg-muted">{children}</div>
    </div>
  );
}

export function AssistantTurn({
  message,
  run,
  persona,
  now,
  latest,
  compact,
  liveNote,
}: {
  message: ChatMessage | null;
  run: RunSnapshot | null;
  persona: ChatPersona;
  now: Date;
  latest: boolean;
  compact: boolean;
  /** Said on the status line while the run works (an automatic retry). */
  liveNote?: string | null;
}) {
  const queued = run?.state === 'queued';
  const live = Boolean(run && isLiveRun(run) && !queued);
  const text = message ? message.text : streamedText(run);
  const failure = run?.state === 'error' ? failureLines(run.error, persona.name) : null;

  return (
    <div
      data-turn="assistant"
      data-message={message?.id}
      data-run={run?.runId}
      aria-busy={live || undefined}
      className={cn('chat-turn-bot group/turn flex items-start', compact ? 'gap-2.5' : 'gap-3.5')}
    >
      <ChatAvatar persona={persona} live={live} compact={compact} />
      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {live && !text && <Thinking />}
        {text && (
          <div className={cn('chat-msg chat-msg-bot text-fg-default wrap-anywhere', compact ? 'leading-normal' : 'text-md leading-relaxed')}>
            <Suspense fallback={<p className="m-0 whitespace-pre-wrap">{text}</p>}>
              <ReplyMarkdown text={text} />
            </Suspense>
          </div>
        )}
        {live && run && (
          <StatusLine label={liveLabel(run, Boolean(text))} note={liveNote} compact={compact} />
        )}
        {queued && (
          <Note icon="pause">
            <p className="m-0">Waiting for you to resume {persona.name}.</p>
          </Note>
        )}
        {run?.state === 'stopped' && (
          <Note icon="stop">
            <p className="m-0">You stopped this reply.</p>
          </Note>
        )}
        {failure && (
          <Note icon="alert" tone="alert">
            <p className="m-0 text-fg-default">{failure.headline}</p>
            {failure.detail && <p className="m-0">{failure.detail}</p>}
            {run?.error?.hint && <p className="m-0">{run.error.hint}</p>}
          </Note>
        )}
        {message && <TurnMeta shown={latest}>{timeLabel(message.timestamp, now)}</TurnMeta>}
      </div>
    </div>
  );
}

/**
 * The employee's side of a turn (design handoff chat, "Assistant row"):
 * their avatar on the left and, with no bubble, in order: what they did on
 * the way (the steps disclosure), what they said in markdown, any interface
 * they showed (GeneratedUiBlock), and anything they want to send, waiting for
 * the owner (ApprovalCard).
 *
 * While their run works the avatar spins its ring; skeleton lines stand in
 * for text that has not come yet; the answer streams in with a caret after
 * it; and a status line says "Thinking" or "Writing · N tok/s" and that Esc
 * stops it. Text written beside tool calls shows muted until the answer
 * replaces it. A run that waits for Resume, failed or was stopped says so
 * where its answer would be.
 *
 * `chat-msg chat-msg-bot` (on the text only) is the theme hook the stylized
 * themes paint as a bubble.
 */

import { CircleAlert, Pause, Square } from 'lucide-react';
import { Suspense, lazy, useMemo, type ReactNode } from 'react';
import { isLiveRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import type { UiStateChange } from '@/lib/jsonRender/uiState';
import { cn } from '@/lib/utils';
import { ApprovalCard } from '../approval/ApprovalCard';
import { liveApprovalIds, savedApprovalIds, savedFollowups, savedSources, savedUiParts, type SourceItem, type UiPart } from '../data/parts';
import type { ChatMessage } from '../data/schemas';
import type { ChatUiActions } from '../genui/actions';
import type { ChatPersona } from '../host';
import { CitationContext, NO_CITATIONS, type CitationInfo } from '../markdown/citationContext';
import { citationOrder } from '../markdown/citations';
import { ChatAvatar } from '../thread/ChatAvatar';
import type { TurnWork } from '../thread/model';
import { timeLabel } from '../thread/timeLabel';
import { GeneratedUiBlock } from './GeneratedUiBlock';
import { FollowUps } from './FollowUps';
import { failureLines, liveLabel, liveText } from './runCopy';
import { SourceChips } from './SourceChips';
import { StatusLine } from './StatusLine';
import { StepsDisclosure } from './StepsDisclosure';
import { TurnMeta } from './TurnMeta';
import { useWritingRate } from './useWritingRate';

const NO_UI: UiPart[] = [];

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
  work,
  liveUi = NO_UI,
  sources,
  persona,
  now,
  latest,
  compact,
  liveNote,
  canStop = false,
  uiActions,
  onUiStateChange,
  onFollowUp,
}: {
  message: ChatMessage | null;
  run: RunSnapshot | null;
  work: TurnWork | null;
  /** Interfaces the run streamed, until the saved reply carries them. */
  liveUi?: UiPart[];
  /** The conversation's sources as of this answer, by number (the thread's
   *  `ChatTurn.sources`); without them, the answer's own. */
  sources?: ReadonlyMap<number, SourceItem>;
  persona: ChatPersona;
  now: Date;
  latest: boolean;
  compact: boolean;
  /** Said on the status line while the run works (an automatic retry). */
  liveNote?: string | null;
  /** The pane stops a working run on Esc, so the status line says so. */
  canStop?: boolean;
  /** What the buttons of an interface in the reply do. */
  uiActions?: ChatUiActions;
  onUiStateChange?: (partId: string, changes: UiStateChange[]) => void;
  /** Sends a suggested next question; absent where they cannot go. */
  onFollowUp?: (text: string) => void;
}) {
  const queued = run?.state === 'queued';
  const live = Boolean(run && isLiveRun(run) && !queued);
  // A saved answer is the answer; until then, what the run streamed.
  const streamed = liveText(message ? null : run);
  const text = message ? message.text : streamed.text;
  const streaming = live && streamed.streaming;
  const narration = streamed.narration;
  const rate = useWritingRate(text, streaming, now);
  const failure = run?.state === 'error' ? failureLines(run.error, persona.name) : null;
  const saved = message ? savedUiParts(message.parts) : NO_UI;
  const interfaces = saved.length > 0 ? saved : liveUi;
  // The drafts it made: named on the saved reply, or arriving with the run.
  const approvals = useMemo(
    () => [...new Set([...savedApprovalIds(message?.parts), ...liveApprovalIds(run?.activities)])],
    [message?.parts, run?.activities],
  );
  // Only under the latest answer, once it is done.
  const followups = message && latest && !live && onFollowUp ? savedFollowups(message.parts) : [];
  const citations = useMemo<CitationInfo>(() => {
    if (!message) return NO_CITATIONS;
    const known = sources ?? new Map(savedSources(message.parts).map((source) => [source.n, source] as const));
    if (known.size === 0) return NO_CITATIONS;
    return { sources: known, order: citationOrder(text, new Set(known.keys())) };
  }, [message, text, sources]);

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
        {work && <StepsDisclosure work={work} compact={compact} />}
        {live && !text && <Thinking />}
        {text && (
          <div
            data-narration={narration || undefined}
            className={cn(
              'chat-msg chat-msg-bot wrap-anywhere',
              narration ? 'text-fg-muted' : 'text-fg-default',
              compact ? 'leading-normal' : 'text-md leading-relaxed',
            )}
          >
            <Suspense fallback={<p className="m-0 whitespace-pre-wrap">{text}</p>}>
              <CitationContext.Provider value={citations}>
                <ReplyMarkdown text={text} streaming={streaming} />
              </CitationContext.Provider>
            </Suspense>
          </div>
        )}
        {uiActions &&
          interfaces.map((part) => (
            <GeneratedUiBlock
              key={part.partId}
              part={part}
              live={!message && live}
              actions={uiActions}
              onStateChange={onUiStateChange}
            />
          ))}
        {approvals.map((id) => (
          <ApprovalCard key={id} approvalId={id} />
        ))}
        {live && run && (
          <StatusLine
            label={liveLabel(run, streaming)}
            rate={rate}
            note={liveNote}
            stopHint={canStop && run.state !== 'stopping'}
            compact={compact}
          />
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
        {citations.order.size > 0 && <SourceChips sources={citations.sources} order={citations.order} />}
        {followups.length > 0 && onFollowUp && <FollowUps items={followups} onPick={onFollowUp} />}
        {message && <TurnMeta shown={latest}>{timeLabel(message.timestamp, now)}</TurnMeta>}
      </div>
    </div>
  );
}

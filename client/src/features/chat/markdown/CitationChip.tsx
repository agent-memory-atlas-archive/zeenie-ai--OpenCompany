/**
 * A citation in an answer's text (design handoff chat, "[n] source chips"):
 * a small numbered chip in the save tint, numbered as the reply numbers its
 * sources. It names the source on hover and focus, and opens it in a new
 * tab when the source has an address.
 */

import { useContext } from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { CitationContext } from './citationContext';

const CHIP =
  'mx-0.5 inline-grid h-4.25 min-w-4.25 place-items-center rounded-pill bg-action-save-soft px-1 align-[2px] font-mono text-2xs font-semibold text-action-save-ink no-underline';

function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return null;
  }
}

export function CitationChip({ n }: { n: number }) {
  const { order, sources } = useContext(CitationContext);
  const shown = order.get(n);
  const source = sources.get(n);
  if (shown === undefined || !source) return <>[{n}]</>;
  const host = hostOf(source.url);
  const label = `Source ${shown}: ${source.title}${host ? ` (${host})` : ''}`;
  const chip = source.url ? (
    <a href={source.url} target="_blank" rel="noopener noreferrer" aria-label={label} className={CHIP}>
      {shown}
    </a>
  ) : (
    <span role="note" aria-label={label} tabIndex={0} className={CHIP}>
      {shown}
    </span>
  );
  // Its own provider: the chat also renders outside the app shell's.
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{chip}</TooltipTrigger>
        <TooltipContent className="max-w-72">
          <span className="font-medium">{source.title}</span>
          {host && <span className="block text-fg-muted">{host}</span>}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

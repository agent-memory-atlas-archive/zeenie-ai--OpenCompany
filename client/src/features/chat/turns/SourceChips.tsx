/**
 * The sources an answer cites, under it (design handoff chat, "sources"):
 * one chip per cited source, numbered as the text's citation chips are, with
 * the site and the title, opening the source in a new tab.
 */

import { cn } from '@/lib/utils';
import type { SourceItem } from '../data/parts';

function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return null;
  }
}

export function SourceChips({ sources, order }: { sources: ReadonlyMap<number, SourceItem>; order: ReadonlyMap<number, number> }) {
  const cited = [...order.entries()]
    .sort((a, b) => a[1] - b[1])
    .flatMap(([n, shown]) => {
      const source = sources.get(n);
      return source ? [{ source, shown }] : [];
    });
  if (cited.length === 0) return null;
  return (
    <ul aria-label="Sources" className="m-0 flex list-none flex-wrap gap-1.5 p-0">
      {cited.map(({ source, shown }) => {
        const host = hostOf(source.url);
        const body = (
          <>
            <span className="grid h-4.25 min-w-4.25 shrink-0 place-items-center rounded-pill bg-action-save-soft px-1 font-mono text-2xs font-semibold text-action-save-ink">
              {shown}
            </span>
            {host && <span className="shrink-0 text-fg-muted">{host}</span>}
            <span className="min-w-0 truncate text-fg-default">{source.title}</span>
          </>
        );
        const chip = 'flex h-7 max-w-72 items-center gap-1.5 rounded-pill border border-border-default bg-bg-panel pr-2.5 pl-1.5 text-xs';
        return (
          <li key={source.n} className="min-w-0">
            {source.url ? (
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                title={source.title}
                className={cn(chip, 'no-underline transition-colors duration-(--dur-default) hover:border-border-strong hover:bg-bg-hover')}
              >
                {body}
              </a>
            ) : (
              <span title={source.title} className={chip}>
                {body}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

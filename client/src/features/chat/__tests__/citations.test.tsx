/**
 * Citations and sources in an answer: `[n]` of a source the reply has reads
 * as a chip numbered in the order the reply cites (code left alone), the
 * cited sources are listed under the answer, and a reply with no sources is
 * drawn as written.
 */

import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { savedSources } from '../data/parts';
import ReplyMarkdown from '../markdown/ReplyMarkdown';
import { CitationContext } from '../markdown/citationContext';
import { citationOrder, citedNumber, linkCitations } from '../markdown/citations';
import { SourceChips } from '../turns/SourceChips';

const known = new Set([7, 9, 12]);

describe('citations', () => {
  it('numbers cited sources in the order the text first cites them', () => {
    const order = citationOrder('Ana is free [9]. Jordan too [7][9]. Not a source [3].', known);
    expect([...order]).toEqual([
      [9, 1],
      [7, 2],
    ]);
  });

  it('links citations outside code only, and leaves real links alone', () => {
    const text = 'See [7] and `[9]` and\n```\n[12]\n```\n[link](https://x.test) [12](https://y.test) [3]';
    expect(linkCitations(text, known)).toBe(
      'See [7](#cite-7) and `[9]` and\n```\n[12]\n```\n[link](https://x.test) [12](https://y.test) [3]',
    );
    expect(linkCitations('Plain [7]', new Set())).toBe('Plain [7]');
    expect(citationOrder('`[7]` only in code', known).size).toBe(0);
  });

  it('reads a citation link back', () => {
    expect(citedNumber('#cite-12')).toBe(12);
    expect(citedNumber('#cite-x')).toBeNull();
    expect(citedNumber('https://x.test')).toBeNull();
    expect(citedNumber(undefined)).toBeNull();
  });
});

describe('savedSources', () => {
  it('keeps one usable source per number', () => {
    expect(
      savedSources({
        sources: [
          { n: 7, title: 'Salon hours', url: 'https://salon.test/hours' },
          { n: 7, title: 'Duplicate' },
          { n: 8, title: '', url: 'javascript:alert(1)' },
          { n: 0, title: 'Bad number' },
          'nope',
        ],
      }),
    ).toEqual([
      { n: 7, title: 'Salon hours', url: 'https://salon.test/hours', detail: null },
      { n: 8, title: 'Source 8', url: null, detail: null },
    ]);
  });
});

describe('a reply that cites', () => {
  const sources = new Map([
    [7, { n: 7, title: 'Salon hours', url: 'https://www.salon.test/hours', detail: null }],
    [9, { n: 9, title: 'Ana’s calendar', url: 'https://cal.test/ana', detail: null }],
  ]);
  const order = new Map([
    [9, 1],
    [7, 2],
  ]);

  it('draws each citation as a chip with the reply’s number', () => {
    render(
      <CitationContext.Provider value={{ sources, order }}>
        <ReplyMarkdown text="Ana is free [9], the salon opens at 9 [7]. Elsewhere [3]." />
      </CitationContext.Provider>,
    );
    const ana = screen.getByRole('link', { name: 'Source 1: Ana’s calendar (cal.test)' });
    expect(ana).toHaveTextContent('1');
    expect(ana).toHaveAttribute('href', 'https://cal.test/ana');
    expect(ana).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: 'Source 2: Salon hours (salon.test)' })).toHaveTextContent('2');
    expect(screen.getByText(/Elsewhere \[3\]\./)).toBeInTheDocument();
  });

  it('lists the cited sources under the answer, in the same order', () => {
    render(<SourceChips sources={sources} order={order} />);
    const items = within(screen.getByRole('list', { name: 'Sources' })).getAllByRole('link');
    expect(items.map((item) => item.textContent)).toEqual(['1cal.testAna’s calendar', '2salon.testSalon hours']);
  });

  it('draws a reply without sources as written', () => {
    render(<ReplyMarkdown text="Nothing cited [1]." />);
    expect(screen.getByText('Nothing cited [1].')).toBeInTheDocument();
  });
});

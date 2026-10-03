/**
 * The sources a reply's text may cite, for ReplyMarkdown's citation chips:
 * each cited source number with its display number in this reply, and the
 * source itself. Set by the reply turn; empty where nothing is cited.
 */

import { createContext } from 'react';
import type { SourceItem } from '../data/parts';

export interface CitationInfo {
  /** Source number -> display number in this reply (1, 2, ...). */
  order: ReadonlyMap<number, number>;
  /** Source number -> source. */
  sources: ReadonlyMap<number, SourceItem>;
}

export const NO_CITATIONS: CitationInfo = { order: new Map(), sources: new Map() };

export const CitationContext = createContext<CitationInfo>(NO_CITATIONS);

/**
 * What a chat's approval cards share (set by ChatPane): the session's drafts,
 * who answers, the Ask first rule, and how to decide a draft. A card outside
 * a chat renders nothing.
 */

import { createContext } from 'react';
import type { ChatApprovals, DecideInput } from '../data/approvals';

export interface ApprovalsValue {
  approvals: ChatApprovals;
  /** Who drafted them ("Maya wants to send ..."). */
  name: string;
  /** The workflow's live rule; null when it has none. */
  askFirst: boolean | null;
  compact: boolean;
  decide: (input: DecideInput) => void;
  /** Drafts with a decision on its way to the server. */
  deciding: ReadonlySet<string>;
}

export const ApprovalsContext = createContext<ApprovalsValue | null>(null);

/**
 * What the owner can do to a saved turn (design handoff chat, "Thread"):
 * edit their message, move between the versions of a message or of an
 * answer, try the latest answer again, rate an answer. ChatPane provides it;
 * turns without it (a host that shows the chat only) offer none of this,
 * and Copy still works.
 */

import { createContext, useContext } from 'react';
import type { Feedback } from '../data/schemas';

export interface TurnActions {
  sessionId: string;
  /** The owner's message being edited, if any. */
  editingId: string | null;
  /** A message is on its way, the employee is answering, or the
   *  conversation is changing: nothing else may change it now. */
  busy: boolean;
  startEdit: (messageId: string) => void;
  cancelEdit: () => void;
  saveEdit: (messageId: string, text: string) => void;
  switchTo: (messageId: string) => void;
  regenerate: (messageId: string) => void;
  rate: (messageId: string, value: Feedback | null) => void;
}

export const TurnActionsContext = createContext<TurnActions | null>(null);

export function useTurnActions(): TurnActions | null {
  return useContext(TurnActionsContext);
}

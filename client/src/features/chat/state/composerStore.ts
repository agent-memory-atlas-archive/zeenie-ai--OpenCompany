/**
 * What the owner is writing, per chat session. It lives outside the chat's
 * components, so switching to another employee and back keeps an unsent
 * message.
 *
 * Sending takes the draft out of the box at once (`takeForSend`); a send that
 * does not go through puts it back (`restore`), unless the owner has started
 * another message meanwhile. A draft carries the `clientMessageId` that names
 * its send: a send that failed in transit keeps it, so sending the same text
 * again is the same message on the server (`send_chat_message` answers with
 * the one it may already have saved). Editing the text forgets the id: it
 * names that text, not the box.
 */

import { create } from 'zustand';

export interface ComposerDraft {
  text: string;
  clientMessageId: string | null;
}

export type TakenDraft = ComposerDraft & { clientMessageId: string };

interface ComposerState {
  drafts: Record<string, ComposerDraft>;
  setText: (sessionId: string, text: string) => void;
  /** Empty the box and return what it held, with the id naming this send. */
  takeForSend: (sessionId: string) => TakenDraft;
  /** Put a draft that did not go through back, if the box is still empty.
   *  `keepId`: it failed in transit, so a retry is the same message. */
  restore: (sessionId: string, draft: TakenDraft, keepId: boolean) => void;
}

const EMPTY: ComposerDraft = { text: '', clientMessageId: null };

function newClientMessageId(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export const useComposerStore = create<ComposerState>((set, get) => ({
  drafts: {},

  setText: (sessionId, text) =>
    set((state) => {
      const draft = state.drafts[sessionId] ?? EMPTY;
      if (draft.text === text) return state;
      return { drafts: { ...state.drafts, [sessionId]: { text, clientMessageId: null } } };
    }),

  takeForSend: (sessionId) => {
    const draft = get().drafts[sessionId] ?? EMPTY;
    const taken: TakenDraft = { text: draft.text, clientMessageId: draft.clientMessageId ?? newClientMessageId() };
    set((state) => ({ drafts: { ...state.drafts, [sessionId]: EMPTY } }));
    return taken;
  },

  restore: (sessionId, draft, keepId) =>
    set((state) => {
      if ((state.drafts[sessionId] ?? EMPTY).text.trim()) return state;
      const restored: ComposerDraft = { text: draft.text, clientMessageId: keepId ? draft.clientMessageId : null };
      return { drafts: { ...state.drafts, [sessionId]: restored } };
    }),
}));

export function useComposerDraft(sessionId: string): ComposerDraft {
  return useComposerStore((state) => state.drafts[sessionId] ?? EMPTY);
}

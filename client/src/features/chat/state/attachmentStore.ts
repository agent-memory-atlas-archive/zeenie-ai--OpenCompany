/**
 * Files waiting in a chat's message box, per session (design handoff chat,
 * "Composer"), beside the text in composerStore. Each is uploaded into the
 * workflow's workspace as soon as it is added (composer/attachments.ts); a
 * message carries the references of the ones that finished. Sending takes
 * them out of the box (`take`), and a send that does not go through puts
 * them back (`restore`).
 */

import { create } from 'zustand';
import type { WorkspaceFileRef } from '@/types/workspaceFiles';

export interface BoxAttachment {
  id: string;
  name: string;
  size: number;
  mime: string;
  state: 'uploading' | 'ready' | 'failed';
  /** What the upload returned, once it has. */
  ref: WorkspaceFileRef | null;
  error: string | null;
  /** An object URL for an image's thumbnail while it is in the box. */
  preview: string | null;
}

interface AttachmentState {
  boxes: Record<string, BoxAttachment[]>;
  add: (sessionId: string, items: BoxAttachment[]) => void;
  update: (sessionId: string, id: string, patch: Partial<BoxAttachment>) => void;
  remove: (sessionId: string, id: string) => void;
  /** Empty the box and return what it held. */
  take: (sessionId: string) => BoxAttachment[];
  /** Take the finished uploads for a send; the rest (still uploading,
   *  failed) stay in the box. */
  takeReady: (sessionId: string) => BoxAttachment[];
  /** Put attachments a send did not take back, ahead of any added since. */
  restore: (sessionId: string, items: BoxAttachment[]) => void;
}

const NONE: BoxAttachment[] = [];

function revoke(item: BoxAttachment): void {
  if (item.preview && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(item.preview);
}

export const useAttachmentStore = create<AttachmentState>((set, get) => ({
  boxes: {},
  add: (sessionId, items) =>
    set((state) => ({ boxes: { ...state.boxes, [sessionId]: [...(state.boxes[sessionId] ?? NONE), ...items] } })),
  update: (sessionId, id, patch) =>
    set((state) => {
      const box = state.boxes[sessionId];
      if (!box?.some((item) => item.id === id)) return state;
      return { boxes: { ...state.boxes, [sessionId]: box.map((item) => (item.id === id ? { ...item, ...patch } : item)) } };
    }),
  remove: (sessionId, id) =>
    set((state) => {
      const box = state.boxes[sessionId] ?? NONE;
      const gone = box.find((item) => item.id === id);
      if (!gone) return state;
      revoke(gone);
      return { boxes: { ...state.boxes, [sessionId]: box.filter((item) => item.id !== id) } };
    }),
  take: (sessionId) => {
    const box = get().boxes[sessionId] ?? NONE;
    if (box.length) set((state) => ({ boxes: { ...state.boxes, [sessionId]: NONE } }));
    return box;
  },
  takeReady: (sessionId) => {
    const box = get().boxes[sessionId] ?? NONE;
    const ready = box.filter((item) => item.state === 'ready' && item.ref);
    if (ready.length) set((state) => ({ boxes: { ...state.boxes, [sessionId]: box.filter((item) => !ready.includes(item)) } }));
    return ready;
  },
  restore: (sessionId, items) =>
    set((state) => ({ boxes: { ...state.boxes, [sessionId]: [...items, ...(state.boxes[sessionId] ?? NONE)] } })),
}));

export function useBoxAttachments(sessionId: string): BoxAttachment[] {
  return useAttachmentStore((state) => state.boxes[sessionId] ?? NONE);
}

/**
 * Adding files to a chat's message box: the file picker, a paste and a drop
 * all call `addAttachments`, so each reaches the same box the same way (a
 * drop needs a pointer alternative that does what it does: the picker,
 * CLAUDE.md rule 9). Each file goes into the box at once, marked uploading,
 * and is uploaded into the workflow's workspace (lib/workspaceUpload: under
 * `uploads/`, at most 25 MB); the box shows a failure on its chip. At most
 * `MAX_ATTACHMENTS` per message, the server's limit too.
 */

import { MEDIA_MAX_UPLOAD_BYTES, uploadToWorkspace } from '@/lib/workspaceUpload';
import type { WorkspaceFileRef } from '@/types/workspaceFiles';
import { newClientMessageId } from '../state/composerStore';
import { useAttachmentStore, type BoxAttachment } from '../state/attachmentStore';

/** The most files one message carries (`services/chat/attachments.py`). */
export const MAX_ATTACHMENTS = 6;

export type Upload = (file: File, workflowId: string) => Promise<WorkspaceFileRef>;

const defaultUpload: Upload = async (file, workflowId) => (await uploadToWorkspace(file, workflowId)) as WorkspaceFileRef;

/** Add files to the box; returns what could not be added and why. */
export async function addAttachments(
  sessionId: string,
  workflowId: string,
  files: readonly File[],
  upload: Upload = defaultUpload,
): Promise<string | null> {
  const store = useAttachmentStore.getState();
  const room = MAX_ATTACHMENTS - (store.boxes[sessionId]?.length ?? 0);
  const taken = files.slice(0, Math.max(0, room));
  const items: BoxAttachment[] = taken.map((file) => {
    const tooLarge = file.size > MEDIA_MAX_UPLOAD_BYTES;
    return {
      id: newClientMessageId(),
      name: file.name || 'pasted file',
      size: file.size,
      mime: file.type || 'application/octet-stream',
      state: tooLarge ? 'failed' : 'uploading',
      ref: null,
      error: tooLarge ? `Larger than ${MEDIA_MAX_UPLOAD_BYTES / (1024 * 1024)} MB` : null,
      preview:
        !tooLarge && file.type.startsWith('image/') && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : null,
    };
  });
  if (items.length) store.add(sessionId, items);
  await Promise.all(
    items.map(async (item, index) => {
      if (item.state !== 'uploading') return;
      try {
        const ref = await upload(taken[index], workflowId);
        useAttachmentStore.getState().update(sessionId, item.id, { state: 'ready', ref });
      } catch (error) {
        const message = error instanceof Error && error.message ? error.message : 'Upload failed';
        useAttachmentStore.getState().update(sessionId, item.id, { state: 'failed', error: message });
      }
    }),
  );
  return files.length > taken.length ? `Up to ${MAX_ATTACHMENTS} files go with one message.` : null;
}

/** The files a clipboard or a drop carries. */
export function filesOf(list: { files?: FileList | null } | null | undefined): File[] {
  return list?.files ? Array.from(list.files) : [];
}

/** The size as the chip says it. */
export function sizeLabel(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** The file's extension, as its chip's badge (`PDF`, `CSV`). */
export function extensionLabel(name: string): string {
  const dot = name.lastIndexOf('.');
  return (dot > 0 ? name.slice(dot + 1) : 'file').slice(0, 4).toUpperCase();
}

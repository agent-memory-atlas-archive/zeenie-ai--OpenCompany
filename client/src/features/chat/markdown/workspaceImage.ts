/**
 * Which image addresses in a reply may load without asking: only this app's
 * own workspace file route, which serves nothing but images, audio and video
 * inline (services/media/preview.py on the server).
 */

import { buildApiUrl } from '@/config/api';

const WORKSPACE_PREFIX = '/api/workspace/';

function apiOrigin(): string {
  return new URL(buildApiUrl('/'), window.location.origin).origin;
}

/** True when `src` points at this app's workspace file route. */
export function isWorkspaceImage(src: string | undefined): boolean {
  if (!src) return false;
  try {
    const origin = apiOrigin();
    const url = new URL(src, origin);
    return url.origin === origin && url.pathname.startsWith(WORKSPACE_PREFIX);
  } catch {
    return false;
  }
}

/** The host to name on the "Show image from …" button. */
export function imageHost(src: string): string {
  try {
    return new URL(src, window.location.origin).host || src;
  } catch {
    return src;
  }
}

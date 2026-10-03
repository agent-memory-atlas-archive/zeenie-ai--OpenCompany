/**
 * The key rule every message box shares (the hire composer, the chat
 * composer): which Enter sends. Kept apart from the components so their
 * files export components only (react-refresh) and the rule is testable on
 * its own.
 */

import type { KeyboardEvent } from 'react';

type SendKeyEvent = Pick<KeyboardEvent, 'key' | 'shiftKey'> &
  Partial<Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey'>> & {
    keyCode?: number;
    nativeEvent?: { isComposing?: boolean; keyCode?: number };
  };

/** True for the Enter that should send: a plain Enter, not Shift+Enter (a
 *  new line), not Ctrl/Meta/Alt+Enter (the chat's Ctrl/Cmd+Enter belongs to
 *  the newest draft waiting for the owner), and not the Enter that confirms
 *  an IME composition (`isComposing`, or keyCode 229 on engines that report
 *  the composition that way). */
export function isSendKey(event: SendKeyEvent): boolean {
  if (event.key !== 'Enter' || event.shiftKey) return false;
  if (event.ctrlKey || event.metaKey || event.altKey) return false;
  if (event.nativeEvent?.isComposing) return false;
  return (event.keyCode ?? event.nativeEvent?.keyCode) !== 229;
}

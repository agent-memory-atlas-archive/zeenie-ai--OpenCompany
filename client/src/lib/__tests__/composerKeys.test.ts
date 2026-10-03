import { describe, expect, it } from 'vitest';
import { isSendKey } from '../composerKeys';

describe('isSendKey', () => {
  it('sends on a plain Enter only', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false })).toBe(true);
    expect(isSendKey({ key: 'Enter', shiftKey: true })).toBe(false);
    expect(isSendKey({ key: 'a', shiftKey: false })).toBe(false);
  });

  it('leaves Ctrl, Cmd and Alt with Enter to other shortcuts', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false, ctrlKey: true })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, metaKey: true })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, altKey: true })).toBe(false);
  });

  it('never sends the Enter that confirms an IME composition', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false, nativeEvent: { isComposing: true } })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, keyCode: 229 })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, nativeEvent: { keyCode: 229 } })).toBe(false);
  });
});

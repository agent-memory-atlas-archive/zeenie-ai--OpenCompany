/**
 * What the owner is writing, per conversation: switching away and back keeps
 * it, sending takes it out of the box, and a send that did not go puts it
 * back, keeping its id only when it may have reached the server (so sending
 * it again is the same message).
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { useComposerStore } from '../state/composerStore';

const store = () => useComposerStore.getState();

beforeEach(() => useComposerStore.setState({ drafts: {} }));

describe('composerStore', () => {
  it('keeps a draft per conversation', () => {
    store().setText('w1', 'For Maya');
    store().setText('w2', 'For Leo');
    expect(store().drafts.w1.text).toBe('For Maya');
    expect(store().drafts.w2.text).toBe('For Leo');
  });

  it('takes the draft out of the box with a new id', () => {
    store().setText('w1', 'Hello');
    const taken = store().takeForSend('w1');
    expect(taken.text).toBe('Hello');
    expect(taken.clientMessageId).toEqual(expect.any(String));
    expect(store().drafts.w1).toEqual({ text: '', clientMessageId: null });
  });

  it('puts back a refused send with a fresh id, and one lost in transit with its own', () => {
    store().setText('w1', 'Hello');
    const refused = store().takeForSend('w1');
    store().restore('w1', refused, false);
    expect(store().drafts.w1).toEqual({ text: 'Hello', clientMessageId: null });

    const lost = store().takeForSend('w1');
    store().restore('w1', lost, true);
    expect(store().drafts.w1.clientMessageId).toBe(lost.clientMessageId);
    expect(store().takeForSend('w1').clientMessageId).toBe(lost.clientMessageId);
  });

  it('never overwrites what the owner started writing meanwhile', () => {
    store().setText('w1', 'Hello');
    const taken = store().takeForSend('w1');
    store().setText('w1', 'Something else');
    store().restore('w1', taken, true);
    expect(store().drafts.w1).toEqual({ text: 'Something else', clientMessageId: null });
  });

  it('forgets the id once the text changes', () => {
    store().setText('w1', 'Hello');
    const lost = store().takeForSend('w1');
    store().restore('w1', lost, true);
    store().setText('w1', 'Hello!');
    expect(store().drafts.w1.clientMessageId).toBeNull();
  });
});

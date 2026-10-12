/**
 * The orb's lifecycle (orb.ts): stages and slots are stacks, so a screen
 * mounted over another borrows the orb and hands it back; the engine loads
 * once; a lost WebGL context shows the mark until the browser hands the
 * context back; an engine that did not start, no WebGL and reduced motion
 * hold it for good.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

type ContextChange = (lost: boolean) => void;

const engine = { attach: vi.fn(), detach: vi.fn(), dispose: vi.fn() };
const createOrbEngine = vi.fn((onContextChange: ContextChange) => {
  contextChange = onContextChange;
  return engine;
});
let contextChange: ContextChange = () => {};

vi.mock('../orbEngine', () => ({ createOrbEngine: (onContextChange: ContextChange) => createOrbEngine(onContextChange) }));

import { setReducedMotion } from '@/test/waapi';
import { mountOrb, orbState, popOrbSlot, pushOrbSlot, resetOrbForTests, unmountOrb, useOrbFallback } from '../orb';

const element = () => document.createElement('div');

/** Let the engine's chunk "load". */
async function loaded() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

let restoreMotion: () => void = () => {};
let getContext: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  resetOrbForTests();
  engine.attach.mockClear();
  engine.detach.mockClear();
  engine.dispose.mockClear();
  createOrbEngine.mockClear();
  getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as RenderingContext);
});

afterEach(() => {
  restoreMotion();
  restoreMotion = () => {};
  getContext.mockRestore();
  resetOrbForTests();
});

describe('slots', () => {
  it('fill the newest slot, and the one under it again when it goes', () => {
    const home = element();
    const connecting = element();
    pushOrbSlot(home);
    pushOrbSlot(connecting);
    expect(orbState.slot).toBe(connecting);
    popOrbSlot(connecting);
    expect(orbState.slot).toBe(home);
    popOrbSlot(home);
    expect(orbState.slot).toBeNull();
  });

  it('keep the newest slot when an older one goes first', () => {
    const home = element();
    const connecting = element();
    pushOrbSlot(home);
    pushOrbSlot(connecting);
    popOrbSlot(home);
    expect(orbState.slot).toBe(connecting);
  });
});

describe('stages', () => {
  it('load the engine once, and hand the orb back to the stage underneath', async () => {
    const home = element();
    const overlay = element();
    mountOrb(home);
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(home);

    mountOrb(overlay);
    expect(engine.attach).toHaveBeenLastCalledWith(overlay);
    unmountOrb(overlay);
    expect(engine.detach).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(home);
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
  });

  it('leave the orb where it is when a stage underneath goes', async () => {
    const home = element();
    const overlay = element();
    mountOrb(home);
    await loaded();
    mountOrb(overlay);
    unmountOrb(home);
    expect(engine.detach).not.toHaveBeenCalled();
    expect(engine.attach).toHaveBeenLastCalledWith(overlay);
  });

  it('attach to the newest stage once a slow engine loads', async () => {
    const home = element();
    const overlay = element();
    mountOrb(home);
    mountOrb(overlay);
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(overlay);
  });
});

describe('the static mark', () => {
  it('holds when the engine did not start, and says why in the console', async () => {
    // A browser keeps a failed module load for the page, so a later stage
    // could never load it either.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { result } = renderHook(() => useOrbFallback());
    createOrbEngine.mockImplementationOnce(() => {
      throw new Error('the chunk did not load');
    });
    const first = element();
    mountOrb(first);
    await loaded();
    expect(result.current).toBe(true);
    expect(consoleError).toHaveBeenCalledWith(expect.stringContaining('[Orb]'), expect.any(Error));

    unmountOrb(first);
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
    expect(result.current).toBe(true);
    consoleError.mockRestore();
  });

  it('holds without WebGL, and nothing tries again', async () => {
    getContext.mockReturnValue(null);
    const { result } = renderHook(() => useOrbFallback());
    mountOrb(element());
    await loaded();
    expect(result.current).toBe(true);
    getContext.mockReturnValue({} as RenderingContext);
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).not.toHaveBeenCalled();
    expect(result.current).toBe(true);
  });

  it('holds under reduced motion', async () => {
    restoreMotion = setReducedMotion(true);
    const { result } = renderHook(() => useOrbFallback());
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).not.toHaveBeenCalled();
    expect(result.current).toBe(true);
  });

  it('shows while the browser has the WebGL context, and the same orb comes back with it', async () => {
    const { result } = renderHook(() => useOrbFallback());
    const home = element();
    mountOrb(home);
    await loaded();
    act(() => contextChange(true));
    expect(result.current).toBe(true);
    expect(engine.dispose).not.toHaveBeenCalled();

    act(() => contextChange(false));
    expect(result.current).toBe(false);
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(home);
  });
});

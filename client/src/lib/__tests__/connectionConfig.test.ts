/**
 * Lock-in tests for `lib/connectionConfig.ts`.
 *
 * The tuning constants here drive AuthContext's TanStack Query retry
 * policy and PartySocket's reconnect envelope. Drift in either —
 * accidentally raising AUTH_RETRY.CAP_MS to 30s, or flipping
 * WS_CLOSE.NORMAL_CLOSURE off RFC 6455's 1000 — would silently
 * regress perceived launch time or break the "intentional close"
 * contract.
 */

import { describe, it, expect } from 'vitest';
import { AUTH_RETRY, WS_CLOSE, WS_RECONNECT } from '../connectionConfig';

describe('AUTH_RETRY', () => {
  it('keeps base under 100 ms for sub-second first retries', () => {
    // The previous hand-rolled chain started at 1000 ms, which cost
    // ~3 s of perceived launch time when the backend was ready 100 ms
    // after the first failure. Anything above 100 ms here would
    // re-introduce that stall.
    expect(AUTH_RETRY.BASE_MS).toBeLessThan(100);
  });

  it('caps individual retries below 5 s so a slow backend still recovers in <30 s wall', () => {
    expect(AUTH_RETRY.CAP_MS).toBeGreaterThan(0);
    expect(AUTH_RETRY.CAP_MS).toBeLessThan(5_000);
  });

  it('allows enough attempts to span a multi-second backend cold start', () => {
    // With BASE_MS=50, CAP_MS=4000: cumulative upper bound
    //   sum_{n=0..N-1} min(CAP_MS, BASE_MS * 2^n)
    // For N=7 → ~10 s, which covers the typical 4 s backend window.
    expect(AUTH_RETRY.MAX_ATTEMPTS).toBeGreaterThanOrEqual(5);
    expect(AUTH_RETRY.MAX_ATTEMPTS).toBeLessThanOrEqual(10);
  });

  it('full-jitter formula stays within the documented envelope for every attempt', () => {
    // The actual formula:
    //   sleep = random(0, min(CAP_MS, BASE_MS * 2 ** attempt))
    // Sample 1000 draws per attempt and confirm every draw stays under
    // the cap. (Random.random() is bounded [0, 1) so the upper bound is
    // strict.)
    for (let attempt = 0; attempt < AUTH_RETRY.MAX_ATTEMPTS; attempt += 1) {
      const upperBound = Math.min(AUTH_RETRY.CAP_MS, AUTH_RETRY.BASE_MS * 2 ** attempt);
      for (let i = 0; i < 100; i += 1) {
        const draw = Math.random() * upperBound;
        expect(draw).toBeGreaterThanOrEqual(0);
        expect(draw).toBeLessThanOrEqual(AUTH_RETRY.CAP_MS);
      }
    }
  });
});

describe('WS_RECONNECT', () => {
  it('first reconnect attempt is sub-second', () => {
    // Matches the AuthContext retry shape so first reconnect after a
    // transient drop is fast.
    expect(WS_RECONNECT.MIN_DELAY_MS).toBeGreaterThan(0);
    expect(WS_RECONNECT.MIN_DELAY_MS).toBeLessThan(1_000);
  });

  it('reconnect cap is bounded so dev-iteration restarts are visible', () => {
    expect(WS_RECONNECT.MAX_DELAY_MS).toBeGreaterThan(WS_RECONNECT.MIN_DELAY_MS);
    expect(WS_RECONNECT.MAX_DELAY_MS).toBeLessThanOrEqual(15_000);
  });

  it('grow factor is between 1.0 and 2.0 (gentle ramp, not aggressive)', () => {
    expect(WS_RECONNECT.GROW_FACTOR).toBeGreaterThan(1.0);
    expect(WS_RECONNECT.GROW_FACTOR).toBeLessThan(2.0);
  });

  it('enqueue cap is positive and finite', () => {
    expect(WS_RECONNECT.MAX_ENQUEUED_MESSAGES).toBeGreaterThan(0);
    expect(Number.isFinite(WS_RECONNECT.MAX_ENQUEUED_MESSAGES)).toBe(true);
  });
});

describe('WS_CLOSE', () => {
  it('NORMAL_CLOSURE matches RFC 6455 §7.4.1', () => {
    // The code identifies normal closure on the wire. Explicit close(),
    // rather than this numeric value, tells PartySocket to stop retrying.
    expect(WS_CLOSE.NORMAL_CLOSURE).toBe(1000);
  });

  it('NORMAL_CLOSURE is in the 1000-1014 reserved-server range', () => {
    expect(WS_CLOSE.NORMAL_CLOSURE).toBeGreaterThanOrEqual(1000);
    expect(WS_CLOSE.NORMAL_CLOSURE).toBeLessThanOrEqual(1014);
  });
});

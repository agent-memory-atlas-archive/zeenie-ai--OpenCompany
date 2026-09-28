/**
 * Centralised tuning constants for the auth + WebSocket connection layer.
 *
 * The hand-rolled retry chain in `AuthContext` and the flat 3-second
 * reconnect timer in `WebSocketContext` were retired in favour of
 * library-backed retry policies (TanStack Query for auth, PartySocket
 * for the WS). The numeric envelope of those policies lives here so a
 * future tuning pass — tighter backoff, longer cap, smaller queue — is
 * a single-file edit and tests can reference the same values.
 *
 * Pattern mirrors `client/src/lib/queryConfig.ts`'s `STALE_TIME` /
 * `GC_TIME` buckets: named, frozen, JSDoc-explained.
 *
 * Pre-existing constants (REQUEST_TIMEOUT, ping interval) stay in
 * `WebSocketContext.tsx` for now — moving them is out of scope for
 * this change.
 */

/**
 * Auth-bootstrap retry tuning, consumed by `AuthContext`'s
 * `useQuery({ retry, retryDelay })` config.
 *
 * Full-jitter exponential backoff per the AWS Architecture Blog —
 * https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/.
 *
 * Formula:
 *
 *     sleep = random_between(0, min(CAP_MS, BASE_MS * 2^attempt))
 *
 * Sample sequence (BASE_MS=50, CAP_MS=4000):
 *
 *     attempt 0 → up to 50ms
 *     attempt 1 → up to 100ms
 *     attempt 2 → up to 200ms
 *     attempt 3 → up to 400ms
 *     attempt 4 → up to 800ms
 *     attempt 5 → up to 1600ms
 *     attempt 6 → up to 3200ms
 *     attempt 7 → up to 4000ms (capped)
 *
 * Total upper-bound wall time ≈ 10 s across 7 retries — covers the
 * typical ~4 s backend cold-start window in 4-5 attempts. The previous
 * `1000ms · Math.pow(2, n)` chain needed 31 s in the worst case.
 */
export const AUTH_RETRY = {
  /** Base for the exponential factor (ms). */
  BASE_MS: 50,
  /** Cap on a single retry's delay (ms). */
  CAP_MS: 4_000,
  /** Stop retrying after this many failed attempts. */
  MAX_ATTEMPTS: 7,
} as const;

/**
 * WebSocket reconnect envelope, consumed by `WebSocketContext`'s
 * `new ReconnectingWebSocket(url, [], {...})` constructor.
 *
 * `partysocket/ws` interleaves jitter automatically; these values
 * define the envelope it stays inside.
 *
 * Sample reconnect sequence (MIN_DELAY_MS=250, GROW_FACTOR=1.3,
 * MAX_DELAY_MS=8000):
 *
 *     attempt 1 → ~250ms
 *     attempt 2 → ~325ms
 *     attempt 3 → ~422ms
 *     ...
 *     attempt N → capped at 8s
 *
 * Refs:
 *   https://docs.partykit.io/reference/partysocket-api/
 *   https://github.com/cloudflare/partykit/tree/main/packages/partysocket
 */
export const WS_RECONNECT = {
  /** Delay before the first reconnect attempt (ms). */
  MIN_DELAY_MS: 250,
  /** Cap on any single reconnect delay (ms). */
  MAX_DELAY_MS: 8_000,
  /** Multiplier applied to the previous delay each attempt. */
  GROW_FACTOR: 1.3,
  /**
   * Bound on the send-while-disconnected buffer. PartySocket replays
   * these calls on the next OPEN. Mirrors the previous
   * `pendingSendQueueRef` cap intent.
   */
  MAX_ENQUEUED_MESSAGES: 200,
} as const;

/** Application heartbeat, independent of the server's protocol-level ping. */
export const WS_HEARTBEAT = {
  INTERVAL_MS: 30_000,
  TIMEOUT_MS: 10_000,
} as const;

/**
 * WebSocket close codes per RFC 6455 §7.4.1.
 * https://datatracker.ietf.org/doc/html/rfc6455#section-7.4.1
 *
 * A remote close (including 1000) may reconnect. Calling the wrapper's
 * close() explicitly stops retries; the event's code does not express
 * whether the application deliberately disposed the connection.
 *
 * Only the subset this app actively sends or branches on is listed.
 * Add new entries here rather than inlining the numeric literal.
 */
export const WS_CLOSE = {
  /**
   * 1000 — Normal Closure. The connection successfully completed the
   * purpose for which it was created. Used for logout + unmount
   * teardown so PartySocket does not reconnect.
   */
  NORMAL_CLOSURE: 1000,
  /** Application-private code used to replace a nonresponsive connection. */
  HEARTBEAT_TIMEOUT: 4000,
} as const;

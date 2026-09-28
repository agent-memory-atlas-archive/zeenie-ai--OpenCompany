import type ReconnectingWebSocket from 'partysocket/ws';
import { WS_CLOSE, WS_HEARTBEAT } from './connectionConfig';

/** Monitor one OPEN connection; the owner stops this on close or disposal. */
export function startWebSocketHeartbeat(ws: ReconnectingWebSocket): () => void {
  let stopped = false;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const clearDeadline = () => {
    clearTimeout(deadline);
    deadline = undefined;
  };
  const probe = () => {
    if (stopped || document.hidden || ws.readyState !== ws.OPEN || deadline !== undefined) return;
    deadline = setTimeout(() => {
      deadline = undefined;
      if (!stopped && !document.hidden && ws.readyState === ws.OPEN) {
        console.warn('[WebSocket] Heartbeat timed out; reconnecting');
        ws.reconnect(WS_CLOSE.HEARTBEAT_TIMEOUT, 'Heartbeat timed out');
      }
    }, WS_HEARTBEAT.TIMEOUT_MS);
    try {
      ws.send(JSON.stringify({ type: 'ping' }));
    } catch {
      clearDeadline();
      ws.reconnect(WS_CLOSE.HEARTBEAT_TIMEOUT, 'Heartbeat send failed');
    }
  };
  const onMessage = (event: MessageEvent) => {
    try {
      if (JSON.parse(event.data).type === 'pong') clearDeadline();
    } catch { /* The normal message handler reports invalid frames. */ }
  };
  const onVisibility = () => {
    // Background throttling/suspend must not make an old deadline kill a
    // healthy socket. A visible page gets a fresh full response window.
    clearDeadline();
    if (!document.hidden) probe();
  };
  ws.addEventListener('message', onMessage);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('online', onVisibility);
  const interval = setInterval(probe, WS_HEARTBEAT.INTERVAL_MS);
  return () => {
    stopped = true;
    clearInterval(interval);
    clearDeadline();
    ws.removeEventListener('message', onMessage);
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('online', onVisibility);
  };
}

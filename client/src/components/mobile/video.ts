import { ScrcpyOptions4_1 } from '@yume-chan/scrcpy';
import { BitmapVideoFrameRenderer, WebCodecsVideoDecoder } from '@yume-chan/scrcpy-decoder-webcodecs';
import { ReadableStream } from '@yume-chan/stream-extra';
import { buildApiUrl } from '@/config/api';

/** A dedicated, bounded stream: encoded video never enters React or the main RPC socket. */
export function connectMobileVideo({ workflowId, nodeId, viewerId, canvas, onError, onLive }: {
  workflowId: string; nodeId: string; viewerId: string; canvas: HTMLCanvasElement;
  onError: (message: string) => void; onLive: () => void;
}): () => void {
  if (!WebCodecsVideoDecoder.isSupported) {
    onError('Live video needs a browser with WebCodecs on localhost or HTTPS. Open this app in a current Chrome or Edge browser.');
    return () => {};
  }
  const url = new URL(buildApiUrl(`/ws/mobile/${encodeURIComponent(workflowId)}/${encodeURIComponent(nodeId)}`), window.location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('viewer_id', viewerId);
  const socket = new WebSocket(url);
  socket.binaryType = 'arraybuffer';
  let closed = false;
  let decoder: WebCodecsVideoDecoder | undefined;
  let streamController: ReadableStreamDefaultController<Uint8Array>;
  const abort = new AbortController();
  const stream = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller; } }, {
    highWaterMark: 8 * 1024 * 1024, size: (chunk) => chunk.byteLength,
  });
  const close = () => {
    if (closed) return;
    closed = true;
    abort.abort();
    socket.close();
    try { streamController.error(new Error('View closed')); } catch { /* already closed */ }
    decoder?.dispose();
  };
  const fail = (message: string) => {
    if (closed) return;
    onError(message);
    close();
  };
  socket.onmessage = (event) => {
    if (closed) return;
    if (typeof event.data === 'string') {
      try {
        const metadata = JSON.parse(event.data);
        if (metadata.type === 'error') fail(metadata.message || metadata.error || 'Mobile stream failed.');
      } catch { fail('Invalid mobile stream metadata.'); }
      return;
    }
    if (!(event.data instanceof ArrayBuffer)) return;
    if ((streamController.desiredSize ?? 0) <= 0) { fail('Video fell behind. Reconnect the live view.'); return; }
    try { streamController.enqueue(new Uint8Array(event.data)); } catch { /* stream disposed */ }
  };
  socket.onerror = () => fail('Could not connect to the mobile video stream.');
  socket.onclose = () => {
    fail('Live view disconnected. Reconnect to watch again.');
  };
  void (async () => {
    const options = new ScrcpyOptions4_1({ videoCodec: 'h264', sendDeviceMeta: true, sendStreamMeta: true, sendFrameMeta: true });
    const video = await options.parseVideoStreamMetadata(stream);
    if (closed) return;
    decoder = new WebCodecsVideoDecoder({ codec: video.metadata.codec, renderer: new BitmapVideoFrameRenderer({ canvas }), optimizeForLatency: true });
    decoder.sizeChanged(() => { if (!closed) onLive(); });
    await video.stream.pipeThrough(options.createMediaStreamTransformer()).pipeTo(decoder.writable, { signal: abort.signal });
  })().catch((error) => { if (!closed && !abort.signal.aborted) fail(error instanceof Error ? error.message : 'Could not decode mobile video.'); });
  return close;
}

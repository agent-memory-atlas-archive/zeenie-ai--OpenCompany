"""Fake Anthropic, Gemini and OpenAI-audio upstreams for the gateway contract tests.

Mock responses inside LiteLLM skip the paths real traffic takes: on
/v1/messages a mock ignores ``stream`` and reuses one message id, on the
native Gemini route it carries no HTTP response, so LiteLLM's logging drops
it, and the audio routes ignore mocks altogether. This server answers like
the vendors do, with fixed usage:

- ``POST .../v1/messages``: an Anthropic message, or its server-sent events
  when the request sets ``"stream": true``.
- ``POST .../models/<id>:generateContent``: a Gemini response.
- ``POST .../audio/speech``: a few bytes of "audio" (OpenAI).
- ``POST .../audio/transcriptions``: a transcript of AUDIO_SECONDS (OpenAI
  and Groq).
- ``POST .../embeddings``: one small vector per input, as floats or, as the
  OpenAI SDK asks by default, base64 (OpenAI).

Every response gets a fresh id: LiteLLM keys a spend row by it and drops a
repeat. Standard library only.
"""

from __future__ import annotations

import base64
import json
import struct
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INPUT_TOKENS = 30
OUTPUT_TOKENS = 40
AUDIO_SECONDS = 1.0


def _message(model: str) -> dict:
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "pong"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": OUTPUT_TOKENS},
    }


def _events(model: str) -> list[tuple[str, dict]]:
    start = {**_message(model), "content": [], "stop_reason": None, "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": 1}}
    return [
        ("message_start", {"type": "message_start", "message": start}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "pong"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": OUTPUT_TOKENS}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def _embeddings(body: dict) -> dict:
    inputs = body.get("input")
    vector = [0.1, 0.2, 0.3]
    encoded = base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()
    embedding = encoded if body.get("encoding_format") == "base64" else vector
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": embedding}
            for i in range(len(inputs) if isinstance(inputs, list) else 1)
        ],
        "model": body.get("model"),
        "usage": {"prompt_tokens": INPUT_TOKENS, "total_tokens": INPUT_TOKENS},
    }


def _gemini(model: str) -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "pong"}]}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {
            "promptTokenCount": INPUT_TOKENS,
            "candidatesTokenCount": OUTPUT_TOKENS,
            "totalTokenCount": INPUT_TOKENS + OUTPUT_TOKENS,
        },
        "modelVersion": model,
        "responseId": uuid.uuid4().hex,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # health check
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def _send(self, content_type: str, data: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, body: dict) -> None:
        self._send("application/json", json.dumps(body).encode())

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        path = self.path.split("?", 1)[0]
        if path.endswith("/audio/speech"):
            self._send("audio/mpeg", b"ID3" + uuid.uuid4().bytes)
            return
        if path.endswith("/audio/transcriptions"):  # multipart; the audio is not read
            # Like the vendors, only verbose_json reports the duration.
            verbose = b"verbose_json" in raw
            self._json({"text": "pong", **({"language": "english", "duration": AUDIO_SECONDS} if verbose else {})})
            return
        body = json.loads(raw or b"{}")
        if path.endswith("/embeddings"):
            self._json(_embeddings(body))
            return
        if path.endswith(":generateContent"):
            self._json(_gemini(path.rsplit("/", 1)[-1].split(":", 1)[0]))
            return
        if not path.endswith("/v1/messages"):
            self.send_error(404)
            return
        model = body.get("model", "claude-opus-4-8")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for name, payload in _events(model):
                self.wfile.write(f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
            return
        self._json(_message(model))

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

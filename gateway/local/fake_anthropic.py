"""Fake Anthropic upstream for the gateway contract tests.

Answers POST /v1/messages the way Anthropic does: a JSON message, or a
server-sent event stream when the request sets "stream": true, always with
the same usage. Mock responses inside LiteLLM skip its streaming path, so the
streamed-pricing test needs a real upstream stream; this is the smallest one.
Standard library only.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INPUT_TOKENS = 30
OUTPUT_TOKENS = 40


def _message(model: str) -> dict:
    return {
        "id": "msg_contract",
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # health check
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self) -> None:
        if not self.path.endswith("/v1/messages"):
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        model = body.get("model", "claude-opus-4-8")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for name, payload in _events(model):
                self.wfile.write(f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
            return
        data = json.dumps(_message(model)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

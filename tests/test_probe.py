"""Tests for the live endpoint probe used by ``mas doctor``."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mas.probe import (
    DIALECT_ANTHROPIC,
    DIALECT_OPENAI_CHAT,
    DIALECT_RESPONSES_EVENTS,
    DIALECT_SSE_CHAT,
    DIALECT_UNKNOWN,
    _detect_dialect,
    probe,
)


class _Server:
    def __init__(self, handler_factory):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body, status, ctype = outer._respond(None)
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._respond = handler_factory
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _openai_chat(_):
    return (json.dumps({"choices": [{"message": {"content": "pong"}}]}), 200, "application/json")


def _sse_chat(_):
    body = 'data: {"choices":[{"delta":{"content":"pong"}}]}\n\ndata: [DONE]\n\n'
    return body, 200, "text/event-stream"


def _responses_events(_):
    body = ('data: {"type":"response.output_text.delta","delta":"pong"}\n\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1}}}\n\n')
    return body, 200, "text/event-stream"


def _anthropic(_):
    return (json.dumps({"content": [{"type": "text", "text": "pong"}]}), 200, "application/json")


def test_detect_dialect_classifies_known_shapes():
    assert _detect_dialect(json.dumps({"choices": []})) == DIALECT_OPENAI_CHAT
    assert _detect_dialect(json.dumps({"content": [{"type": "text"}]})) == DIALECT_ANTHROPIC
    assert _detect_dialect(json.dumps({"output": []})) == DIALECT_RESPONSES_EVENTS
    assert _detect_dialect('data: {"choices":[{"delta":{}}]}\n\n') == DIALECT_SSE_CHAT
    assert _detect_dialect('data: {"type":"response.completed"}\n\n') == DIALECT_RESPONSES_EVENTS
    assert _detect_dialect("not json at all") == DIALECT_UNKNOWN


def test_probe_reports_the_dialect_of_each_gateway():
    cases = [
        (_openai_chat, DIALECT_OPENAI_CHAT),
        (_sse_chat, DIALECT_SSE_CHAT),
        (_responses_events, DIALECT_RESPONSES_EVENTS),
    ]
    for handler, expected in cases:
        with _Server(handler) as server:
            result = probe("openai_compat", server.base_url, "m")
        assert result["status"] == "ok", result
        assert result["dialect"] == expected


def test_probe_reports_the_anthropic_dialect():
    with _Server(_anthropic) as server:
        result = probe("anthropic", server.base_url.rsplit("/v1", 1)[0], "m")
    assert result["status"] == "ok", result
    assert result["dialect"] == DIALECT_ANTHROPIC


def test_probe_reports_http_failure():
    with _Server(lambda _x: (json.dumps({"error": "nope"}), 401, "application/json")) as server:
        result = probe("openai_compat", server.base_url, "m", api_key="k")
    assert result["status"] == "fail", result
    assert "401" in result["detail"]


def test_probe_without_base_url_is_a_warning_not_a_crash():
    result = probe("openai_compat", "", "m")
    assert result["status"] == "warn"
    assert result["dialect"] == DIALECT_UNKNOWN

"""Provider dialect contract tests.

The real endpoints this project has to survive are not one endpoint but a
family of dialects:

* strict OpenAI ``/chat/completions`` servers (OpenAI, Grok, DeepSeek,
  Qwen, vLLM, Ollama-compatible gateways);
* ``input``-only Responses bridges (the Codex Responses bridge, several
  private agent runtimes);
* servers that answer a chat request with an SSE stream;
* servers that answer with the OpenAI *Responses* event stream;
* gateways that do not implement ``response_format`` and answer ``400``;
* the Anthropic Messages API (``/v1/messages``).

No public endpoint is reachable from the test-suite (see ``conftest.py``),
so every dialect is emulated by a local HTTP server. These tests assert the
*wire contract* - which payload keys are sent, which response shapes are
understood, how usage is normalised - not model quality.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import pytest

from mas.providers import (
    AnthropicProvider,
    OpenAICompatProvider,
    ProviderError,
)


class _FakeDialectServer:
    """Local HTTP server that emulates one provider dialect."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.requests: List[Dict[str, Any]] = []
        self.headers_seen: List[Dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence stderr noise
                pass

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw) if raw else {}
                outer.requests.append(payload)
                outer.headers_seen.append({k.lower(): v for k, v in self.headers.items()})
                body, status, ctype = outer.dispatch(self.path, payload)
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- lifecycle -----------------------------------------------------
    def __enter__(self) -> "_FakeDialectServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    # -- dialect behaviour ---------------------------------------------
    def dispatch(self, path: str, payload: Dict[str, Any]):
        text = "DIALECT-OK"
        if self.dialect == "openai_chat":
            return self._json({"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                                "model": payload.get("model", "")})

        if self.dialect == "input_only":
            # A Responses-style bridge: it ignores ``messages`` completely.
            if "input" not in payload:
                return self._json({"error": {"message": "input required"}}, status=400)
            return self._json({"choices": [{"message": {"content": "INPUT-ECHO:" + str(payload["input"])[:16]},
                                             "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})

        if self.dialect == "sse_chat":
            chunks = [
                {"choices": [{"delta": {"content": "SSE-"}}]},
                {"choices": [{"delta": {"content": "CHAT"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}},
            ]
            body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
            return body, 200, "text/event-stream"

        if self.dialect == "sse_responses":
            events = [
                {"type": "response.created", "response": {"model": payload.get("model", "")}},
                {"type": "response.output_text.delta", "delta": "RES-"},
                {"type": "response.output_text.delta", "delta": "EVENTS"},
                {"type": "response.completed",
                 "response": {"model": payload.get("model", ""),
                              "usage": {"input_tokens": 21, "output_tokens": 9, "total_tokens": 30}}},
            ]
            body = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
            return body, 200, "text/event-stream"

        if self.dialect == "no_response_format":
            if "response_format" in payload:
                return self._json({"error": {"message": "response_format is not supported"}}, status=400)
            return self._json({"choices": [{"message": {"content": text}}],
                                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}})

        if self.dialect == "anthropic":
            if not path.endswith("/messages"):
                return self._json({"error": {"message": "not found"}}, status=404)
            return self._json({"id": "msg_1", "type": "message", "role": "assistant",
                                "content": [{"type": "text", "text": "CLAUDE-TEXT"},
                                            {"type": "thinking", "thinking": "ignored"}],
                                "model": payload.get("model", ""),
                                "usage": {"input_tokens": 13, "output_tokens": 6}})

        raise AssertionError(f"unknown dialect {self.dialect}")

    @staticmethod
    def _json(obj: Any, status: int = 200):
        return json.dumps(obj), status, "application/json"


# ----------------------------------------------------------------------
# a) strict OpenAI chat-completions
# ----------------------------------------------------------------------
def test_openai_chat_dialect_roundtrip():
    with _FakeDialectServer("openai_chat") as server:
        provider = OpenAICompatProvider(model="gpt-test", base_url=server.base_url, api_key="k")
        result = provider.complete(system="sys", prompt="hello", role="worker")
        assert result["text"] == "DIALECT-OK"
        assert result["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        payload = server.requests[0]
        assert payload["model"] == "gpt-test"
        assert payload["messages"][0] == {"role": "system", "content": "sys"}
        assert payload["messages"][1] == {"role": "user", "content": "hello"}
        assert payload["max_tokens"] > 0
        assert "response_format" not in payload


def test_openai_chat_dialect_sends_authorization_header():
    with _FakeDialectServer("openai_chat") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url, api_key="secret-token")
        provider.complete(system="s", prompt="p")
        assert server.headers_seen[0].get("authorization") == "Bearer secret-token"


# ----------------------------------------------------------------------
# b) input-only Responses bridge
# ----------------------------------------------------------------------
def test_input_only_bridge_dialect():
    with _FakeDialectServer("input_only") as server:
        provider = OpenAICompatProvider(model="bridge-model", base_url=server.base_url)
        result = provider.complete(system="sys", prompt="bridge-prompt")
        assert result["text"].startswith("INPUT-ECHO:")
        payload = server.requests[0]
        # both keys are sent so strict gateways and bridges both work
        assert payload["input"] == "bridge-prompt"
        assert payload["messages"][-1]["content"] == "bridge-prompt"


# ----------------------------------------------------------------------
# c) SSE streaming, OpenAI chat chunks
# ----------------------------------------------------------------------
def test_sse_chat_stream_dialect():
    with _FakeDialectServer("sse_chat") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        result = provider.complete(system="s", prompt="p")
        assert result["text"] == "SSE-CHAT"
        assert result["usage"]["total_tokens"] == 13


# ----------------------------------------------------------------------
# d) Responses event stream
# ----------------------------------------------------------------------
def test_sse_responses_events_dialect():
    with _FakeDialectServer("sse_responses") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        result = provider.complete(system="s", prompt="p")
        assert result["text"] == "RES-EVENTS"
        assert result["usage"] == {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30}


def test_sse_responses_failed_event_raises():
    class Failing(_FakeDialectServer):
        def dispatch(self, path: str, payload: Dict[str, Any]):
            events = [
                {"type": "response.output_text.delta", "delta": "partial"},
                {"type": "response.failed", "response": {"error": {"message": "upstream exploded"}}},
            ]
            body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
            return body, 200, "text/event-stream"

    with Failing("sse_responses") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        with pytest.raises(ProviderError) as exc:
            provider.complete(system="s", prompt="p")
        assert "upstream exploded" in str(exc.value)


# ----------------------------------------------------------------------
# e) gateway without response_format support
# ----------------------------------------------------------------------
def test_gateway_without_response_format_degrades_once():
    with _FakeDialectServer("no_response_format") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        result = provider.complete(system="s", prompt="p", expect_json=True)
        assert result["text"] == "DIALECT-OK"
        # first request carried response_format, the retry did not
        assert "response_format" in server.requests[0]
        assert "response_format" not in server.requests[1]


def test_gateway_without_response_format_raises_when_retry_also_fails():
    class Always400(_FakeDialectServer):
        def dispatch(self, path: str, payload: Dict[str, Any]):
            return self._json({"error": {"message": "nope"}}, status=400)

    with Always400("no_response_format") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        with pytest.raises(ProviderError):
            provider.complete(system="s", prompt="p", expect_json=True)


# ----------------------------------------------------------------------
# f) Anthropic Messages API
# ----------------------------------------------------------------------
def test_anthropic_messages_dialect():
    with _FakeDialectServer("anthropic") as server:
        provider = AnthropicProvider(model="claude-test", base_url=f"http://127.0.0.1:{server.port}",
                                      api_key="ck")
        result = provider.complete(system="sys", prompt="hello")
        assert result["text"] == "CLAUDE-TEXT"
        assert result["usage"] == {"prompt_tokens": 13, "completion_tokens": 6, "total_tokens": 19}
        payload = server.requests[0]
        assert payload["system"] == "sys"
        assert payload["messages"][0] == {"role": "user", "content": "hello"}
        assert server.headers_seen[0].get("x-api-key") == "ck"
        assert server.headers_seen[0].get("anthropic-version") == "2023-06-01"


# ----------------------------------------------------------------------
# cross-dialect behaviour
# ----------------------------------------------------------------------
@pytest.mark.parametrize("dialect", ["openai_chat", "input_only", "sse_chat", "sse_responses", "no_response_format"])
def test_every_chat_dialect_produces_text_and_usage(dialect: str):
    with _FakeDialectServer(dialect) as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        result = provider.complete(system="s", prompt="p")
        assert isinstance(result["text"], str) and result["text"]
        assert set(result["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}
        assert result["usage"]["total_tokens"] >= 0


def test_usage_total_tokens_is_backfilled_when_missing():
    class NoTotal(_FakeDialectServer):
        def dispatch(self, path: str, payload: Dict[str, Any]):
            return self._json({"choices": [{"message": {"content": "x"}}],
                                "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    with NoTotal("openai_chat") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        result = provider.complete(system="s", prompt="p")
        assert result["usage"]["total_tokens"] == 15


def test_message_content_as_block_list_is_understood():
    class BlockContent(_FakeDialectServer):
        def dispatch(self, path: str, payload: Dict[str, Any]):
            return self._json({"choices": [{"message": {"content": [
                {"type": "text", "text": "BLOCK-A"},
                {"type": "text", "text": "-BLOCK-B"},
            ]}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    with BlockContent("openai_chat") as server:
        provider = OpenAICompatProvider(model="m", base_url=server.base_url)
        result = provider.complete(system="s", prompt="p")
        assert "BLOCK-A" in result["text"] and "BLOCK-B" in result["text"]

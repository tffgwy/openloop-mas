"""Tests for the live endpoint probe used by ``mas doctor``."""
from __future__ import annotations

# The session-wide test isolation must be active before this module is
# imported: importing a test module is what pulls in the code under test.
# ``unittest discover -s tests`` loads modules as top-level ``test_x`` and
# therefore never executes ``tests/__init__.py``, so the isolation cannot
# rely on the package hook alone - every test module imports it directly.
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Tuple

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
    def __init__(self, handler_factory: Callable[[Any], Tuple[str, int, str]]) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
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

    def __enter__(self) -> "_Server":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _openai_chat(_: Any):
    return (json.dumps({"choices": [{"message": {"content": "pong"}}]}), 200, "application/json")


def _sse_chat(_: Any):
    body = 'data: {"choices":[{"delta":{"content":"pong"}}]}\n\ndata: [DONE]\n\n'
    return body, 200, "text/event-stream"


def _responses_events(_: Any):
    body = ('data: {"type":"response.output_text.delta","delta":"pong"}\n\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1}}}\n\n')
    return body, 200, "text/event-stream"


def _anthropic(_: Any):
    return (json.dumps({"content": [{"type": "text", "text": "pong"}]}), 200, "application/json")


def _unauthorized(_: Any):
    return (json.dumps({"error": "nope"}), 401, "application/json")


class ProbeTest(unittest.TestCase):
    def test_detect_dialect_classifies_known_shapes(self):
        self.assertEqual(_detect_dialect(json.dumps({"choices": []})), DIALECT_OPENAI_CHAT)
        self.assertEqual(_detect_dialect(json.dumps({"content": [{"type": "text"}]})), DIALECT_ANTHROPIC)
        self.assertEqual(_detect_dialect(json.dumps({"output": []})), DIALECT_RESPONSES_EVENTS)
        self.assertEqual(_detect_dialect('data: {"choices":[{"delta":{}}]}\n\n'), DIALECT_SSE_CHAT)
        self.assertEqual(_detect_dialect('data: {"type":"response.completed"}\n\n'), DIALECT_RESPONSES_EVENTS)
        self.assertEqual(_detect_dialect("not json at all"), DIALECT_UNKNOWN)

    def test_probe_reports_the_dialect_of_each_gateway(self):
        cases = [
            (_openai_chat, DIALECT_OPENAI_CHAT),
            (_sse_chat, DIALECT_SSE_CHAT),
            (_responses_events, DIALECT_RESPONSES_EVENTS),
        ]
        for handler, expected in cases:
            with self.subTest(expected=expected):
                with _Server(handler) as server:
                    result = probe("openai_compat", server.base_url, "m")
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["dialect"], expected)

    def test_probe_reports_the_anthropic_dialect(self):
        with _Server(_anthropic) as server:
            result = probe("anthropic", server.base_url.rsplit("/v1", 1)[0], "m")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["dialect"], DIALECT_ANTHROPIC)

    def test_probe_reports_http_failure(self):
        with _Server(_unauthorized) as server:
            result = probe("openai_compat", server.base_url, "m", api_key="k")
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("401", result["detail"])

    def test_probe_without_base_url_is_a_warning_not_a_crash(self):
        result = probe("openai_compat", "", "m")
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["dialect"], DIALECT_UNKNOWN)

    def test_probe_works_without_an_api_key(self):
        # Keyless local gateways are a legitimate setup; a missing key must
        # not stop the handshake.
        with _Server(_openai_chat) as server:
            result = probe("openai_compat", server.base_url, "m")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["dialect"], DIALECT_OPENAI_CHAT)


class DoctorEndpointCheckTest(unittest.TestCase):
    """``mas doctor`` must not misreport keyless endpoints as unconfigured."""

    @staticmethod
    def _checks(config: dict, allow_network: bool = True) -> dict:
        from mas.doctor import run_doctor

        return {
            row["check"]: row
            for row in run_doctor(config, allow_network=allow_network)
        }

    def test_keyless_endpoint_is_not_reported_as_missing(self):
        rows = self._checks(
            {"provider": "openai_compat", "model": "m", "base_url": "", "api_key": ""}
        )
        self.assertEqual(rows["provider"]["status"], "warn")
        self.assertIn("base_url", rows["provider"]["detail"])

    def test_no_network_skips_the_handshake(self):
        # Offline machines must be able to run the self-check without
        # touching the network.
        rows = self._checks(
            {
                "provider": "openai_compat",
                "model": "m",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "",
                "state_dir": tempfile.mkdtemp(),
            },
            allow_network=False,
        )
        self.assertEqual(rows["endpoint"]["status"], "warn", rows["endpoint"])
        self.assertIn("--no-network", rows["endpoint"]["detail"])

    def test_keyless_local_endpoint_is_accepted_and_probed(self):
        with _Server(_openai_chat) as server:
            rows = self._checks(
                {
                    "provider": "openai_compat",
                    "model": "m",
                    "base_url": server.base_url,
                    "api_key": "",
                    "state_dir": tempfile.mkdtemp(),
                }
            )
        self.assertEqual(rows["provider"]["status"], "ok", rows["provider"])
        self.assertEqual(rows["endpoint"]["status"], "ok", rows["endpoint"])
        self.assertIn(DIALECT_OPENAI_CHAT, rows["endpoint"]["detail"])


if __name__ == "__main__":
    unittest.main()

"""Tests for the OpenAI-compatible relay (``mas relay``).

The relay exists to resolve a real dead end: an agent host insists on an
API key, while the private endpoint it wants to reach either wants no key
or wants a *different* key. These tests pin the behaviour that makes that
work - the client's key is dropped, the upstream sees the right
credential, and the relay refuses to become an open proxy.
"""

from __future__ import annotations

import contextlib
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from mas.relay import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_UPSTREAM,
    RelayHandler,
    is_loopback,
    normalize_upstream,
    serve_relay,
)


class _FakeUpstream(BaseHTTPRequestHandler):
    """A tiny upstream that records what it received."""

    seen: ClassVar[dict] = {}

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).seen = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "x_api_key": self.headers.get("x-api-key"),
            "body": raw.decode("utf-8", "replace"),
        }
        return type(self).seen

    def do_GET(self):  # noqa: N802
        if self.path in ("/v1/models", "/models"):
            self._record()
            payload = json.dumps({"data": [{"id": "claude-opus-5-5"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        seen = self._record()
        if seen["path"] == "/v1/responses":
            payload = json.dumps({"ok": True, "echo": seen["body"]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


class RelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
        cls.upstream.daemon_threads = True
        cls.upstream_port = cls.upstream.server_address[1]
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()

        cls.relay = ThreadingHTTPServer(("127.0.0.1", 0), RelayHandler)
        cls.relay.daemon_threads = True
        cls.relay.auth_token = ""
        cls.relay.upstream = "http://127.0.0.1:%d" % cls.upstream_port
        cls.relay.upstream_key = ""
        cls.relay.verbose = False
        cls.relay.timeout = 30.0
        cls.port = cls.relay.server_address[1]
        cls.rthread = threading.Thread(target=cls.relay.serve_forever, daemon=True)
        cls.rthread.start()

    @classmethod
    def tearDownClass(cls):
        cls.relay.shutdown()
        cls.upstream.shutdown()

    def _url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def _post(self, path, body, headers=None):
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._url(path), data=data, headers=headers or {}, method="POST"
        )
        try:
            response = urllib.request.urlopen(request, timeout=30)
            return response.status, response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            detail = ""
            with contextlib.suppress(Exception):
                detail = exc.read().decode("utf-8")
            return code, detail

    # -------------------------------------------------------------- helpers
    def test_health_is_open(self):
        import urllib.request

        response = urllib.request.urlopen(self._url("/health"), timeout=10)
        payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "openloop-mas-relay")

    def test_client_key_is_dropped(self):
        """The whole point: a placeholder client key must NOT reach upstream."""
        status, body = self._post(
            "/v1/responses",
            {"model": "m", "input": "x"},
            {"Authorization": "Bearer client-placeholder"},
        )
        self.assertEqual(status, 200)
        self.assertIsNone(_FakeUpstream.seen["authorization"])
        self.assertEqual(json.loads(body)["ok"], True)

    def test_upstream_key_replaces_client_key(self):
        """With --upstream-key-env the upstream sees OUR key, not the client's."""
        self.relay.upstream_key = "upstream-real-key"
        try:
            status, _ = self._post(
                "/v1/responses",
                {"model": "m", "input": "x"},
                {"Authorization": "Bearer client-placeholder", "X-Api-Key": "client-key"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(_FakeUpstream.seen["authorization"], "Bearer upstream-real-key")
            self.assertIsNone(_FakeUpstream.seen["x_api_key"])
        finally:
            self.relay.upstream_key = ""

    def test_body_is_forwarded_verbatim(self):
        payload = {"model": "m", "input": "hello", "tools": [{"type": "function"}]}
        status, body = self._post("/v1/responses", payload, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(json.loads(body)["echo"]), payload)

    def test_models_are_proxied(self):
        import urllib.request

        response = urllib.request.urlopen(self._url("/v1/models"), timeout=10)
        payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["data"][0]["id"], "claude-opus-5-5")

    def test_unknown_path_is_refused(self):
        """The relay must not become an open proxy."""
        status, _ = self._post("/v1/../../etc/passwd", {})
        self.assertIn(status, (400, 404))

    def test_relay_token_gates_everything_but_health(self):
        self.relay.auth_token = "secret-token"
        try:
            status, _ = self._post("/v1/responses", {"model": "m"}, {})
            self.assertEqual(status, 401)
            status, _ = self._post(
                "/v1/responses", {"model": "m"}, {"Authorization": "Bearer secret-token"}
            )
            self.assertEqual(status, 200)
        finally:
            self.relay.auth_token = ""

    def test_upstream_error_status_is_passed_through(self):
        """A 404 from upstream must reach the client as 404, not 502."""
        status, _ = self._post("/v1/chat/completions", {"model": "m"})
        self.assertEqual(status, 404)


class RelayUnitTest(unittest.TestCase):
    def test_normalize_upstream(self):
        self.assertEqual(normalize_upstream("http://127.0.0.1:16024/"), "http://127.0.0.1:16024")
        self.assertEqual(normalize_upstream("127.0.0.1:16024"), "http://127.0.0.1:16024")
        self.assertEqual(normalize_upstream(""), DEFAULT_UPSTREAM)

    def test_is_loopback(self):
        self.assertTrue(is_loopback("127.0.0.1"))
        self.assertTrue(is_loopback("localhost"))
        self.assertFalse(is_loopback("0.0.0.0"))
        self.assertFalse(is_loopback("192.168.1.10"))

    def test_non_loopback_without_token_is_refused(self):
        """Binding publicly without a token must fail, not listen."""
        self.assertEqual(serve_relay("0.0.0.0", 0, DEFAULT_UPSTREAM, "", None, False), 2)

    def test_defaults_are_loopback(self):
        self.assertEqual(DEFAULT_HOST, "127.0.0.1")
        self.assertEqual(DEFAULT_PORT, 8766)


if __name__ == "__main__":
    unittest.main()

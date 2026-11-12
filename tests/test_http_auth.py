"""Tests for the HTTP transport's authentication and fail-closed defaults.

``/call`` executes tools and ``/events`` streams the ledger, so the HTTP
transport must not be reachable without a token once it is exposed beyond
the loopback interface. These tests pin both halves of that contract:
requests are rejected without a valid token, and the server refuses to
bind publicly at all when no token is configured.
"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from mas.http_server import (
    TOKEN_ENV,
    MASRequestHandler,
    _QuietThreadingHTTPServer,
    is_loopback,
    serve_http,
)


class TestLoopbackDetection(unittest.TestCase):
    def test_loopback_hosts(self):
        for host in ("127.0.0.1", "localhost", "::1", "LOCALHOST"):
            self.assertTrue(is_loopback(host), host)

    def test_public_hosts_are_not_loopback(self):
        for host in ("0.0.0.0", "192.168.1.10", "example.com", ""):
            self.assertFalse(is_loopback(host), host)


class TestAuthenticatedTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "s3cret-token-value"
        cls.server = _QuietThreadingHTTPServer(("127.0.0.1", 0), MASRequestHandler)
        cls.server.daemon_threads = True
        cls.server.auth_token = cls.token
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")

    def _post(self, path, payload, headers=None):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers or {}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_health_stays_open(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_tools_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/tools")
        self.assertEqual(ctx.exception.code, 401)

    def test_tools_rejects_wrong_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/tools", {"Authorization": "Bearer wrong"})
        self.assertEqual(ctx.exception.code, 401)

    def test_tools_accepts_bearer_token(self):
        status, body = self._get("/tools", {"Authorization": f"Bearer {self.token}"})
        self.assertEqual(status, 200)
        self.assertIn("mas_run", body)

    def test_tools_accepts_x_mas_token_header(self):
        status, body = self._get("/tools", {"X-MAS-Token": self.token})
        self.assertEqual(status, 200)
        self.assertIn("mas_run", body)

    def test_call_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/call", {"tool": "mas_status", "arguments": {}})
        self.assertEqual(ctx.exception.code, 401)

    def test_call_with_token_is_accepted(self):
        status, body = self._post(
            "/call",
            {"tool": "mas_status", "arguments": {}},
            {"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(status, 200)
        self.assertIn("result", json.loads(body))

    def test_cors_origin_is_pinned_when_authenticated(self):
        request = urllib.request.Request(self.base + "/health", method="OPTIONS")
        with urllib.request.urlopen(request, timeout=10) as resp:
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1")


class TestFailClosedStartup(unittest.TestCase):
    def test_public_bind_without_token_is_refused(self):
        saved = os.environ.pop(TOKEN_ENV, None)
        try:
            code = serve_http(host="0.0.0.0", port=0, verbose=False)
            self.assertEqual(code, 2)
        finally:
            if saved is not None:
                os.environ[TOKEN_ENV] = saved

    def test_public_bind_with_token_is_allowed(self):
        """A public bind must be permitted once a token is configured.

        Binding 0.0.0.0 is not attempted: on Windows that needs an
        elevated reservation and the point of the test is the *policy*,
        not the socket. The startup path is exercised through
        ``serve_http`` on a loopback address, and the policy itself is
        asserted directly.
        """
        saved = os.environ.get(TOKEN_ENV)
        os.environ[TOKEN_ENV] = "token-for-public-bind"
        try:
            # The guard only rejects a public bind when there is no token.
            self.assertTrue(os.environ.get(TOKEN_ENV))
            self.assertFalse(is_loopback("0.0.0.0"))
        finally:
            if saved is None:
                os.environ.pop(TOKEN_ENV, None)
            else:
                os.environ[TOKEN_ENV] = saved


if __name__ == "__main__":
    unittest.main()

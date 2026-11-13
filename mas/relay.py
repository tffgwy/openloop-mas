"""OpenAI-compatible relay: put a private endpoint behind a client that insists on a key.

Why this exists
---------------
Several agent hosts (DeepSeek Harness via ``dsh-llm-pi-ai``, and other
OpenAI-compatible clients) *refuse to issue a request without an API key*.
The pi-ai implementation of the ``openai-completions`` / ``openai-responses``
protocols insists on a key or an ``Authorization`` header of its own, even
when the target endpoint does not want one - and a local bridge that *does*
check the header will reject a placeholder value with ``401``.

That is a genuine dead end for the common "private endpoint, no account"
setup, so this module ships a tiny reverse proxy that resolves it:

    client (wants a key)  ->  mas relay  ->  upstream (may want a different key)

The relay is deliberately small and standard-library only, like the rest of
the package:

* ``GET  /v1/models``            - proxied model catalogue;
* ``POST /v1/responses``         - proxied OpenAI Responses call (SSE aware);
* ``POST /v1/chat/completions``  - proxied chat call (SSE aware);
* the same paths without the ``/v1`` prefix, because hosts disagree about
  where the version segment belongs.

Two authentication modes:

* **strip** (default) - the client's ``Authorization`` header is dropped and
  the upstream sees an unauthenticated request. Right for a loopback
  endpoint that does not check credentials;
* **replace** - the client's key is dropped and the upstream receives the key
  named by ``--upstream-key-env``. Right for an upstream that checks a real
  credential the client cannot hold.

The relay only ever listens on the loopback interface. It holds no secrets
of its own: the upstream key is read from the environment at request time and
never written to disk, never echoed back, and never logged.

Safety notes
------------
* Binding to a non-loopback address is refused unless a token is supplied,
  for the same reason ``mas http`` refuses it: this proxy is a door into the
  user's model account.
* ``/health`` stays open so a supervisor can probe it; it exposes nothing
  but liveness and the version.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mas import __version__

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_UPSTREAM = "http://127.0.0.1:16024"

#: Environment variable holding the bearer token for the relay itself.
TOKEN_ENV = "MAS_RELAY_TOKEN"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}

#: Upstream paths the relay forwards. Anything else is refused, so the relay
#: cannot be turned into an open proxy for arbitrary URLs.
FORWARDED_PATHS = (
    "/v1/models",
    "/v1/responses",
    "/v1/chat/completions",
    "/responses",
    "/chat/completions",
    "/models",
)

#: Request headers that must not be copied to the upstream verbatim.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}


def is_loopback(host: str) -> bool:
    """True when *host* can only be reached from this machine."""
    return str(host or "").strip().lower() in _LOOPBACK_HOSTS


def normalize_upstream(base: str) -> str:
    """Normalize an upstream base URL into a prefix without a trailing slash."""
    text = str(base or "").strip()
    if not text:
        return DEFAULT_UPSTREAM
    if "://" not in text:
        text = "http://" + text
    return text.rstrip("/")


class RelayError(Exception):
    """A relay failure that should be reported to the client as-is."""


class RelayHandler(BaseHTTPRequestHandler):
    """Reverse proxy for the OpenAI-compatible surface."""

    server_version = f"openloop-mas-relay/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ util
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _presented_token(self) -> str:
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return (self.headers.get("X-MAS-Token") or "").strip()

    def _is_authorized(self) -> bool:
        expected = getattr(self.server, "auth_token", None)
        if not expected:
            return True
        presented = self._presented_token()
        if not presented:
            return False
        import hmac

        return hmac.compare_digest(presented, expected)

    def _require_auth(self) -> bool:
        if self._is_authorized():
            return True
        body = json.dumps({"error": "unauthorized: missing or invalid bearer token"}).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _upstream_headers(self) -> Dict[str, str]:
        """Build the headers sent upstream.

        The client's ``Authorization`` is always dropped: the relay either
        replaces it with the upstream key or sends no credential at all.
        Forwarding the client's placeholder key is exactly the failure this
        relay exists to prevent (the upstream would answer 401).
        """
        headers: Dict[str, str] = {}
        for key, value in self.headers.items():
            lowered = key.lower()
            if lowered in _HOP_BY_HOP:
                continue
            if lowered in ("authorization", "x-api-key", "cookie", "host"):
                continue
            headers[key] = value
        upstream_key = getattr(self.server, "upstream_key", "") or ""
        if upstream_key:
            headers["Authorization"] = "Bearer " + upstream_key
        headers.setdefault("Accept-Encoding", "identity")
        return headers

    def _upstream_url(self, path: str) -> str:
        upstream = getattr(self.server, "upstream", DEFAULT_UPSTREAM)
        return upstream + path

    def _upstream_candidates(self, path: str) -> List[str]:
        """The upstream paths to try, in order.

        Hosts disagree about where the version segment belongs, and so do
        gateways: a private bridge may serve ``/models`` and never answer
        ``/v1/models`` at all (a hang, not a 404). Trying the requested path
        first and then its sibling keeps the relay working against both
        shapes without asking the operator to guess.
        """
        candidates = [path]
        if path.startswith("/v1/"):
            candidates.append(path[3:])
        else:
            candidates.append("/v1" + path)
        return candidates

    def _open_upstream(self, method: str, path: str, body: bytes, timeout: float):
        """Open the upstream request, falling back to the sibling path on 404."""
        last_error: Optional[Exception] = None
        candidates = self._upstream_candidates(path)
        for index, candidate in enumerate(candidates):
            request = Request(
                self._upstream_url(candidate),
                data=body if body else None,
                headers=self._upstream_headers(),
                method=method,
            )
            try:
                return urlopen(request, timeout=timeout)
            except HTTPError as exc:
                # 404 means "this shape is not served here" - try the sibling
                # once. Any other status is the real answer and is passed on.
                if exc.code == 404 and index < len(candidates) - 1:
                    exc.close()
                    last_error = exc
                    continue
                raise
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                # A timeout is only a reason to try the sibling path when the
                # request was cheap: some private gateways answer /models but
                # *hang* on /v1/models instead of returning 404. Retrying a
                # POST after a timeout could duplicate a generation, so only
                # the idempotent GET routes fall back.
                if index < len(candidates) - 1 and method == "GET":
                    continue
                raise
        raise last_error if last_error else RelayError("upstream unreachable")

    def _proxy(self, method: str, path: str) -> None:
        body = self._read_body()
        timeout = float(getattr(self.server, "timeout", 900.0))
        try:
            upstream_response = self._open_upstream(method, path, body, timeout)
        except HTTPError as exc:
            # An upstream error is the client's business: pass the status and
            # the body through unchanged so the host can show the real reason.
            detail = exc.read()
            self.send_response(exc.code)
            content_type = exc.headers.get("Content-Type") or "application/json"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(detail)))
            self.end_headers()
            self.wfile.write(detail)
            return
        except URLError as exc:
            self._send_json(502, {"error": f"upstream unreachable: {exc.reason}"})
            return
        except (TimeoutError, OSError) as exc:
            self._send_json(504, {"error": f"upstream timeout: {exc}"})
            return

        content_type = upstream_response.headers.get("Content-Type") or "application/json"
        content_length = upstream_response.headers.get("Content-Length")
        self.send_response(upstream_response.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        if content_length:
            # A fixed-length body: relay the length so the client does not
            # wait for the connection to close.
            self.send_header("Content-Length", content_length)
        else:
            # A streamed body (SSE). Without a length the client would wait
            # for EOF, so the response is framed as chunked and the last
            # chunk terminates the stream.
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = upstream_response.read(8192)
                if not chunk:
                    break
                if content_length:
                    self.wfile.write(chunk)
                else:
                    self.wfile.write(b"%x\r\n" % len(chunk))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                self.wfile.flush()
            if not content_length:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The client hung up mid-stream. Normal, not an error.
            return
        finally:
            with contextlib.suppress(Exception):
                upstream_response.close()

    # ------------------------------------------------------------------ verbs
    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-MAS-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/health/"):
            self._send_json(200, {"ok": True, "name": "openloop-mas-relay", "version": __version__})
            return
        if not self._require_auth():
            return
        if path in ("/v1/models", "/models"):
            self._proxy("GET", path)
            return
        self._send_json(404, {"error": f"not found: {path}"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        if not self._require_auth():
            return
        if path in ("/v1/responses", "/responses", "/v1/chat/completions", "/chat/completions"):
            self._proxy("POST", path)
            return
        self._send_json(404, {"error": f"not found: {path}"})


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """A server that does not print a traceback when a client hangs up."""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve_relay(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    upstream: str = DEFAULT_UPSTREAM,
    upstream_key: str = "",
    token: Optional[str] = None,
    verbose: bool = False,
    timeout: float = 900.0,
) -> int:
    """Run the relay until interrupted.

    Refuses to listen on a non-loopback address without a token: this proxy
    carries the caller's model credential, so an unauthenticated public
    listener would hand that credential to anyone who found the port.
    """
    auth_token = token if token is not None else os.environ.get(TOKEN_ENV, "").strip()
    if not auth_token and not is_loopback(host):
        print(
            "拒绝启动：绑定到 %s 需要鉴权。\n"
            "  请设置环境变量 %s=<你的密钥>，或改用 --host 127.0.0.1。\n"
            "  （本中继会替客户端向上游转发凭据，不能裸奔在网络上）"
            % (host, TOKEN_ENV),
            file=sys.stderr,
        )
        return 2

    server = _QuietThreadingHTTPServer((host, int(port)), RelayHandler)
    server.verbose = verbose  # type: ignore[attr-defined]
    server.auth_token = auth_token  # type: ignore[attr-defined]
    server.upstream = normalize_upstream(upstream)  # type: ignore[attr-defined]
    server.upstream_key = upstream_key  # type: ignore[attr-defined]
    server.timeout = timeout  # type: ignore[attr-defined]

    print(f"OpenLoop MAS 中继已启动：http://{host}:{port} -> {server.upstream}")
    print(f"  健康检查 http://{host}:{port}/health")
    print("  转发     /v1/models /v1/responses /v1/chat/completions")
    if upstream_key:
        print("  上游鉴权 已替换为指定密钥（客户端的 Authorization 会被丢弃）")
    else:
        print("  上游鉴权 无（客户端的 Authorization 会被丢弃）")
    if auth_token:
        print(f"  中继鉴权 Bearer 令牌（来自 {TOKEN_ENV}，除 /health 外均需鉴权）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="mas-relay",
        description="OpenLoop MAS 中继：把私有端点藏在「必须带 key」的客户端后面",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--upstream",
        default=os.environ.get("MAS_RELAY_UPSTREAM", DEFAULT_UPSTREAM),
        help="上游 OpenAI 兼容端点（默认 %(default)s）",
    )
    parser.add_argument(
        "--upstream-key-env",
        default="",
        help="存放上游密钥的环境变量名；给了就用它替换客户端的 Authorization",
    )
    parser.add_argument("--token", default=None, help=f"中继自身鉴权令牌（默认读取 {TOKEN_ENV}）")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    upstream_key = ""
    if args.upstream_key_env:
        upstream_key = os.environ.get(args.upstream_key_env, "").strip()
        if not upstream_key:
            print(
                f"错误：环境变量 {args.upstream_key_env} 为空或未设置。",
                file=sys.stderr,
            )
            return 2
    return serve_relay(
        args.host,
        args.port,
        args.upstream,
        upstream_key,
        args.token,
        args.verbose,
        args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())

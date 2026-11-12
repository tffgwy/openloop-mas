"""HTTP + SSE transport for the OpenLoop MAS MCP server.

The stdio transport in :mod:`mas.mcp_server` is what a local agent uses.
This module exposes the same tool surface over HTTP so the runtime can be
reached by a remote client, a browser dashboard or a CI job:

* ``GET  /health``      - liveness plus the version.
* ``GET  /tools``       - the tool catalogue.
* ``POST /call``        - call one tool, JSON in, JSON out.
* ``GET  /events``      - Server-Sent Events stream of the run ledger, so a
  dashboard can watch a loop close in real time.

It is written against ``http.server`` from the standard library, so the
package still installs with zero third-party dependencies.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from mas import __version__
from mas.mcp_server import TOOLS, handle_tool

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class MASRequestHandler(BaseHTTPRequestHandler):
    """Request handler for the HTTP/SSE transport."""

    server_version = f"openloop-mas/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ util
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Keep the console readable: the stdio transport is the chatty one.
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = _json_bytes(payload) if "json" in content_type else str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        return data if isinstance(data, dict) else {"value": data}

    # ------------------------------------------------------------------ verbs
    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"

        if route == "/health":
            self._send(200, {"ok": True, "name": "openloop-mas", "version": __version__})
            return

        if route == "/tools":
            self._send(200, {"tools": TOOLS})
            return

        if route == "/events":
            self._stream_events(parsed)
            return

        if route == "/":
            self._send(
                200,
                {
                    "name": "openloop-mas",
                    "version": __version__,
                    "routes": ["/health", "/tools", "/call", "/events"],
                },
            )
            return

        self._send(404, {"error": f"not found: {route}"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route != "/call":
            self._send(404, {"error": f"not found: {route}"})
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return

        name = str(body.get("tool") or body.get("name") or "")
        arguments = body.get("arguments") or body.get("params") or {}
        if not name:
            self._send(400, {"error": "missing tool name"})
            return
        try:
            payload = handle_tool(name, arguments)
            self._send(200, {"tool": name, "result": payload})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported to the client
            self._send(500, {"error": str(exc), "tool": name})

    # ------------------------------------------------------------------ sse
    def _stream_events(self, parsed: Any) -> None:
        """Stream ledger growth as Server-Sent Events."""
        params = parse_qs(parsed.query)
        try:
            interval = float((params.get("interval") or ["2"])[0])
        except ValueError:
            interval = 2.0
        interval = max(0.2, min(interval, 60.0))
        run_id = (params.get("run_id") or [""])[0]
        max_events = int((params.get("max_events") or ["0"])[0])

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        sent = 0
        last_seq = 0
        try:
            while True:
                payload = self._collect_new_entries(run_id, last_seq)
                if payload:
                    last_seq = payload[-1].get("seq", last_seq)
                    for row in payload:
                        event = (
                            f"event: ledger\n"
                            f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
                        )
                        self.wfile.write(event.encode("utf-8"))
                        self.wfile.flush()
                        sent += 1
                else:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                if max_events and sent >= max_events:
                    break
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The client disconnected. That is a normal end of the stream.
            return

    def _collect_new_entries(self, run_id: str, after_seq: int) -> list:
        from mas.config import load_config
        from mas.engine import LoopEngine
        from mas.teams import load_team

        config = load_config()
        engine = LoopEngine(config, team=load_team(config.get("team")))
        rows = engine.ledger.entries()
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        return [row for row in rows if int(row.get("seq") or 0) > after_seq]


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """A server that does not print a traceback when a client hangs up.

    A browser closing an SSE stream is routine, not an error. Without this
    the console fills with ConnectionResetError noise that hides real
    problems.
    """

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve_http(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, verbose: bool = False) -> int:
    """Run the HTTP/SSE transport until interrupted."""
    server = _QuietThreadingHTTPServer((host, int(port)), MASRequestHandler)
    server.verbose = verbose  # type: ignore[attr-defined]
    server.daemon_threads = True
    print(f"OpenLoop MAS HTTP/SSE 传输已启动：http://{host}:{port}")
    print(f"  健康检查 http://{host}:{port}/health")
    print(f"  工具清单 http://{host}:{port}/tools")
    print(f"  事件流   http://{host}:{port}/events")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="mas-http", description="OpenLoop MAS MCP over HTTP + SSE"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    return serve_http(args.host, args.port, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())

"""mas/selftest.py - the flaky-bug gate (v0.7.62).

Why this module exists
----------------------
Two bugs in this project were *intermittent*: they passed every ordinary
test and then failed on a CI runner, or the other way round. Both were
real bugs, and both were found by hammering one path many times instead
of testing it once:

* the relay refused an unknown path without reading the request body, so
  the leftover bytes poisoned the *next* request on the same keep-alive
  connection (v0.7.59);
* a provider call that failed *after* the endpoint had answered could
  lose the usage report, and the run was then billed for zero tokens.

A bug that only shows up one time in twenty is not covered by a test
that runs once. So the method that found them becomes a first-class,
repeatable command: ``mas selftest``.

The rule of this module: **everything runs offline, against loopback
servers this module starts itself.** No network, no model endpoint, no
API key. A beginner can run it on a plane.

What it guarantees
------------------
Each check states an invariant, hammers it N times, and fails loudly
the first time the invariant breaks. The exit code is 0 only when every
invariant held on every iteration.
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from mas.providers import OpenAICompatProvider, ProviderError

#: How many times each invariant is hammered. The two historical bugs
#: showed up roughly once in twenty and once in a few hundred calls, so
#: the default has to be well above that to be meaningful - and small
#: enough to keep the command usable on a laptop.
DEFAULT_ITERATIONS = 200


# --------------------------------------------------------------------- utils
def _serve(handler_class: Any) -> Tuple[ThreadingHTTPServer, int]:
    """Start a loopback server in a thread and return it with its port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _stop(server: Any) -> None:
    with contextlib.suppress(Exception):
        server.shutdown()
    with contextlib.suppress(Exception):
        server.server_close()


def _post_json(port: int, path: str, payload: Any, timeout: float = 30.0) -> Tuple[int, str]:
    """POST *payload* and return ``(status, body_text)``.

    A transport failure is reported as status ``-1`` with the reason in
    the body - a broken connection is exactly the failure this module
    hunts for, so it must never be swallowed.
    """
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - the failure is the subject
        code = getattr(exc, "code", None)
        with contextlib.suppress(Exception):
            exc.read()
        if code is None:
            return -1, "%s: %s" % (type(exc).__name__, exc)
        return int(code), "%s: %s" % (type(exc).__name__, exc)


def _read_head(client: Any) -> Tuple[bytes, bytes]:
    """Read a HTTP response head (and whatever else arrived with it)."""
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = client.recv(4096)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > 65536:
            break
    return buffer, b""


def _read_body(client: Any, headers: Dict[str, str]) -> bytes:
    """Read the body a HTTP response declared, so the socket is clean."""
    try:
        length = int(headers.get("content-length", "0") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return b""
    body = b""
    while len(body) < length:
        chunk = client.recv(length - len(body))
        if not chunk:
            break
        body += chunk
    return body


def _parse_head(head: bytes) -> Tuple[int, Dict[str, str]]:
    """Return ``(status, headers)`` from a raw HTTP response head."""
    lines = head.split(b"\r\n")
    if not lines:
        return 0, {}
    parts = lines[0].split(b" ", 2)
    if len(parts) < 2:
        return 0, {}
    try:
        status = int(parts[1])
    except ValueError:
        status = 0
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        name, _sep, value = line.partition(b":")
        headers[name.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return status, headers


# ------------------------------------------------------------------- check 1
class _FakeUpstream(BaseHTTPRequestHandler):
    """A model endpoint that answers ``/v1/models`` and nothing else."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path in ("/v1/models", "/models"):
            payload = json.dumps({"data": [{"id": "selftest-model"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path in ("/v1/responses", "/responses"):
            payload = json.dumps({"ok": True, "echo": body.decode("utf-8", "replace")}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def check_relay_refusal_keeps_the_connection_usable(
    iterations: int = DEFAULT_ITERATIONS,
) -> Dict[str, Any]:

    """The relay must refuse a bad path *without* poisoning the connection.

    The historical bug (v0.7.59): the refusal did not read the request
    body, so the leftover bytes stayed in the socket. HTTP/1.1 keeps the
    connection alive, so the *next* request on that connection started
    in the middle of the old body and the server answered "400 Bad
    Request" - or aborted the connection. The client was promised a
    404 and got something else entirely, roughly one time in twenty.

    The invariant has two halves, because the relay is allowed to
    answer a refusal in two honest ways:

    * it may **close** the connection - then it must say so
      (``Connection: close``) and really close it;
    * it may **keep** the connection - then the next request on that
      very socket has to be answered correctly.

    What it may *not* do is keep the connection open, say nothing, and
    leave the unread body in the socket. That is the bug.
    """
    import socket

    from mas.relay import RelayHandler

    upstream, upstream_port = _serve(_FakeUpstream)
    relay = ThreadingHTTPServer(("127.0.0.1", 0), RelayHandler)
    relay.daemon_threads = True
    relay.auth_token = ""
    relay.upstream = "http://127.0.0.1:%d" % upstream_port
    relay.upstream_key = ""
    relay.verbose = False
    relay.timeout = 30.0
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    port = int(relay.server_address[1])

    failures: List[str] = []
    try:
        for index in range(iterations):
            payload = json.dumps({"probe": index, "pad": "x" * 300}).encode("utf-8")
            request = (
                b"POST /v1/../../etc/passwd HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n"
                b"\r\n" + payload
            )
            try:
                client = socket.create_connection(("127.0.0.1", port), timeout=30)
            except OSError as exc:
                failures.append("\u7b2c %d \u6b21\uff1a\u8fde\u4e0d\u4e0a\u4e2d\u7ee7\uff1a%s" % (index + 1, exc))
                break
            try:
                client.sendall(request)
                head, _rest = _read_head(client)
                status, headers = _parse_head(head)
                if status != 404:
                    failures.append(
                        "\u7b2c %d \u6b21\uff1a\u62d2\u7edd\u4e00\u4e2a\u8d8a\u754c\u8def\u5f84\u5e94\u56de 404\uff0c\u5b9e\u9645 %r"
                        % (index + 1, status)
                    )
                    break

                closes = "close" in headers.get("connection", "").lower()
                if closes:
                    # The relay announced the close. It has to really
                    # close, and a client that opens a new connection
                    # must be served correctly.
                    client.settimeout(10)
                    _read_body(client, headers)
                    try:
                        leftover = client.recv(4096)
                    except OSError:
                        leftover = b""
                    if leftover:
                        failures.append(
                            "\u7b2c %d \u6b21\uff1a\u4e2d\u7ee7\u8bf4\u4e86\u8981\u5173\u8fde\u63a5\uff0c\u5374\u8fd8\u6709\u6570\u636e\u7559\u5728 socket \u91cc"
                            % (index + 1)
                        )
                        break
                    client.close()
                    status2, _body2 = _post_json(port, "/v1/responses", {"model": "selftest-model"})
                    if status2 != 200:
                        failures.append(
                            "\u7b2c %d \u6b21\uff1a\u62d2\u7edd\u4e4b\u540e\u65b0\u5efa\u8fde\u63a5\u7684\u8bf7\u6c42\u5e94\u56de 200\uff0c\u5b9e\u9645 %r"
                            % (index + 1, status2)
                        )
                        break
                else:
                    # The relay keeps the connection. The body of the
                    # refused request must have been read, otherwise the
                    # next request on this socket starts in the middle
                    # of it - the historical bug.
                    client.sendall(
                        b"POST /v1/responses HTTP/1.1\r\n"
                        b"Host: 127.0.0.1\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: 25\r\n"
                        b"\r\n" + json.dumps({"model": "selftest"}).encode("utf-8")
                    )
                    head2, _rest2 = _read_head(client)
                    status2, _headers2 = _parse_head(head2)
                    if status2 != 200:
                        failures.append(
                            "\u7b2c %d \u6b21\uff1a\u62d2\u7edd\u4e4b\u540e\u540c\u4e00\u8fde\u63a5\u4e0a\u7684\u4e0b\u4e00\u4e2a\u8bf7\u6c42\u5e94\u56de 200\uff0c\u5b9e\u9645 %r"
                            % (index + 1, status2)
                        )
                        break
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u62d2\u7edd\u4e4b\u540e\u7684\u8fde\u63a5\u51fa\u9519\uff1a%s: %s"
                    % (index + 1, type(exc).__name__, exc)
                )
                break
            finally:
                with contextlib.suppress(Exception):
                    client.close()
            if len(failures) >= 5:
                break
    finally:
        _stop(relay)
        _stop(upstream)

    return {
        "name": "relay_refusal",
        "title": "\u4e2d\u7ee7\u62d2\u7edd\u8d8a\u754c\u8def\u5f84\u65f6\u4e0d\u4f1a\u6bd2\u67d3\u8fde\u63a5",
        "invariant": "\u62d2\u7edd\u56de 404\uff1b\u8981\u4e48\u660e\u786e\u5173\u95ed\u8fde\u63a5\uff0c\u8981\u4e48\u540c\u4e00 socket \u4e0a\u7684\u4e0b\u4e00\u4e2a\u8bf7\u6c42\u4ecd\u6709\u6b63\u786e\u7b54\u6848",
        "iterations": iterations,
        "failures": failures,
        "ok": not failures,
    }


# ------------------------------------------------------------------- check 2
class _BrokenShapeUpstream(BaseHTTPRequestHandler):
    """An endpoint that bills the caller but answers with a wrong shape.

    It reports usage but has no ``choices[0].message.content``. That is
    the shape that made the provider raise without the usage, so the run
    was charged for zero tokens.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        payload = json.dumps(
            {
                "usage": {"prompt_tokens": 30, "completion_tokens": 70, "total_tokens": 100},
                "choices": [{"unexpected": "shape"}],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def check_provider_keeps_the_usage_of_a_failed_call(
    iterations: int = DEFAULT_ITERATIONS,
) -> Dict[str, Any]:

    """A call that was billed must be charged, even when it failed.

    The historical bug: the endpoint answered and billed, the shape was
    wrong, and the raised error carried no usage - so the run reported
    zero tokens spent on a call that cost money.

    The invariant: the error raised by the provider always carries the
    usage the endpoint reported.
    """
    server, port = _serve(_BrokenShapeUpstream)
    failures: List[str] = []
    try:
        provider = OpenAICompatProvider(
            model="selftest-model", base_url="http://127.0.0.1:%d" % port
        )
        for index in range(iterations):
            try:
                provider.complete(system="s", prompt="p")
                failures.append(
                    "第 %d 次：形状不对本该报错，却成功返回了" % (index + 1)
                )
            except ProviderError as exc:
                tokens = getattr(exc, "tokens", 0)
                if tokens != 100:
                    failures.append(
                        "第 %d 次：已计费的调用丢了用量（tokens=%r，应为 100）"
                        % (index + 1, tokens)
                    )
            if len(failures) >= 5:
                break
    finally:
        _stop(server)

    return {
        "name": "provider_usage",
        "title": "已计费的失败调用照样带着用量",
        "invariant": "形状不对而报错时，错误里仍带着端点报的 total_tokens",
        "iterations": iterations,
        "failures": failures,
        "ok": not failures,
    }


# ------------------------------------------------------------------- check 3
def check_a_run_keeps_its_files_with_the_deliverable(
    iterations: int = 20,
) -> Dict[str, Any]:

    """Every run must leave one folder holding everything it produced.

    The invariant: after a run, the deliverable and the files the worker
    wrote live in the same folder, that folder is inside the state
    directory, and the run actually closed. This is the rule v0.7.61
    introduced; the self-test keeps it true instead of leaving it to a
    single unit test.
    """
    import os
    import tempfile

    from mas.engine import LoopEngine
    from mas.providers import Provider

    class _Writer(Provider):
        name = "selftest-writer"

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "worker":
                text = (
                    "\u4ea4\u4ed8\u7269\u6b63\u6587\n"
                    '<<<MAS:FILE path="notes/finding.txt">>>\n'
                    "\u7ed3\u8bba\uff1aSELFTEST\n"
                    "<<<MAS:END FILE>>>\n"
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "verifier":
                return {
                    "text": json.dumps(
                        {"verdict": "pass", "scores": [{"id": "c1", "pass": True, "note": "ok"}]}
                    ),
                    "usage": usage,
                    "raw": {},
                }
            return {"text": "{}", "usage": usage, "raw": {}}

    failures: List[str] = []
    for index in range(max(1, int(iterations))):
        with tempfile.TemporaryDirectory(prefix="mas-selftest-") as tmp:
            state = os.path.join(tmp, "state")
            config = {
                "provider": "mock",
                "model": "selftest-model",
                "state_dir": state,
                "workspace": "",
                "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
                "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
                "team": {"language": "zh"},
            }
            engine = LoopEngine(config, provider=_Writer(model="selftest-model"))
            try:
                result = engine.run(
                    goal="\u5199\u4e00\u4efd\u7ed3\u8bba",
                    criteria=[
                        {"id": "c1", "desc": "\u542b\u7ed3\u8bba",
                         "check": {"type": "contains", "arg": "\u6b63\u6587"}},
                        {"id": "c2", "desc": "\u6587\u4ef6\u5199\u51fa\u4e86",
                         "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
                    ],
                )
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append("\u7b2c %d \u6b21\uff1a\u8fd0\u884c\u62a5\u9519\uff1a%s" % (index + 1, exc))
                continue

            workspace = str(result.workspace or "")
            if not workspace:
                failures.append("\u7b2c %d \u6b21\uff1a\u8fd0\u884c\u6ca1\u6709\u5de5\u4f5c\u533a" % (index + 1))
                continue

            paths = {str(row.get("path") or "").replace("\\", "/")
                     for row in (result.files or [])}
            if "notes/finding.txt" not in paths:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5de5\u4f5c\u533a\u91cc\u6ca1\u6709 worker \u5199\u7684\u6587\u4ef6\uff08%s\uff09"
                    % (index + 1, ", ".join(sorted(paths)) or "\u65e0")
                )

            deliverable = os.path.join(workspace, "deliverable.md")
            if not os.path.isfile(deliverable):
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5de5\u4f5c\u533a\u91cc\u6ca1\u6709\u4ea4\u4ed8\u8bf4\u660e deliverable.md" % (index + 1)
                )

            written = os.path.join(workspace, "notes", "finding.txt")
            if not os.path.isfile(written):
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5de5\u4f5c\u533a\u91cc\u6ca1\u6709 notes/finding.txt" % (index + 1)
                )

            state_real = os.path.realpath(state)
            workspace_real = os.path.realpath(workspace)
            if not workspace_real.startswith(state_real + os.sep):
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5de5\u4f5c\u533a\u6ca1\u5728\u72b6\u6001\u76ee\u5f55\u91cc\uff08%s\uff09" % (index + 1, workspace)
                )

            if not result.closed:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u8fd0\u884c\u6ca1\u95ed\u73af\uff08status=%s\uff09" % (index + 1, result.status)
                )

            if len(failures) >= 5:
                break

    return {
        "name": "workspace_keeps_files",
        "title": "\u4e00\u6b21\u8fd0\u884c\u7684\u4ea7\u7269\u90fd\u5728\u540c\u4e00\u4e2a\u6587\u4ef6\u5939",
        "invariant": "\u4ea4\u4ed8\u8bf4\u660e\u4e0e worker \u5199\u7684\u6587\u4ef6\u540c\u5c45\uff0c\u4e14\u5728\u72b6\u6001\u76ee\u5f55\u91cc",
        "iterations": max(1, int(iterations)),
        "failures": failures,
        "ok": not failures,
    }


# ----------------------------------------------------------------- the gate
# The short name of a check is a property of the function, so that
# ``--only`` and the JSON output speak the same word.
check_relay_refusal_keeps_the_connection_usable.CHECK_NAME = "relay_refusal"  # type: ignore[attr-defined]
check_provider_keeps_the_usage_of_a_failed_call.CHECK_NAME = "provider_usage"  # type: ignore[attr-defined]
check_a_run_keeps_its_files_with_the_deliverable.CHECK_NAME = "workspace_keeps_files"  # type: ignore[attr-defined]

CHECKS = (
    check_relay_refusal_keeps_the_connection_usable,
    check_provider_keeps_the_usage_of_a_failed_call,
    check_a_run_keeps_its_files_with_the_deliverable,
)


def run_selftest(
    iterations: int = DEFAULT_ITERATIONS,
    only: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run every check and return a structured result."""
    selected = list(CHECKS)
    if only:
        wanted = {str(name).strip() for name in only}
        selected = [c for c in selected if c.__name__ in wanted or _check_name(c) in wanted]
        if not selected:
            raise ValueError("\u6ca1\u6709\u5339\u914d\u7684\u81ea\u68c0\u9879\uff1a%s" % ", ".join(sorted(wanted)))

    results: List[Dict[str, Any]] = []
    for check in selected:
        if check is check_a_run_keeps_its_files_with_the_deliverable:
            row = check(iterations=min(int(iterations), 20))
        else:
            row = check(iterations=int(iterations))
        results.append(row)
    ok = all(row["ok"] for row in results)
    return {
        "ok": ok,
        "iterations": int(iterations),
        "checks": results,
        "failed": [row["name"] for row in results if not row["ok"]],
    }


def _check_name(check: Any) -> str:
    """The short name of a check function.

    The name is the one the check reports (``relay_refusal``), so
    ``--only relay_refusal`` and the JSON output use the same word.
    """
    return str(getattr(check, "CHECK_NAME", "") or getattr(check, "__name__", ""))


def format_selftest(result: Dict[str, Any]) -> str:
    """Render the self-test so that a person can act on it."""
    lines: List[str] = []
    for row in result.get("checks", []):
        mark = "[OK  ]" if row.get("ok") else "[坏了]"
        lines.append("%s %s" % (mark, row.get("title", row.get("name", ""))))
        lines.append("       \u4fdd\u8bc1\uff1a%s" % row.get("invariant", ""))
        lines.append("       \u6253\u4e86 %s \u6b21" % row.get("iterations", 0))
        for failure in row.get("failures", []) or []:
            lines.append("       \u2192 %s" % failure)
    lines.append("")
    failed = result.get("failed") or []
    if failed:
        lines.append("\u7ed3\u8bba\uff1a%d \u9879\u81ea\u68c0\u5931\u8d25\uff08%s\uff09" % (len(failed), ", ".join(failed)))
    else:
        lines.append("\u7ed3\u8bba\uff1a\u6240\u6709\u81ea\u68c0\u9879\u90fd\u7ecf\u8fc7\u4e86\u3002")
    return "\n".join(lines) + "\n"

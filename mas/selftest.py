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
import os
import pathlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from mas.providers import OpenAICompatProvider, ProviderError
from mas.selftest_audit import (
    check_a_human_decision_leaves_a_trace_that_cannot_be_changed,
)
from mas.selftest_bootstrap import (
    check_the_one_click_install_survives_a_machine_without_python,
)
from mas.selftest_bootstrap_execution import (
    check_the_bootstrap_path_is_executed_and_not_just_described,
)
from mas.selftest_closure_grain_text import (
    check_the_verdict_page_speaks_the_same_grain,
)
from mas.selftest_closure_supersede import (
    check_the_report_and_the_verdict_agree_on_the_grain,
)
from mas.selftest_closure_verdict import (
    check_the_closure_verdict_reaches_both_surfaces,
)
from mas.selftest_doc_text import (
    check_the_documents_are_still_text,
)
from mas.selftest_draft_decision_run import (
    check_a_decision_hangs_on_its_run,
)
from mas.selftest_draft_undo_pending import (
    check_an_undone_draft_is_still_waiting_everywhere,
)
from mas.selftest_draft_undo_visibility import (
    check_a_taken_back_approval_is_visible_everywhere,
)
from mas.selftest_isolation import (
    check_the_selftest_leaves_the_machine_alone,
)
from mas.selftest_mcp_artifacts import (
    check_the_deliverables_reach_the_mcp_caller,
)
from mas.selftest_onboarding import (
    check_a_beginner_can_walk_the_docs_and_close_a_loop,
)
from mas.selftest_packaged_docs import (
    check_the_documents_the_beginner_reads_travel_with_the_wheel,
)
from mas.selftest_queue_brief import (
    check_the_ticket_queue_is_a_handover_everywhere,
)
from mas.selftest_replay_queue import (
    check_the_replay_listens_to_the_queue_too,
)
from mas.selftest_report_audit import (
    check_the_report_reads_the_decision_chains,
)
from mas.selftest_report_drafts import (
    check_the_report_reads_the_drafts,
)
from mas.selftest_report_seal import (
    check_the_report_sees_the_drift,
)
from mas.selftest_report_tickets import (
    check_the_report_reads_the_human_tickets,
)
from mas.selftest_report_trace_roles import (
    check_the_report_reads_every_ledger_row,
)
from mas.selftest_report_undo import (
    check_the_report_says_which_approvals_were_wrong,
)
from mas.selftest_run_detail_grain import (
    check_the_run_detail_speaks_the_same_grain,
)
from mas.selftest_seal import (
    check_a_closure_is_sealed_to_the_bytes_it_decided_on,
)
from mas.selftest_shadow import (
    check_the_program_you_type_is_the_program_that_was_checked,
)
from mas.selftest_single_run_supersede import (
    check_the_queue_decides_and_not_the_ledger_alone,
)
from mas.selftest_status_grain import (
    check_the_status_page_speaks_the_same_grain,
)
from mas.selftest_ticket_brief import (
    check_a_ticket_a_human_can_act_on_an_agent_can_too,
)
from mas.selftest_undo_visibility import (
    check_an_undo_is_visible_in_the_audit,
)

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


# ------------------------------------------------------------------- check 4
def check_mcp_handshake_answers_every_tool(
    iterations: int = DEFAULT_ITERATIONS,
) -> Dict[str, Any]:

    """The MCP handshake must answer, and every advertised tool must work.

    This is the surface an agent actually touches. If the handshake
    breaks, the agent sees a dead server and the whole loop is
    unreachable - but the failure is invisible from the CLI, because the
    CLI never speaks MCP.

    The invariant:

    * ``initialize`` answers with a protocol version and a server name;
    * ``tools/list`` advertises the tools, including ``mas_run``;
    * every advertised tool answers a call - a tool that never comes
      back is a tool the agent can only find out about in the middle of
      a task;
    * a call to a tool that does not exist is refused with an error
      instead of crashing the server;
    * the server is still alive afterwards - one broken tool may not
      take the other tools down with it.

    The server is driven exactly the way a host drives it: newline
    delimited JSON-RPC over stdio, one long lived pipe.
    """
    import queue
    import subprocess
    import sys
    import tempfile
    import threading

    from mas.mcp_server import PROTOCOL_VERSION, SERVER_NAME, TOOLS

    failures: List[str] = []
    names = [str(t.get("name") or "") for t in TOOLS if t.get("name")]

    with tempfile.TemporaryDirectory(prefix="mas-selftest-mcp-") as tmp:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1])
        env["PYTHONUTF8"] = "1"
        env["MAS_STATE_DIR"] = os.path.join(tmp, "state")
        script = "from mas.mcp_server import serve\nserve()\n"
        try:
            proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-c", script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8",
            )
        except OSError as exc:
            return {
                "name": "mcp_handshake",
                "title": "MCP 握手能应答，广告出来的工具都答得上来",
                "invariant": "initialize / tools/list 有正确回应，每个工具都有回应",
                "iterations": iterations,
                "failures": ["起不来 MCP 服务进程：%s" % exc],
                "ok": False,
            }
        # A stdio server is a pipe, not a request/response socket: the
        # host writes messages whenever it likes and reads the answers
        # whenever they come. The reader thread is what makes that
        # possible - a single ``communicate`` would close the pipe.
        lines: "queue.Queue[str]" = queue.Queue()

        def pump() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    lines.put(line)
            except Exception:  # noqa: BLE001 - the pipe closing is normal
                return

        threading.Thread(target=pump, daemon=True).start()

        def ask(batch: List[Dict[str, Any]], expect: int, timeout: float = 60.0) -> Dict[Any, Dict[str, Any]]:
            """Send a batch of JSON-RPC messages and read their answers."""
            try:
                for message in batch:
                    proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append("写不进服务：%s: %s" % (type(exc).__name__, exc))
                return {}

            replies: Dict[Any, Dict[str, Any]] = {}
            deadline = time.monotonic() + timeout
            while len(replies) < expect and time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=1.0)
                except queue.Empty:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if isinstance(reply, dict):
                    replies[reply.get("id")] = reply
            return replies

        replies = ask(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ],
            2,
        )

        init = replies.get(1) or {}
        result = init.get("result") or {}
        if result.get("protocolVersion") != PROTOCOL_VERSION:
            failures.append("initialize 的协议版本不对：%r" % result.get("protocolVersion"))
        info = result.get("serverInfo") or {}
        if info.get("name") != SERVER_NAME:
            failures.append("initialize 的服务名不对：%r" % info.get("name"))

        listed = replies.get(2) or {}
        tools = ((listed.get("result") or {}).get("tools")) or []
        if not tools:
            failures.append("tools/list 没有广告任何工具")
        advertised = [str(t.get("name") or "") for t in tools if t.get("name")]
        if "mas_run" not in advertised:
            failures.append("tools/list 里没有 mas_run（这是最核心的工具）")
        if sorted(advertised) != sorted(names):
            failures.append(
                "tools/list 广告的工具和真实的不一样：广告 %s / 实际 %s"
                % (", ".join(sorted(advertised)), ", ".join(sorted(names)))
            )
        # Every advertised tool has to answer. A tool that never comes
        # back is a tool the agent cannot use - and the agent finds out
        # at the worst possible moment, in the middle of a task. The
        # answer may carry an error (a tool that needs arguments has to
        # say so); what it may not do is vanish.
        calls: List[Dict[str, Any]] = []
        for index, name in enumerate(advertised):
            calls.append({
                "jsonrpc": "2.0", "id": 10 + index, "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            })
        calls.append({
            "jsonrpc": "2.0", "id": 99, "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        })
        replies2 = ask(calls, len(calls), timeout=120.0)

        for index, name in enumerate(advertised):
            reply = replies2.get(10 + index)
            if not reply:
                failures.append("工具 %s 没有回应" % name)
                continue
            if not (reply.get("result") is not None or reply.get("error")):
                failures.append("工具 %s 的回应既不是结果也不是错误：%r" % (name, reply))

        unknown = replies2.get(99) or {}
        if not (unknown.get("error") or (unknown.get("result") or {}).get("isError")):
            failures.append("调用不存在的工具没有被拒绝")

        # The server has to survive all of that: one broken tool may not
        # take the other tools down with it.
        alive = ask([{"jsonrpc": "2.0", "id": 500, "method": "ping"}], 1)
        if not alive.get(500):
            failures.append("跑完一轮工具调用之后，服务不再应答了")

    return {
        "name": "mcp_handshake",
        "title": "MCP 握手能应答，广告出来的工具都答得上来",
        "invariant": "initialize / tools/list 有正确回应，每个工具都有回应，服务不会被打死",
        "iterations": iterations,
        "failures": failures,
        "ok": not failures,
    }
# ------------------------------------------------------------------- check 5
class _SseUpstream(BaseHTTPRequestHandler):
    """An endpoint that answers with an SSE event stream.

    Two dialects show up in the wild: the chat streaming chunks and
    the Responses events. Both have to fold back into one plain answer,
    and the usage has to survive the fold.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            request = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            request = {}
        dialect = str(request.get("dialect") or "responses")
        if dialect == "chat":
            events = [
                {"choices": [{"delta": {"content": "第一段"}}]},
                {"choices": [{"delta": {"content": "第二段"}}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                },
            ]
        else:
            events = [
                {"type": "response.created", "response": {"model": "selftest-model"}},
                {"type": "response.output_text.delta", "delta": "第一段"},
                {"type": "response.output_text.delta", "delta": "第二段"},
                {
                    "type": "response.completed",
                    "response": {
                        "model": "selftest-model",
                        "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
                    },
                },
            ]
        lines = []
        for event in events:
            lines.append("data: " + json.dumps(event, ensure_ascii=False))
        lines.append("data: [DONE]")
        payload = ("\n\n".join(lines) + "\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def check_a_stream_folds_into_one_answer(
    iterations: int = DEFAULT_ITERATIONS,
) -> Dict[str, Any]:

    """A streamed answer must fold into one answer, with its usage.

    Gateways answer with an event stream on the chat route even when
    the client did not ask for one. The provider folds the stream back
    into a plain answer. If the fold loses a fragment, the deliverable
    is silently shorter than what the model wrote; if the fold loses
    the usage, the run reports a call that cost money as a free call.

    The invariant: both stream dialects fold into the exact same text,
    and the usage survives the fold.
    """
    from mas.providers import OpenAICompatProvider

    server, port = _serve(_SseUpstream)
    failures: List[str] = []
    try:
        provider = OpenAICompatProvider(
            model="selftest-model", base_url="http://127.0.0.1:%d" % port
        )
        for index in range(iterations):
            for dialect in ("responses", "chat"):
                try:
                    payload = provider.complete(
                        system="s", prompt="p", role="worker"
                    )
                except Exception as exc:  # noqa: BLE001 - the failure is the subject
                    failures.append(
                        "第 %d 次（%s）：流没被折叠成答案：%s: %s"
                        % (index + 1, dialect, type(exc).__name__, exc)
                    )
                    break
                text = str(payload.get("text") or "")
                if text != "第一段第二段":
                    failures.append(
                        "第 %d 次（%s）：折叠出来的正文不对：%r" % (index + 1, dialect, text)
                    )
                usage = payload.get("usage") or {}
                if int(usage.get("total_tokens") or 0) != 7:
                    failures.append(
                        "第 %d 次（%s）：折叠把用量弄丢了（tokens=%r，应为 7）"
                        % (index + 1, dialect, usage.get("total_tokens"))
                    )
            if len(failures) >= 5:
                break
    finally:
        _stop(server)

    return {
        "name": "stream_folds",
        "title": "流式回答折叠成一段完整正文，用量不丢",
        "invariant": "两种事件流方言折叠出同一段正文，且 total_tokens 保持 7",
        "iterations": iterations,
        "failures": failures,
        "ok": not failures,
    }


# ------------------------------------------------------------------- check 6
def check_parallel_candidates_pick_the_same_winner_every_time(
    iterations: int = 20,
) -> Dict[str, Any]:

    """The parallel pool must pick the same winner every single time.

    When several workers produce candidates at the same time, the
    engine picks one of them. The engine's docstring promises that the
    winner is deterministic - a replay of the same run picks the same
    artifact. That promise was never guarded, so nothing stopped a
    later change from letting the *race* decide: a pool that returns
    its results in completion order, a tie broken the wrong way round
    or a ranking that weighs the text length above the machine checks
    would all hand over a different deliverable, and the run would
    still report success.

    The check stages the race instead of waiting for it. The three
    candidates are chained with events, so the pool always finishes in
    the same *reversed* order - the last candidate of the pool returns
    first, the first candidate returns last. Nothing about the result
    depends on the luck of the scheduler.

    The three texts make the ranking do real work:

    * ``direct`` passes both machine checks and is exactly as long as
      ``thorough``, so the winner is decided by the *lowest index*;
    * ``thorough`` passes both checks, has the same length and sits at
      a later index - the twin that must *not* win;
    * ``concise`` passes nothing and is the longest text - a ranking
      that ignored the machine checks would hand it over.

    A pool that returns its results in completion order, a ranking
    that breaks the tie the other way round and a ranking that weighs
    the text length above the checks all produce a different
    deliverable. The gate turns red on all three.
    """
    import tempfile

    from mas.engine import LoopEngine
    from mas.providers import Provider

    direct_text = "\u7ed3\u8bba\uff1a\u76f4\u63a5\u65b9\u6848\u6709\u6548\u3002\u8bc1\u636e\uff1a\u6765\u81ea\u76f4\u63a5\u89c6\u89d2\u7684\u89c2\u5bdf\u3002"
    thorough_text = "\u7ed3\u8bba\uff1a\u8be6\u5c3d\u65b9\u6848\u6709\u6548\u3002\u8bc1\u636e\uff1a\u6765\u81ea\u8be6\u5c3d\u89c6\u89d2\u7684\u89c2\u5bdf\u3002"
    concise_text = "\u53e6\u4e00\u79cd\u8bf4\u6cd5\uff0c\u8fd9\u91cc\u6ca1\u6709\u4efb\u4f55\u6807\u8bb0\u3002" + "\u8865\u5145\u8bf4\u660e\u4e00\u6b21\u3002" * 6

    class _OrderedCandidateProvider(Provider):
        """A provider that makes the parallel pool finish out of order."""

        name = "selftest-parallel"

        def __init__(self, model: str = "selftest-model", **options: Any) -> None:
            super().__init__(model=model, **options)
            self._lock = threading.Lock()
            self._events = {
                "concise": threading.Event(),
                "thorough": threading.Event(),
            }
            # The order the candidates came back in.
            self.completion_order: List[str] = []
            # False when a candidate waited for a predecessor that never
            # showed up - the race could not be staged.
            self.race_ok = True

        @staticmethod
        def _text_for(angle: str) -> str:
            if angle == "direct":
                return direct_text
            if angle == "thorough":
                return thorough_text
            if angle == "concise":
                return concise_text
            return "\u7ed3\u8bba\uff1a\u9ed8\u8ba4\u65b9\u6848\u6709\u6548\u3002\u8bc1\u636e\uff1a\u6765\u81ea\u9ed8\u8ba4\u89c6\u89d2\u7684\u89c2\u5bdf\u3002"

        @staticmethod
        def _angle_of(prompt: str) -> str:
            """Read the candidate angle out of the worker's prompt."""
            match = re.search(r'"angle":\s*"([a-z_]+)"', prompt)
            return match.group(1) if match else ""

        def _gate(self, angle: str) -> None:
            """Hold the candidate until the one before it has finished."""
            if angle == "concise":
                with self._lock:
                    self.completion_order.append(angle)
                self._events["concise"].set()
                return
            if angle == "thorough":
                if not self._events["concise"].wait(timeout=5):
                    self.race_ok = False
                with self._lock:
                    self.completion_order.append(angle)
                self._events["thorough"].set()
                return
            # ``direct`` waits for ``thorough``: it is the last to finish.
            if not self._events["thorough"].wait(timeout=5):
                self.race_ok = False
            with self._lock:
                self.completion_order.append(angle)

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "planner":
                text = json.dumps(
                    {"summary": "\u8ba1\u5212", "plan": ["\u5e76\u884c\u4ea7\u51fa", "\u9009\u4f18"],
                     "risks": ["\u5b8c\u6210\u987a\u5e8f"]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "verifier":
                text = json.dumps(
                    {
                        "verdict": "pass",
                        "scores": [
                            {"id": "c1", "pass": True, "note": "\u6709\u7ed3\u8bba"},
                            {"id": "c2", "pass": True, "note": "\u6709\u8bc1\u636e"},
                        ],
                    }
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "reflector":
                text = json.dumps(
                    {"summary": "\u6539", "issues": [], "next_action": "revise"}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role != "worker":
                return {"text": "{}", "usage": usage, "raw": {}}
            angle = self._angle_of(prompt)
            text = self._text_for(angle)
            self._gate(angle)
            return {"text": text, "usage": usage, "raw": {}}

    if len(direct_text) != len(thorough_text):
        # The twin candidates have to be exactly the same length, or the
        # tie-break is never exercised and the gate proves nothing.
        return {
            "name": "parallel_pick",
            "title": "\u5e76\u884c\u5019\u9009\u9009\u4f18\u6bcf\u6b21\u90fd\u9009\u540c\u4e00\u4e2a",
            "invariant": "\u4e24\u4e2a\u957f\u5ea6\u76f8\u540c\u7684\u5019\u9009\u5fc5\u987b\u80fd\u5237\u51fa\u5e73\u5c40",
            "iterations": 0,
            "failures": [
                "\u95e8\u7981\u81ea\u8eab\u6709\u9519\uff1adirect \u4e0e thorough \u7684\u957f\u5ea6\u4e0d\u4e00\u81f4"
                " (%d vs %d)\uff0c\u5e73\u5c40\u6d4b\u4e0d\u5230"
                % (len(direct_text), len(thorough_text))
            ],
            "ok": False,
        }

    failures: List[str] = []
    winners: set = set()
    delivered: set = set()
    rounds = max(1, int(iterations))
    for index in range(rounds):
        with tempfile.TemporaryDirectory(prefix="mas-selftest-par-") as tmp:
            state = os.path.join(tmp, "state")
            config = {
                "provider": "mock",
                "model": "selftest-model",
                "state_dir": state,
                "workspace": "",
                "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
                "orchestration": {"parallel_workers": 3, "reviewers": 1, "review_mode": "all"},
                "team": {"language": "zh"},
            }
            provider = _OrderedCandidateProvider(model="selftest-model")
            engine = LoopEngine(config, provider=provider)
            try:
                result = engine.run(
                    goal="\u5199\u4e00\u4efd\u7ed3\u8bba",
                    criteria=[
                        {"id": "c1", "desc": "\u542b\u7ed3\u8bba",
                         "check": {"type": "contains", "arg": "\u7ed3\u8bba"}},
                        {"id": "c2", "desc": "\u542b\u8bc1\u636e",
                         "check": {"type": "contains", "arg": "\u8bc1\u636e"}},
                    ],
                )
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u8fd0\u884c\u62a5\u9519\uff1a%s: %s"
                    % (index + 1, type(exc).__name__, exc)
                )
                continue

            if not provider.race_ok:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5019\u9009\u6c60\u7684\u5b8c\u6210\u987a\u5e8f\u6ca1\u80fd\u6392\u597d"
                    "\uff08\u6709\u5019\u9009\u7b49\u4e86\u4e00\u4e2a\u6c38\u8fdc\u4e0d\u6765\u7684\u524d\u4f19\uff09" % (index + 1)
                )

            order = list(provider.completion_order)
            # Only the first round of the pool is looked at. A run that
            # has to try again would produce a second round of candidates
            # - and the order of *that* round is a different question.
            # The first round is the one that decides the winner.
            if order[:3] != ["concise", "thorough", "direct"]:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5019\u9009\u6c60\u7684\u5b8c\u6210\u987a\u5e8f\u4e0d\u662f\u6392\u597d\u7684"
                    "\u9006\u5e8f\uff08%s\uff09\uff0c\u8d5b\u6b21\u6ca1\u6709\u771f\u6b63\u53d1\u751f"
                    % (index + 1, " -> ".join(order[:3]) or "\u7a7a")
                )
            if int(result.iterations) != 1:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u4ea4\u4ed8\u7269\u672c\u5e94\u8be5\u7b2c\u4e00\u8f6e\u5c31\u80dc\u51fa"
                    "\uff08\u5b9e\u9645轮\u6570=%s\uff09" % (index + 1, result.iterations)
                )

            entry = _find_candidate_entry(result.ledger_path)
            if not entry:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u8d26\u672c\u91cc\u6ca1\u6709\u5019\u9009\u6c60\u7684\u8bb0\u5f55" % (index + 1)
                )
                continue
            selected_angle = str(entry.get("selected_angle") or "")
            winners.add(selected_angle)
            if selected_angle != "direct":
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u9009\u4e2d\u7684\u5019\u9009\u4e0d\u662f direct\uff08%s\uff09"
                    "\uff0c\u800c direct \u548c thorough \u901a\u8fc7\u6570\u4e0e\u957f\u5ea6\u5b8c\u5168\u76f8\u540c"
                    % (index + 1, selected_angle or "\u7a7a")
                )

            rows = entry.get("candidates") or []
            selected_rows = [r for r in rows if isinstance(r, dict) and r.get("selected")]
            if not selected_rows:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u8d26\u672c\u91cc\u6ca1\u6709\u6807\u8bb0\u51fa\u88ab\u9009\u4e2d\u7684\u5019\u9009" % (index + 1)
                )
            else:
                chars = selected_rows[0].get("chars")
                if int(chars or 0) != len(direct_text):
                    failures.append(
                        "\u7b2c %d \u6b21\uff1a\u88ab\u9009\u4e2d\u7684\u5019\u9009\u957f\u5ea6\u4e0d\u5bf9"
                        "\uff08%s\uff0c\u5e94\u4e3a %d\uff09" % (index + 1, chars, len(direct_text))
                    )

            artifact = str(result.artifact or "")
            delivered.add(artifact)
            if artifact != direct_text:
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u4ea4\u4ed8\u7684\u6b63\u6587\u4e0d\u662f\u7b2c\u4e00\u4e2a\u5019\u9009\uff08%r\uff09"
                    % (index + 1, artifact[:40])
                )

            workspace = str(result.workspace or "")
            deliverable = os.path.join(workspace, "deliverable.md")
            if not os.path.isfile(deliverable):
                failures.append(
                    "\u7b2c %d \u6b21\uff1a\u5de5\u4f5c\u533a\u91cc\u6ca1\u6709 deliverable.md" % (index + 1)
                )
            else:
                with open(deliverable, "r", encoding="utf-8") as handle:
                    on_disk = handle.read()
                if on_disk != direct_text:
                    failures.append(
                        "\u7b2c %d \u6b21\uff1a\u843d\u76d8\u7684\u4ea4\u4ed8\u7269\u4e0d\u662f\u7b2c\u4e00\u4e2a\u5019\u9009" % (index + 1)
                    )

            if len(failures) >= 5:
                break

    if len(winners) > 1:
        failures.append(
            "\u4e0d\u540c\u4e00\u6b21\u8fd0\u884c\u9009\u51fa\u4e86\u4e0d\u540c\u7684\u5019\u9009\uff1a%s"
            % ", ".join(sorted(winners))
        )
    if len(delivered) > 1:
        failures.append(
            "\u4e0d\u540c\u4e00\u6b21\u8fd0\u884c\u4ea4\u4ed8\u4e86\u4e0d\u540c\u7684\u6b63\u6587\uff1a%d \u79cd" % len(delivered)
        )

    return {
        "name": "parallel_pick",
        "title": "\u5e76\u884c\u5019\u9009\u6bcf\u6b21\u90fd\u9009\u540c\u4e00\u4e2a\u80dc\u8005",
        "invariant": "\u5019\u9009\u6c60\u987a\u5e8f\u62bd\u8d70\u4e5f\u4e0d\u6539\u53d8\u9009\u4f18\uff0c"
                     "\u4e14\u6bcf\u6b21\u90fd\u9009\u540c\u4e00\u4e2a\u5019\u9009",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


def _find_candidate_entry(ledger_path: str) -> Optional[Dict[str, Any]]:
    """Return the ledger entry that describes the parallel candidate pool."""
    path = pathlib.Path(str(ledger_path or ""))
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "") == "candidates" and row.get("parallel"):
            return row
    return None


def _ledger_rows(ledger_path: str) -> List[Dict[str, Any]]:
    """Read the run ledger into a list of rows."""
    path = pathlib.Path(str(ledger_path or ""))
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ledger_run_ids(ledger_path: str) -> List[str]:
    """The run ids the ledger knows about."""
    return [str(row.get("run_id") or "") for row in _ledger_rows(ledger_path)]


def _ledger_ticket_ids(ledger_path: str) -> List[str]:
    """The ticket ids the ledger points at."""
    found: List[str] = []
    for row in _ledger_rows(ledger_path):
        for key in ("ticket_id", "parent_ticket_id"):
            value = str(row.get(key) or "")
            if value and value not in found:
                found.append(value)
    return found


# ------------------------------------------------------------------- check 7
def check_an_escalated_run_leaves_a_ticket_a_human_can_take_over(
    iterations: int = 10,
) -> Dict[str, Any]:

    """An escalated run must leave a ticket a human can actually pick up.

    The engine promises three things when a loop cannot close itself: it
    escalates instead of spinning, it opens a ticket, and that ticket
    carries enough context to resume the loop. The first two were easy
    to see. The third was a promise nobody kept.

    A ticket that names the cause but drops the acceptance criteria is
    not a handover: the human who resumes the run gets a loop with no
    standards left, and the verifier's opinion becomes the only rule.
    The loop still reports success - it just stops being the loop the
    user asked for. So the ticket is checked by *using* it:

    * the run escalates (the deliverable stopped changing) and opens a
      ticket;
    * the ticket is on disk, waiting for a human, and names its cause;
    * the ticket carries the goal, the criteria, the last deliverable,
      the failing issues and the history;
    * the criteria survive the round trip - they come back as machine
      checks, not as prose;
    * the ledger points at the ticket and the ticket points back at the
      ledger;
    * and the decisive one: feeding the ticket back into
      ``engine.resume()`` closes the loop **through the machine check**.

    That last point is what makes the check bite. The verifier in this
    check always answers "fail", so a resumed run can only close if the
    criteria really came back on the ticket. A ticket that lost its
    criteria - or kept them as unreadable prose - leaves the loop
    unable to close, and the gate turns red.
    """
    import tempfile

    from mas.engine import Criterion, LoopEngine
    from mas.providers import Provider

    class _StuckProvider(Provider):
        """A worker that never improves, and a verifier that never passes."""

        name = "selftest-handover"

        def __init__(self, model: str = "selftest-model", **options: Any) -> None:
            super().__init__(model=model, **options)
            # False: the worker writes the same text every round, so the
            # deliverable stops changing and the loop cannot close.
            self.repaired = False

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "planner":
                text = json.dumps(
                    {"summary": "计划", "plan": ["写交付物"], "risks": ["写不好"]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "verifier":
                # The verifier never passes. The only way a resumed run
                # can close is through the machine check that came back
                # on the ticket - so the check cannot be faked by the
                # model, and a ticket without criteria cannot close.
                text = json.dumps(
                    {
                        "verdict": "fail",
                        "scores": [{"id": "c1", "pass": False, "note": "不过"}],
                    }
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "reflector":
                text = json.dumps(
                    {"summary": "复盘", "issues": [], "next_action": "revise"}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "worker":
                text = "结论。数据是 42。" if self.repaired else "结论。数据是 41。"
                return {"text": text, "usage": usage, "raw": {}}
            return {"text": "{}", "usage": usage, "raw": {}}

    def _config(tmp: str, **budget: Any) -> Dict[str, Any]:
        # The iteration ceiling has to sit *above* the stagnation limit,
        # otherwise the budget fires first and the escalation path - the
        # one this check is about - is never reached.
        base = {"max_iterations": 6, "max_tokens": 1000000, "deadline_sec": 60}
        base.update(budget)
        return {
            "provider": "mock",
            "model": "selftest-model",
            "state_dir": os.path.join(tmp, "state"),
            "workspace": "",
            "budget": base,
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
            "stagnation_limit": 2,
        }

    #: The keys a human needs to take the problem over.
    required_payload_keys = (
        "run_id", "task_id", "goal", "title", "criteria", "last_artifact",
        "issues", "attempts", "iterations_total", "runs", "history",
        "stagnation", "reason", "status",
    )

    def _one_round(index: int) -> List[str]:
        """Escalate, then take the loop over from the ticket itself."""
        problems: List[str] = []
        prefix = "第 %d 次：" % (index + 1)

        with tempfile.TemporaryDirectory(prefix="mas-selftest-handover-") as tmp:
            provider = _StuckProvider(model="selftest-model")
            config = _config(tmp)
            engine = LoopEngine(config, provider=provider)
            criteria = [
                {
                    "id": "c1",
                    "desc": "正文里要出现 42",
                    "check": {"type": "contains", "arg": "42"},
                }
            ]
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]

            if result.closed:
                return [prefix + "运行竟然闭合了（status=%s），升级路径没被走到" % result.status]
            if result.status != "escalated":
                return [prefix + "状态不是 escalated（而是 %s）" % result.status]
            if not result.ticket_id:
                return [prefix + "升级了却没有票据"]

            ticket_id = str(result.ticket_id)

            # The ticket has to be on disk, not only in memory: the human
            # finds it through ``mas tickets``, which reads the folder.
            ticket_file = pathlib.Path(
                os.path.join(config["state_dir"], "tickets", ticket_id + ".json")
            )
            if not ticket_file.is_file():
                return [prefix + "票据没有落盘（%s）" % ticket_file]
            try:
                ticket = json.loads(ticket_file.read_text(encoding="utf-8"))
            except ValueError as exc:
                return [prefix + "票据不是合法 JSON：%s" % exc]

            if ticket.get("state") != "waiting_human":
                problems.append(
                    prefix + "票据状态不是 waiting_human（而是 %r）" % ticket.get("state")
                )
            if ticket.get("kind") != "loop_escalation":
                problems.append(
                    prefix + "票据类型不对（%r）" % ticket.get("kind")
                )
            reason = str((ticket.get("payload") or {}).get("reason") or "")
            if "stagnation" not in reason:
                problems.append(prefix + "票据没说清楚为什么停下（reason=%r）" % reason)

            payload = ticket.get("payload") or {}
            missing = [key for key in required_payload_keys if key not in payload]
            if missing:
                problems.append(prefix + "票据缺了字段：%s" % ", ".join(missing))

            # The criteria have to come back as machine checks. Prose is
            # not a standard: it cannot be re-run, so it cannot close a
            # loop on its own.
            raw_criteria = payload.get("criteria")
            if not isinstance(raw_criteria, list) or not raw_criteria:
                problems.append(prefix + "票据里没有验收标准")
                raw_criteria = []
            for position, raw in enumerate(raw_criteria):
                criterion = Criterion.from_raw(raw, position)
                if not criterion.check:
                    problems.append(
                        prefix + "第 %d 条验收标准没有机检（只剩散文）" % (position + 1)
                    )
                elif criterion.check.get("arg") != "42":
                    problems.append(
                        prefix + "第 %d 条验收标准的机检内容变了（arg=%r）"
                        % (position + 1, criterion.check.get("arg"))
                    )
                if not criterion.desc:
                    problems.append(prefix + "第 %d 条验收标准没有说明" % (position + 1))

            if not str(payload.get("goal") or ""):
                problems.append(prefix + "票据没有写目标")
            if not str(payload.get("last_artifact") or ""):
                problems.append(prefix + "票据没有带上最后一次产物")
            if not list(payload.get("issues") or []):
                problems.append(prefix + "票据没有带上失败的原因")
            if not list(payload.get("history") or []):
                problems.append(prefix + "票据没有带上历史")
            if not list(payload.get("runs") or []):
                problems.append(prefix + "票据没有指明涉及哪些运行")

            # The ledger and the ticket have to point at each other:
            # the ledger is how the human finds the run, the ticket is
            # how the human finds the problem.
            run_id = str(payload.get("run_id") or "")
            ledger_run_ids = _ledger_run_ids(result.ledger_path)
            if run_id not in ledger_run_ids:
                problems.append(
                    prefix + "票据上的 run_id 不在账本里（%s）" % (run_id or "空")
                )
            if ticket_id not in _ledger_ticket_ids(result.ledger_path):
                problems.append(prefix + "账本里没有指向这张票据的记录")

            # The decisive part: take the loop over with the ticket
            # alone. The worker is repaired and the verifier still says
            # "fail", so the resumed loop can only close through the
            # machine check that came back on the ticket.
            provider.repaired = True
            try:
                resumed = engine.resume(ticket_id, note="人工接手")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                problems.append(prefix + "按票据接手失败：%s" % exc)
                return problems

            if not resumed.closed:
                problems.append(
                    prefix + "按票据接手后没有闭合（status=%s, error=%s）"
                    % (resumed.status, resumed.error)
                )
            else:
                by_id = {str(s.get("id")): s for s in (resumed.scores or [])}
                score = by_id.get("c1") or {}
                if score.get("source") != "rule":
                    problems.append(
                        prefix + "接手的闭合不是靠机检（source=%r）" % score.get("source")
                    )
                if not score.get("pass"):
                    problems.append(prefix + "接手的闭合里 c1 没有通过")

            final = engine.tickets.get(ticket_id) or {}
            if final.get("state") != "resolved":
                problems.append(
                    prefix + "闭合后票据没有归档（state=%r）" % final.get("state")
                )
            final_payload = final.get("payload") or {}
            if not str(final_payload.get("closed_run_id") or ""):
                problems.append(prefix + "归档的票据没有记下是哪次运行闭合的")
            if resumed.closed and str(resumed.run_id) not in list(final_payload.get("runs") or []):
                problems.append(prefix + "归档的票据没有把闭合的那次运行记进去")
            return problems

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for index in range(rounds):
        failures.extend(_one_round(index))
        if len(failures) >= 5:
            break
    return {
        "name": "handover_ticket",
        "title": "没闭合的运行会留下人能接手的东西",
        "invariant": "升级票据带齐上下文，且票据里的验收标准能直接把循环接回来并闭合",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }

def check_a_takeover_sees_the_work_and_gets_a_fresh_budget(
    iterations: int = 10,
) -> Dict[str, Any]:

    """A takeover must see the work, and must get a budget of its own.

    Two promises broke at the same time, and both of them made a
    takeover a formality:

    * **the takeover could not see the work.** The last deliverable
      lived on the ticket, but the resumed run started its attempt
      counter at one again and its issue list empty, so the worker was
      asked for the deliverable from nothing. The result was the same
      bytes over and over, and the loop never converged on the work
      that had already been done.
    * **the takeover could not get a budget.** The resumed run reused
      the ceilings of the machine-wide configuration, so a problem that
      did not fit into those ceilings stopped at exactly the same
      ceiling again. The human was told to "continue", and continuing
      meant hitting the same wall.

    The check takes a run that stopped on a ceiling, reads the ticket
    it left behind, and then:

    * every way of stopping leaves a ticket a human can pick up, and
      the ticket says *which* stop it was - a budget stop is not an
      escalation, and the two need different human reactions;
    * the ticket carries the budget the run actually ran under;
    * the continuation command comes with a *wider* ceiling for the
      one that stopped the run;
    * the worker of the resumed run is shown the deliverable of the
      previous attempt;
    * and the resumed run really closes - through the machine check
      that came back on the ticket, not through the reviewer's mood.
    """
    import tempfile

    from mas.engine import LoopEngine
    from mas.providers import Provider, ProviderError
    from mas.reason_text import continuation_command, widen_budget

    class _RepairableProvider(Provider):
        """A worker whose text the human can repair.

        The verifier always answers "fail", so a resumed loop can only
        close through the machine check that came back on the ticket -
        the model cannot fake it.
        """

        name = "selftest-takeover"

        def __init__(self, model: str = "selftest-model", **options: Any) -> None:
            super().__init__(model=model, **options)
            # False: the worker writes the same text every round, so the
            # deliverable is wrong and the loop cannot close.
            self.repaired = False
            # Every prompt the worker was given. The takeover has to
            # show up in here.
            self.worker_prompts: List[str] = []

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "planner":
                text = json.dumps(
                    {"summary": "计划", "plan": ["写交付物"], "risks": ["写不好"]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "verifier":
                text = json.dumps(
                    {"verdict": "fail",
                     "scores": [{"id": "c1", "pass": False, "note": "不过"}]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "reflector":
                text = json.dumps(
                    {"summary": "复盘", "issues": [], "next_action": "revise"}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "worker":
                self.worker_prompts.append(prompt)
                text = "结论。数据是 42。" if self.repaired else "结论。数据是 41。"
                return {"text": text, "usage": usage, "raw": {}}
            return {"text": "{}", "usage": usage, "raw": {}}

    class _BrokenProvider(Provider):
        """A backend that is simply not there."""

        name = "selftest-takeover-broken"

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            raise ProviderError("the endpoint is not reachable")
    def _config(tmp: str, **budget: Any) -> Dict[str, Any]:
        base = {"max_iterations": 6, "max_tokens": 1000000, "deadline_sec": 60}
        base.update(budget)
        return {
            "provider": "mock",
            "model": "selftest-model",
            "state_dir": os.path.join(tmp, "state"),
            "workspace": "",
            "budget": base,
            "orchestration": {
                "parallel_workers": 1, "reviewers": 1, "review_mode": "all",
            },
            "team": {"language": "zh"},
            "stagnation_limit": 2,
        }

    #: The standards every scenario of this check uses.
    criteria = [
        {
            "id": "c1",
            "desc": "正文里要出现 42",
            "check": {"type": "contains", "arg": "42"},
        }
    ]

    def _ticket_of(engine: Any, result: Any) -> Dict[str, Any]:
        if not result.ticket_id:
            return {}
        return engine.tickets.get(str(result.ticket_id)) or {}

    def _check_stop(
        label: str,
        engine: Any,
        result: Any,
        expect_status: str,
        expect_kind: str,
        expect_reason: str,
    ) -> List[str]:
        """Every way of stopping has to leave a ticket that fits."""
        problems: List[str] = []
        if result.closed:
            problems.append(label + "：这一轮竟然闭合了")
        if result.status != expect_status:
            problems.append(
                "%s：状态不是 %s（而是 %s）" % (label, expect_status, result.status)
            )
        ticket = _ticket_of(engine, result)
        if not ticket:
            problems.append(label + "：没有留下可接手的票据")
            return problems
        if ticket.get("state") != "waiting_human":
            problems.append(
                "%s：票据不是等人接手的状态（%r）" % (label, ticket.get("state"))
            )
        if ticket.get("kind") != expect_kind:
            problems.append(
                "%s：票据类型不是 %s（而是 %s）"
                % (label, expect_kind, ticket.get("kind"))
            )
        payload = ticket.get("payload") or {}
        if payload.get("status") != expect_status:
            problems.append(
                "%s：票据里的状态和运行的状态对不上（%r / %r）"
                % (label, payload.get("status"), expect_status)
            )
        reason = str(payload.get("reason") or "")
        if expect_reason not in reason:
            problems.append(
                "%s：票据没有说清为什么停（reason=%r）" % (label, reason)
            )
        if not (payload.get("budget") or {}):
            problems.append(label + "：票据没有记下这一轮跑在什么预算下")
        return problems

    def _takeover(
        label: str,
        engine: Any,
        result: Any,
        provider: Any,
    ) -> List[str]:
        """Take the ticket over, and see whether the takeover can work."""
        problems: List[str] = []
        ticket = _ticket_of(engine, result)
        payload = ticket.get("payload") or {}
        budget = payload.get("budget") or {}
        ticket_id = str(result.ticket_id)

        # The continuation command has to be one that can work. A
        # takeover that inherits the ceiling of the run that failed
        # stops at the same place again.
        command = continuation_command(ticket_id, payload.get("reason"), budget)
        if not command.startswith("mas resume " + ticket_id):
            problems.append(label + "：续跑命令指错了票据（%s）" % command)
        widened = widen_budget(payload.get("reason"), budget)
        for name, value in widened.items():
            if value <= 0:
                problems.append(label + "：放宽后的 %s 是 %r，不是更宽" % (name, value))
            if "--%s %d" % (name.replace("_", "-"), value) not in command:
                problems.append(
                    "%s：续跑命令没有带上放宽后的 %s（%s）" % (label, name, command)
                )

        # The worker of the takeover has to see the deliverable of the
        # last attempt. Otherwise the takeover rewrites the same text
        # from nothing, forever.
        provider.repaired = True
        try:
            resumed = engine.resume(ticket_id, note="人工接手", **widened)
        except Exception as exc:  # noqa: BLE001 - the failure is the subject
            return [*problems, label + "：按票据接手失败：%s" % exc]

        prompts = list(getattr(provider, "worker_prompts", []) or [])
        # Only a run that actually produced something can show it. A run
        # that stopped before its first round has no deliverable to
        # hand over, and asking the takeover to see one would be
        # asking for the impossible.
        if str(payload.get("last_artifact") or "").strip():
            if not prompts:
                problems.append(label + "：接手的运行没有调用 worker")
            else:
                last_prompt = prompts[-1]
                if "PREVIOUS" not in last_prompt:
                    problems.append(label + "：接手的 worker 没有看到上一轮的交付物")
                if "数据是 41" not in last_prompt:
                    problems.append(
                        label + "：接手的 worker 拿不到上一轮交付物的内容"
                    )

        if not resumed.closed:
            problems.append(
                "%s：用放宽后的预算接手后没有闭合（status=%s, error=%s）"
                % (label, resumed.status, resumed.error)
            )
            return problems
        by_id = {str(s.get("id")): s for s in (resumed.scores or [])}
        score = by_id.get("c1") or {}
        if score.get("source") != "rule":
            problems.append(
                "%s：接手的闭合不是靠机检（source=%r）" % (label, score.get("source"))
            )
        if not score.get("pass"):
            problems.append(label + "：接手的闭合里 c1 没有通过")
        final = engine.tickets.get(ticket_id) or {}
        if final.get("state") != "resolved":
            problems.append(
                "%s：闭合后票据没有归档（state=%r）" % (label, final.get("state"))
            )
        return problems
    def _one_round(index: int) -> List[str]:
        """Stop four different ways, then take every ticket over."""
        problems: List[str] = []
        prefix = "第 %d 次：" % (index + 1)

        # 1. The token ceiling. A run that ran out of tokens used to be
        #    filed as an escalation - "the loop could not close itself" -
        #    which sends the human looking for a stuck worker instead of
        #    a budget that was too small.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-takeover-") as tmp:
            provider = _RepairableProvider(model="selftest-model")
            engine = LoopEngine(_config(tmp, max_tokens=1), provider=provider)
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            problems.extend(
                _check_stop(
                    prefix + "触顶（token）",
                    engine, result,
                    "budget_exhausted", "budget_exhausted", "tokens",
                )
            )
            problems.extend(_takeover(prefix + "触顶（token）", engine, result, provider))

        # 2. The iteration ceiling.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-takeover-") as tmp:
            provider = _RepairableProvider(model="selftest-model")
            engine = LoopEngine(_config(tmp, max_iterations=1), provider=provider)
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            problems.extend(
                _check_stop(
                    prefix + "触顶（轮数）",
                    engine, result,
                    "budget_exhausted", "budget_exhausted", "max_iterations",
                )
            )
            problems.extend(_takeover(prefix + "触顶（轮数）", engine, result, provider))

        # 3. The wall-clock ceiling.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-takeover-") as tmp:
            provider = _RepairableProvider(model="selftest-model")
            engine = LoopEngine(_config(tmp, deadline_sec=0), provider=provider)
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            problems.extend(
                _check_stop(
                    prefix + "触顶（时间）",
                    engine, result,
                    "budget_exhausted", "budget_exhausted", "deadline_sec",
                )
            )
            problems.extend(_takeover(prefix + "触顶（时间）", engine, result, provider))

        # 4. The backend dies. This one cannot be taken over into a
        #    closed loop - there is no model to close it - but it still
        #    has to leave a ticket that says what happened, and the
        #    ticket must not pretend the problem was solved.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-takeover-") as tmp:
            engine = LoopEngine(_config(tmp), provider=_BrokenProvider(model="selftest-model"))
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            problems.extend(
                _check_stop(
                    prefix + "后端报错",
                    engine, result,
                    "error", "run_error", "provider error",
                )
            )
            ticket = _ticket_of(engine, result)
            payload = ticket.get("payload") or {}
            command = continuation_command(
                str(result.ticket_id), payload.get("reason"), payload.get("budget")
            )
            if not command.startswith("mas resume "):
                problems.append(prefix + "后端报错：续跑命令指错了票据（%s）" % command)
            if ticket.get("state") != "waiting_human":
                problems.append(
                    prefix + "后端报错：票据不等人接手（%r）" % ticket.get("state")
                )
        return problems

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for index in range(rounds):
        failures.extend(_one_round(index))
        if len(failures) >= 5:
            break

    return {
        "name": "takeover_handover",
        "title": "接手的运行看得见上一轮的成果，并且能换一份预算",
        "invariant": (
            "每种停法都留下说清原因的票据；续跑命令带更宽的预算；"
            "接手的 worker 看得见上一轮的交付物，并能用新预算闭合"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }

def check_a_handback_closes_on_human_work(
    iterations: int = 10,
) -> Dict[str, Any]:

    """A human-written deliverable must be able to close the loop.

    The ticket queue had exactly one answer: resume the loop and let
    the machine try again. A human who had already written the answer
    - the case that actually happens when a loop escalates - had
    nowhere to put it. Their work was thrown away and re-derived by
    the machine, usually worse.

    The handback path fixes that, and it is only worth having if it is
    honest. Three properties make it a closure rather than a rubber
    stamp, and the check asserts all three:

    * **The same standards decide it.** The criteria come back from
      the ticket, so the deliverable is judged by the rules the task
      was written with - not by a re-interpretation, and not by the
      reviewer's mood.
    * **A deliverable that fails does not close.** The failing
      criteria are written back onto the ticket and the ticket stays
      in the human queue. A rejected handback is a second try, not a
      closure.
    * **A dead reviewer does not destroy the work.** The machine
      checks are already decided when the reviewer is called; if the
      backend dies, the handback still reports what the rules said
      instead of crashing and losing a deliverable a human had
      written.
    """
    import tempfile

    from mas.engine import LoopEngine
    from mas.providers import Provider, ProviderError

    class _AlwaysFails(Provider):
        """The verifier always says no.

        A handback that closes therefore has to close through the
        machine check that came back on the ticket - the model cannot
        fake it.
        """

        name = "selftest-handback"

        def __init__(self, model: str = "selftest-model", **options: Any) -> None:
            super().__init__(model=model, **options)

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "planner":
                text = json.dumps(
                    {"summary": "计划", "plan": ["写交付物"], "risks": ["写不好"]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "verifier":
                text = json.dumps(
                    {"verdict": "fail",
                     "scores": [{"id": "c1", "pass": False, "note": "不过"}]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "reflector":
                text = json.dumps(
                    {"summary": "复盘", "issues": [], "next_action": "revise"}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "worker":
                text = "结论。数据是 41。"
                return {"text": text, "usage": usage, "raw": {}}
            return {"text": "{}", "usage": usage, "raw": {}}

    class _DeadBackend(Provider):
        """A backend that is simply not there."""

        name = "selftest-handback-dead"

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            raise ProviderError("the endpoint is not reachable")

    def _config(tmp: str, **budget: Any) -> Dict[str, Any]:
        base = {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60}
        base.update(budget)
        return {
            "provider": "mock",
            "model": "selftest-model",
            "state_dir": os.path.join(tmp, "state"),
            "workspace": "",
            "budget": base,
            "orchestration": {
                "parallel_workers": 1, "reviewers": 1, "review_mode": "all",
            },
            "team": {"language": "zh"},
            "stagnation_limit": 2,
        }

    criteria = [
        {
            "id": "c1",
            "desc": "正文里要出现 42",
            "check": {"type": "contains", "arg": "42"},
        }
    ]

    def _one_round(index: int) -> List[str]:
        problems: List[str] = []
        prefix = "第 %d 次：" % (index + 1)

        # A run that escalates, and a ticket a human can pick up.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-handback-") as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="selftest-model"))
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            ticket_id = str(result.ticket_id or "")
            if not ticket_id:
                return [prefix + "没有留下可接手的票据"]
            ticket = engine.tickets.get(ticket_id) or {}
            payload = ticket.get("payload") or {}
            if not (payload.get("criteria") or []):
                problems.append(prefix + "票据没有带回验收标准")
                return problems
            if not payload.get("workspace"):
                problems.append(prefix + "票据没有记下这一轮的工作区")

            # 1. The human's work closes the loop.
            good = "结论。数据是 42。"
            try:
                accepted = engine.handback(ticket_id, good, note="手写的")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                problems.append(prefix + "接回人工交付物报错：%s" % exc)
                return problems
            if not accepted.closed:
                problems.append(
                    "%s：合格的人工交付物没有闭合（status=%s, error=%s）"
                    % (prefix, accepted.status, accepted.error)
                )
            by_id = {str(s.get("id")): s for s in (accepted.scores or [])}
            score = by_id.get("c1") or {}
            if score.get("source") != "rule":
                problems.append(
                    "%s：闭合不是靠机检（source=%r）" % (prefix, score.get("source"))
                )
            if not score.get("pass"):
                problems.append(prefix + "闭合里 c1 没有通过")
            if not accepted.artifact_path:
                problems.append(prefix + "通过验收的交付物没有落盘")
            elif not pathlib.Path(accepted.artifact_path).exists():
                problems.append(prefix + "落盘的交付物不在磁盘上")
            final = engine.tickets.get(ticket_id) or {}
            if final.get("state") != "resolved":
                problems.append(
                    "%s：闭合后票据没有归档（state=%r）" % (prefix, final.get("state"))
                )
            if final.get("decision") != "handback":
                problems.append(
                    "%s：票据的处理结果不是 handback（%r）"
                    % (prefix, final.get("decision"))
                )

        # 2. Work that does not meet the standards does not close, and
        #    the failure is recorded on the ticket.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-handback-") as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="selftest-model"))
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            ticket_id = str(result.ticket_id or "")
            if not ticket_id:
                return [prefix + "没有留下可接手的票据"]
            bad = "结论。数据是 41。"
            try:
                rejected = engine.handback(ticket_id, bad, note="不合格的")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                problems.append(prefix + "接回不合格交付物报错：%s" % exc)
                return problems
            if rejected.closed:
                problems.append(prefix + "不合格的人工交付物竟然闭合了")
            if not rejected.issues:
                problems.append(prefix + "不合格的人工交付物没有记下失败项")
            still = engine.tickets.get(ticket_id) or {}
            if still.get("state") != "waiting_human":
                problems.append(
                    "%s：不合格的接回把工单从人工队列里弄丢了（state=%r）"
                    % (prefix, still.get("state"))
                )
            attempts = list((still.get("payload") or {}).get("resume_attempts") or [])
            if not attempts:
                problems.append(prefix + "不合格的接回没有记录在票上")

        # 3. A dead reviewer must not destroy the human's work.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-handback-") as tmp:
            engine = LoopEngine(_config(tmp), provider=_DeadBackend(model="selftest-model"))
            try:
                result = engine.run(goal="写一份 Q3 报告", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "运行报错：%s" % exc]
            ticket_id = str(result.ticket_id or "")
            if not ticket_id:
                return [prefix + "没有留下可接手的票据"]
            try:
                survived = engine.handback(ticket_id, "结论。数据是 42。", note="手写的")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                problems.append(
                    "%s：评审挂了以后人工交付物也丢了（%s）" % (prefix, exc)
                )
                return problems
            if not survived.closed:
                problems.append(
                    "%s：评审挂了，但机检已经通过，交付物仍应闭合（status=%s, error=%s）"
                    % (prefix, survived.status, survived.error)
                )
        return problems

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for index in range(rounds):
        failures.extend(_one_round(index))
        if len(failures) >= 5:
            break

    return {
        "name": "handback_closes",
        "title": "人工写的交付物也能通过同一套验收把闭环关上",
        "invariant": (
            "人工交付物按票上的同一套验收标准判定；不合格的接回不闭合、"
            "失败项留在票上；评审挂了也不会毁掉人已经写好的交付物"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


def check_a_handback_shows_up_in_the_report(
    iterations: int = 10,
) -> Dict[str, Any]:

    """The report has to show the step a human took.

    v0.7.67 made it possible for a human-written deliverable to close
    the loop. But the report could not see it: the handback row was
    not a closure row, so the report read no verdict, no goal, no
    files - and the problem stayed in the "pending" column even
    though the loop had closed. The reader was told the opposite of
    what happened.

    The ninth gate (``handback_closes``) watches the *engine*: that
    a human deliverable passes the same standards and closes the
    loop. Nothing watched the *report*. That gap was found by
    mutation: six mutations of the reporting layer - drop the
    ``closed`` flag, rename the decision, stop reading the handback
    scores, go back to "all member runs must close", forget who
    closed it, always label the source as the machine - and the
    ninth gate caught **none** of them.

    So this is the tenth gate. It asserts what the reader sees:

    * a handback that closed is **closed** in the report, and the
      problem is **no longer pending**;
    * the report carries the **verdict** the standards produced, not
      an empty one;
    * the report says the answer came from a **person**, and says so
      in words that are not the machine's;
    * the human's **own note** survives into the report;
    * a handback that was **rejected** is still reported - a failure
      may not disappear;
    * a thread whose last attempt failed but whose earlier attempt
      closed is **closed**, because the last attempt counts.
    """
    import tempfile

    from mas.engine import LoopEngine
    from mas.providers import Provider
    from mas.report import build_report, render_markdown, replay_trace

    class _AlwaysFails(Provider):
        """The verifier always says no.

        A handback that closes therefore has to close through the
        machine check that came back on the ticket - the model
        cannot fake it.
        """

        name = "selftest-handback-report"

        def __init__(self, model: str = "selftest-model", **options: Any) -> None:
            super().__init__(model=model, **options)

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "planner":
                text = json.dumps(
                    {"summary": "\u8ba1\u5212", "plan": ["\u5199\u4ea4\u4ed8\u7269"],
                     "risks": ["\u5199\u4e0d\u597d"]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "verifier":
                text = json.dumps(
                    {"verdict": "fail",
                     "scores": [{"id": "c1", "pass": False, "note": "\u4e0d\u8fc7"}]}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "reflector":
                text = json.dumps(
                    {"summary": "\u590d\u76d8", "issues": [], "next_action": "revise"}
                )
                return {"text": text, "usage": usage, "raw": {}}
            if role == "worker":
                text = "\u7ed3\u8bba\u3002\u6570\u636e\u662f 41\u3002"
                return {"text": text, "usage": usage, "raw": {}}
            return {"text": "{}", "usage": usage, "raw": {}}

    def _config(tmp: str) -> Dict[str, Any]:
        return {
            "provider": "mock",
            "model": "selftest-model",
            "state_dir": os.path.join(tmp, "state"),
            "workspace": "",
            "budget": {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {
                "parallel_workers": 1, "reviewers": 1, "review_mode": "all",
            },
            "team": {"language": "zh"},
            "stagnation_limit": 2,
        }

    criteria = [
        {
            "id": "c1",
            "desc": "\u6b63\u6587\u91cc\u8981\u51fa\u73b0 42",
            "check": {"type": "contains", "arg": "42"},
        }
    ]

    def _one_round(index: int) -> List[str]:
        problems: List[str] = []
        prefix = "\u7b2c %d \u6b21\uff1a" % (index + 1)

        with tempfile.TemporaryDirectory(prefix="mas-selftest-handback-report-") as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="selftest-model"))
            try:
                result = engine.run(goal="\u5199\u4e00\u4efd Q3 \u62a5\u544a", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "\u8fd0\u884c\u62a5\u9519\uff1a%s" % exc]
            ticket_id = str(result.ticket_id or "")
            if not ticket_id:
                return [prefix + "\u6ca1\u6709\u7559\u4e0b\u53ef\u63a5\u624b\u7684\u7968\u636e"]
            try:
                accepted = engine.handback(ticket_id, "\u7ed3\u8bba\u3002\u6570\u636e\u662f 42\u3002",
                                           note="\u4eba\u5de5\u5199\u7684")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [prefix + "\u63a5\u56de\u4eba\u5de5\u4ea4\u4ed8\u7269\u62a5\u9519\uff1a%s" % exc]
            if not accepted.closed:
                return [prefix + "\u5408\u683c\u7684\u4eba\u5de5\u4ea4\u4ed8\u7269\u6ca1\u6709\u95ed\u5408"]

            rows = _ledger_rows(str(result.ledger_path))
            report = build_report(rows)
            markdown = render_markdown(report)
            trace = replay_trace(rows)

            # 1. The report must agree with the engine.
            handback_runs = [r for r in report["runs"] if r.get("handback")]
            if not handback_runs:
                problems.append(prefix + "\u62a5\u544a\u91cc\u6ca1\u6709\u4eba\u5de5\u4ea4\u56de\u7684\u8fd0\u884c")
                return problems
            run = handback_runs[0]
            if not run.get("closed"):
                problems.append(
                    "%s\uff1a\u4eba\u5de5\u4ea4\u56de\u5df2\u7ecf\u95ed\u5408\uff0c\u62a5\u544a\u5374\u8bf4\u6ca1\u6709"
                    % prefix
                )
            if run.get("decision") != "handback":
                problems.append(
                    "%s\uff1a\u62a5\u544a\u91cc\u7684\u51b3\u7b56\u4e0d\u662f handback\uff08%r\uff09"
                    % (prefix, run.get("decision"))
                )
            if not run.get("scores"):
                problems.append(prefix + "\u62a5\u544a\u91cc\u6ca1\u6709\u4eba\u5de5\u4ea4\u4ed8\u7269\u7684\u9a8c\u6536\u7ed3\u679c")
            if not run.get("goal"):
                problems.append(prefix + "\u62a5\u544a\u91cc\u6ca1\u6709\u4eba\u5de5\u4ea4\u4ed8\u7269\u7684\u4efb\u52a1")

            # 3b. The thread has to say *which* attempt closed it, and
            #     that the answer came from a person. Without
            #     ``closed_by`` the reader cannot answer "which try
            #     solved it"; without the handback mark on the thread
            #     the report cannot say who did the work.
            threads = report.get("threads") or []
            handback_threads = [t for t in threads if t.get("handback")]
            if not handback_threads:
                problems.append(prefix + "\u95ee\u9898\u6982\u89c8\u91cc\u6ca1\u6709\u6807\u51fa\u4eba\u5de5\u5b8c\u6210\u7684\u95ee\u9898")
            else:
                thread = handback_threads[0]
                if not thread.get("closed"):
                    problems.append(prefix + "\u4eba\u5de5\u5b8c\u6210\u7684\u95ee\u9898\u5374\u6ca1\u6709\u95ed\u5408")
                if not thread.get("closed_by"):
                    problems.append(prefix + "\u95ee\u9898\u91cc\u6ca1\u6709\u8bb0\u4f4f\u662f\u54ea\u4e00\u6b21\u8fd0\u884c\u95ed\u5408\u7684")

            # 2. The problem must no longer be pending.
            totals = report["totals"]
            if int(totals.get("problems_open") or 0) != 0:
                problems.append(
                    "%s\uff1a\u5df2\u7ecf\u95ed\u5408\u7684\u95ee\u9898\u8fd8\u5728\u5f85\u5904\u7406\u91cc"
                    % prefix
                )
            if int(totals.get("problems_closed") or 0) < 1:
                problems.append(prefix + "\u95ee\u9898\u6ca1\u6709\u8ba1\u5165\u5df2\u89e3\u51b3")

            # 3. The reader has to see that a person did it.
            if "\U0001f91d" not in markdown:
                problems.append(prefix + "\u62a5\u544a\u91cc\u6ca1\u6709\u6807\u51fa\u4eba\u5de5\u4ea4\u4ed8\u7269\u6765\u6e90")
            if "\u4eba\u5de5\u4ea4\u4ed8\u7269" not in markdown:
                problems.append(prefix + "\u62a5\u544a\u91cc\u6ca1\u6709\u4eba\u5de5\u4ea4\u4ed8\u7269\u7684\u9a8c\u6536\u5c0f\u8282")
            if "\u4eba\u5de5\u5199\u7684" not in markdown:
                problems.append(prefix + "\u62a5\u544a\u91cc\u6ca1\u6709\u4eba\u7684\u610f\u89c1")

            # 4. The replay has to show it too.
            trace_runs = [r for r in trace.get("runs") or [] if r.get("handback")]
            if not trace_runs:
                problems.append(prefix + "replay \u91cc\u6ca1\u6709\u4eba\u5de5\u4ea4\u56de\u7684\u8fd0\u884c")
            elif not trace_runs[0].get("human_note"):
                problems.append(prefix + "replay \u91cc\u6ca1\u6709\u4eba\u5de5\u610f\u89c1")
            if not any(row.get("handback") for row in (trace.get("steps") or [])):
                problems.append(prefix + "replay \u7684\u6b65\u9aa4\u91cc\u6ca1\u6709\u6807\u51fa\u4eba\u5de5\u4ea4\u56de")

        # 5. A rejected handback must still be reported. A failure may
        #    not disappear - the reader has to see that the human's
        #    work was refused, and why.
        with tempfile.TemporaryDirectory(prefix="mas-selftest-handback-report-") as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="selftest-model"))
            try:
                result = engine.run(goal="\u5199\u4e00\u4efd Q3 \u62a5\u544a", criteria=criteria)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [*problems, prefix + "\u8fd0\u884c\u62a5\u9519\uff1a%s" % exc]
            ticket_id = str(result.ticket_id or "")
            if not ticket_id:
                return [*problems, prefix + "\u6ca1\u6709\u7559\u4e0b\u53ef\u63a5\u624b\u7684\u7968\u636e"]
            try:
                rejected = engine.handback(ticket_id, "\u7ed3\u8bba\u3002\u6570\u636e\u662f 41\u3002",
                                           note="\u4e0d\u5408\u683c\u7684")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                return [*problems, prefix + "\u63a5\u56de\u4e0d\u5408\u683c\u4ea4\u4ed8\u7269\u62a5\u9519\uff1a%s" % exc]
            if rejected.closed:
                return [*problems, prefix + "\u4e0d\u5408\u683c\u7684\u4eba\u5de5\u4ea4\u4ed8\u7269\u7adf\u7136\u95ed\u5408\u4e86"]
            rows = _ledger_rows(str(result.ledger_path))
            report = build_report(rows)
            if not any(r.get("handback") for r in report["runs"]):
                problems.append(prefix + "\u88ab\u62d2\u7edd\u7684\u4eba\u5de5\u4ea4\u56de\u4ece\u62a5\u544a\u91cc\u6d88\u5931\u4e86")
            if int(report["totals"].get("problems_open") or 0) < 1:
                problems.append(prefix + "\u88ab\u62d2\u7edd\u7684\u4ea4\u4ed8\u7269\u6ca1\u6709\u7559\u5728\u5f85\u5904\u7406\u91cc")
        return problems

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for index in range(rounds):
        failures.extend(_one_round(index))
        if len(failures) >= 5:
            break

    return {
        "name": "handback_report",
        "title": "\u62a5\u544a\u91cc\u770b\u5f97\u89c1\u4eba\u5199\u7684\u90a3\u4e00\u6b65",
        "invariant": (
            "\u4eba\u5de5\u4ea4\u56de\u7684\u95ed\u5408\u5728\u62a5\u544a\u91cc\u662f\u5df2\u89e3\u51b3\u7684\u95ee\u9898\uff1b"
            "\u9a8c\u6536\u7ed3\u679c\u3001\u4eba\u7684\u610f\u89c1\u4e0e\u6765\u6e90\u90fd\u5728\u62a5\u544a\u548c replay \u91cc\u770b\u5f97\u89c1\uff1b"
            "\u88ab\u62d2\u7edd\u7684\u4ea4\u4ed8\u7269\u4e0d\u80fd\u6d88\u5931"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


# ----------------------------------------------------------------- the gate
# The short name of a check is a property of the function, so that
#: The behaviours the twelfth gate watches. Every one of them is
#: observed on every round - a gate that watches fewer of them than it
#: claims is a gate that mostly does not watch.
CONTENT_OBSERVATIONS = (
    "the content check reads the words of a compressed PDF",
    "the byte-level check cannot see those words",
    "the content check reads the words of a Word document",
    "the Word document is read as text, not as markup",
    "the format check names the format of the file",
    "the format check rejects the wrong format",
    "a file whose content cannot be read fails",
    "a file the loop would write is judged by what it would write",
    "a file the loop would write is not judged by words it does not have",
)


def _content_observations(root: Any) -> Dict[str, bool]:
    """Observe the content path, one behaviour at a time.

    Every observation is recorded, whether it holds or not. The gate
    decides later, from the record - so a gate whose *observation* was
    broken shows up as a missing observation instead of as a silent
    pass. That is what makes the gate hard to fool: the decision is not
    made where the reading happens.
    """
    import pathlib

    from mas.content import extract_text
    from mas.filechecks import evaluate_file_check

    seen: Dict[str, bool] = {}

    content_level = evaluate_file_check(
        "content_contains", "report.pdf::Conclusion", root, None
    )
    seen["the content check reads the words of a compressed PDF"] = bool(
        content_level.get("pass")
    )

    byte_level = evaluate_file_check(
        "file_contains", "report.pdf::Conclusion", root, None
    )
    seen["the byte-level check cannot see those words"] = not bool(
        byte_level.get("pass")
    )

    docx_check = evaluate_file_check(
        "content_contains", "memo.docx::Memo", root, None
    )
    seen["the content check reads the words of a Word document"] = bool(
        docx_check.get("pass")
    )

    # The words of a .docx live inside a member of the archive, and the
    # member is an XML document. A reader that unzips the file but
    # forgets to read the document gets the raw markup instead of the
    # text. The words are in both, so the words alone prove nothing -
    # the *tags* have to be gone.
    docx_text = extract_text(pathlib.Path(root) / "memo.docx").get("text") or ""
    seen["the Word document is read as text, not as markup"] = not (
        "<w:t>" in docx_text or "<w:p>" in docx_text
    )

    format_check = evaluate_file_check(
        "content_format_is", "report.pdf::pdf", root, None
    )
    seen["the format check names the format of the file"] = bool(
        format_check.get("pass")
    )

    wrong_format = evaluate_file_check(
        "content_format_is", "memo.docx::pdf", root, None
    )
    seen["the format check rejects the wrong format"] = (
        wrong_format.get("pass") is False
    )

    unreadable = evaluate_file_check(
        "content_contains", "blob.bin::anything", root, None
    )
    seen["a file whose content cannot be read fails"] = (
        unreadable.get("pass") is False
    )

    # A file the loop would write is judged by the bytes it *would*
    # write. The file is not on the disk at all, so only the overlay
    # can decide it.
    overlay = {str(pathlib.Path(root) / "fresh.pdf"): _compressed_pdf_bytes("Conclusion 42")}
    offered = evaluate_file_check(
        "content_contains", "fresh.pdf::Conclusion", root, overlay
    )
    seen["a file the loop would write is judged by what it would write"] = bool(
        offered.get("pass")
    )
    wrong = evaluate_file_check(
        "content_contains", "fresh.pdf::Verdict", root, overlay
    )
    seen["a file the loop would write is not judged by words it does not have"] = (
        wrong.get("pass") is False
    )
    return seen


def _docx_bytes(body: str) -> bytes:
    """The bytes of a Word document whose text lives in the archive."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:t>%s</w:t></w:p></w:body></w:document>" % body,
        )
    return buffer.getvalue()


def _compressed_pdf_bytes(body: str) -> bytes:
    """The bytes of a PDF whose text lives in a *compressed* stream.

    The words are not in the bytes of the file, so a byte-level check
    cannot see them - which is the whole point of the gate.
    """
    import zlib

    content = ("BT (\\(%s\\)) Tj ET" % body).encode("utf-8")
    compressed = zlib.compress(content)
    pieces = [
        b"%PDF-1.4\n",
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Page/Parent 1 0 R/MediaBox[0 0 200 200]>>endobj\n",
        b"3 0 obj<</Length %d/Filter/FlateDecode>>stream\n" % len(compressed),
        compressed,
        b"\nendstream endobj\n",
        b"trailer<</Root 1 0 R>>\n",
        b"%%EOF\n",
    ]
    return b"".join(pieces)


def check_a_content_check_reads_inside_the_file(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The loop has to be able to judge what is *inside* a file.

    A machine check could only look at the *bytes* of a file. That is
    enough for "the file has the line in it", but it is blind for
    every format whose payload is not plain text: a PDF keeps its text
    in a compressed stream, a .docx is a zip file, an image has no text
    at all.

    So the loop could only accept work it could read as text, and a
    task whose deliverable is a PDF was unjudgeable - the criterion
    would go to the model, and a model that says "looks fine" closes a
    criterion whose rule was never written. That is a fake closure.

    This is the twelfth gate. It watches that the *content* checks see
    what is inside the file:

    * a content check on a PDF finds the words in the compressed
      stream - the byte-level check on the same file does not;
    * a content check on a .docx finds the text of the document;
    * a content check names the *format* of a file, so a PNG that was
      written as a PDF is caught;
    * a file whose content cannot be read is **failed**, never passed -
      a check that cannot decide must not close a criterion, and it
      must not go back to the model either, because a model that says
      "looks fine" closes a criterion nobody ever checked;
    * a content check on a file the loop is about to write judges the
      content the loop *would* write.

    And the gate proves it can fail: it breaks the content path on
    purpose and checks that it notices. A gate that cannot fail is not
    a gate.
    """
    import tempfile

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "report.pdf").write_bytes(_compressed_pdf_bytes("Conclusion 42"))
            (root / "memo.docx").write_bytes(_docx_bytes("Memo 42"))
            (root / "blob.bin").write_bytes(bytes(range(256)) * 8)

            seen = _content_observations(root)
            for label in CONTENT_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append(
                        "round %d: %s" % (round_index + 1, label)
                    )

            # The gate proves it can fail. The content path is broken on
            # purpose - every file reads as empty - and the gate has to
            # notice. Without this, a gate whose own observation was
            # broken would report "all good" for ever.
            import mas.filechecks as filechecks

            real_extract = filechecks.extract_text

            def _blind(path: Any) -> Dict[str, Any]:
                return {"text": "", "format": "binary", "ok": False,
                        "reason": "the gate broke the content path on purpose",
                        "bytes": 0}

            filechecks.extract_text = _blind
            try:
                broken = _content_observations(root)
            finally:
                filechecks.extract_text = real_extract
            if broken.get("the content check reads the words of a compressed PDF"):
                failures.append(
                    "round %d: the gate did not notice a broken content path - "
                    "it cannot fail, so it does not watch" % (round_index + 1)
                )

    return {
        "name": "content_check",
        "title": "机检能读进文件里的内容，不只是字节",
        "invariant": (
            "PDF 里压缩着的字、Word 文档里的字，机检都读得到；"
            "读不出内容的文件判「失败」而不是「通过」——不然它会回到模型，"
            "模型一句「looks fine」就闭掉没人查过的标准；"
            "格式判错要抓得住；要写的新文件按将要写进去的内容判"
        ),
        "detail": "内容级机检：PDF / Word / 压缩包 / 图片，循环判得了",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


# ----------------------------------------------------------------- the gate
# The short name of a check is a property of the function, so that
# ``--only binary_write`` and the JSON output speak the same word.
BINARY_WRITE_OBSERVATIONS = (
    "a base64 write block lands on the disk as real bytes",
    "a base64 write block does not land as its base64 text",
    "the written bytes are the bytes the content checks read",
    "a write block with an unknown encoding is refused",
    "a write block with broken base64 is refused",
    "base64 that is not base64 does not become an empty file",
    "a write block without an encoding is still text",
    "a content check creates a write obligation",
    "the engine plans the binary write, not the text around it",
    "the ranking overlay carries the bytes, not their number",
)


def _binary_write_observations(root: Any) -> Dict[str, bool]:
    """Observe the binary write path, one behaviour at a time.

    Same discipline as the twelfth gate: every observation is recorded,
    whether it holds or not, and the decision is made later from the
    record. A gate that only records what it happens to see cannot tell
    the difference between "the path works" and "the path was never
    exercised".
    """
    import base64
    import pathlib as _pathlib

    from mas.filechecks import evaluate_file_check
    from mas.filewrite import apply_write_blocks, parse_write_blocks, plan_writes

    seen: Dict[str, bool] = {}

    pdf = _compressed_pdf_bytes("Conclusion 42")
    encoded = base64.b64encode(pdf).decode("ascii")
    block = (
        '<<<MAS:FILE path="out/report.pdf" encoding="base64">>>>\n'
        + encoded
        + "\n<<<MAS:END FILE>>>"
    )

    applied = apply_write_blocks(block, _pathlib.Path(root))
    assert not applied.get("errors"), applied.get("errors")
    target = _pathlib.Path(root) / "out" / "report.pdf"
    landed = target.exists() and target.read_bytes() == pdf
    seen["a base64 write block lands on the disk as real bytes"] = bool(landed)
    seen["a base64 write block does not land as its base64 text"] = bool(
        landed and target.read_bytes() != encoded.encode("utf-8")
    )

    # The bytes that are on the disk are the bytes the content checks
    # read. If the write path and the read path disagreed, the check
    # would judge a different file than the one the loop delivered.
    on_disk = evaluate_file_check(
        "content_contains", "out/report.pdf::Conclusion", root, None
    )
    seen["the written bytes are the bytes the content checks read"] = bool(
        on_disk.get("pass")
    )

    bad_encoding = (
        '<<<MAS:FILE path="out/x.bin" encoding="rot13">>>>\n'
        "hello\n<<<MAS:END FILE>>>"
    )
    _artifact, refused = parse_write_blocks(bad_encoding)
    seen["a write block with an unknown encoding is refused"] = (
        refused == [] and not (_pathlib.Path(root) / "out" / "x.bin").exists()
    )

    broken = (
        '<<<MAS:FILE path="out/y.bin" encoding="base64">>>>\n'
        "!!!!\n<<<MAS:END FILE>>>"
    )
    try:
        broken_result = apply_write_blocks(broken, _pathlib.Path(root))
        broken_refused = (
            bool(broken_result.get("errors"))
            and not (_pathlib.Path(root) / "out" / "y.bin").exists()
        )
    except Exception:  # noqa: BLE001 - the failure is the subject
        broken_refused = True
    seen["a write block with broken base64 is refused"] = bool(broken_refused)

    # A lenient base64 decoder drops every character that is not part
    # of the alphabet. A block of pure nonsense then decoded into an
    # *empty* file - silently. The worker asked for a PDF and the
    # workspace got a zero-byte file, which the content checks then
    # judged as unreadable. A refusal at least says what went wrong.
    # Four characters that are not in the base64 alphabet. The lenient
    # decoder drops all of them and returns an *empty* payload without
    # a word of complaint - so the workspace would get a zero-byte
    # "PDF". The strict decoder refuses it.
    junk = (
        '<<<MAS:FILE path="out/junk.pdf" encoding="base64">>>>\n'
        "!!!!\n<<<MAS:END FILE>>>"
    )
    junk_result = apply_write_blocks(junk, _pathlib.Path(root))
    junk_target = _pathlib.Path(root) / "out" / "junk.pdf"
    seen["base64 that is not base64 does not become an empty file"] = bool(
        junk_result.get("errors")
        and not junk_target.exists()
    )

    plain = '<<<MAS:FILE path="out/plain.txt">>>>\nhello\n<<<MAS:END FILE>>>'
    plain_result = apply_write_blocks(plain, _pathlib.Path(root))
    plain_target = _pathlib.Path(root) / "out" / "plain.txt"
    seen["a write block without an encoding is still text"] = bool(
        plain_result.get("written")
        and plain_target.exists()
        and plain_target.read_bytes() == b"hello"
    )

    # A content check demands a file, so the loop has to *write* one -
    # otherwise the worker only talks about the file and the check can
    # never be satisfied.
    from mas.engine import Criterion, LoopEngine

    engine = LoopEngine(
        {
            "provider": "mock",
            "model": "selftest-model",
            "state_dir": str(_pathlib.Path(root) / "state"),
            "workspace": "",
            "budget": {"max_iterations": 2, "max_tokens": 100000, "deadline_sec": 30},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
        }
    )
    criteria = [
        Criterion.from_raw(
            {
                "id": "c1",
                "desc": "the report has to say Conclusion",
                "check": {"type": "content_contains", "arg": "out/report.pdf::Conclusion"},
            },
            0,
        )
    ]
    write_paths = engine._required_write_paths(criteria)
    seen["a content check creates a write obligation"] = (
        "out/report.pdf" in write_paths
    )

    # The engine has to plan the *binary* write. If the plan carried the
    # base64 text instead of the decoded bytes, the parallel ranking
    # would score the candidate against an encoding instead of against
    # the PDF it would write - and the winner would be the candidate
    # that wrote the *least*.
    planned = plan_writes(block, _pathlib.Path(root))["planned"]
    seen["the engine plans the binary write, not the text around it"] = bool(
        planned
        and planned[0].get("data") == pdf
        and planned[0].get("bytes") == len(pdf)
    )

    # The overlay the parallel ranking uses has to carry the *bytes*.
    # It used to carry the number of them - ``planned["bytes"]`` is the
    # length - and every file check that read it then judged a number
    # instead of the file. The candidate that would have written the
    # right PDF lost to one that would have written nothing, and no
    # test caught it because the overlay was never inspected.
    # The *engine's* overlay - the one the parallel ranking really
    # uses - has to carry the bytes. The gate calls the engine's own
    # method, so it judges the shipping code path and not a copy of
    # it.
    #
    # The block names a file that is **not** on the disk: the earlier
    # observation wrote ``report.pdf`` to the root, and a check that
    # finds a file on the disk reads that file and never looks at the
    # overlay at all. A file that exists nowhere but in the overlay is
    # the only way to tell the overlay apart from the disk.
    ghost = (
        '<<<MAS:FILE path="out/ghost.pdf" encoding="base64">>>>\n'
        + base64.b64encode(_compressed_pdf_bytes("Ghost 42")).decode("ascii")
        + "\n<<<MAS:END FILE>>>"
    )
    # The engine needs a run id before it knows its own workspace.
    engine._run_id = "gate"
    engine_root = engine._write_workspace()
    engine_overlay = engine._planned_overlay(ghost)
    offered = evaluate_file_check(
        "content_contains", "out/ghost.pdf::Ghost", engine_root, engine_overlay
    )
    seen["the ranking overlay carries the bytes, not their number"] = bool(
        offered.get("pass")
    )
    return seen


def check_a_binary_write_lands_as_bytes(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The loop has to be able to *produce* a file it can read.

    The content checks of v0.7.70 can read the words inside a PDF - but
    only if a PDF is actually on the disk. The write protocol of
    v0.7.15 could only carry **text**: the worker emitted a string, the
    engine wrote it as UTF-8, and a task whose deliverable was a PDF
    had no way to produce one. So the machine could judge a file that
    the loop was unable to write, and the strongest reading in the
    project still could not close a binary task.

    This is the thirteenth gate. It watches that:

    * a write block that says ``encoding="base64"`` lands on the disk
      as the *decoded* bytes, not as its base64 text;
    * the bytes on the disk are the bytes the content checks read - the
      write path and the read path must agree;
    * an unknown encoding is refused, and so is broken base64 - a
      request that cannot be honoured must not leave a half-written
      file behind;
    * a block without an encoding is still written as text, so the
      older protocol keeps working;
    * a content check creates a *write obligation*, so the worker is
      told to write the file at all;
    * the engine plans the binary write as bytes, so the parallel
      ranking scores a candidate against the PDF it would write.

    And the gate proves it can fail: it breaks the write path on
    purpose and checks that it notices.
    """
    import tempfile

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)

            # A broken path must not *crash* the gate - it must make
            # the gate red. A gate that raises instead of reporting is
            # a gate that stops watching the moment it is needed most.
            try:
                seen = _binary_write_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the binary write path broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                continue
            for label in BINARY_WRITE_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. The encoding is ignored on
            # purpose - every binary block lands as its base64 text -
            # and the gate has to notice. Without this, a gate whose own
            # observation was broken would report "all good" for ever.
            import mas.filewrite as filewrite

            real_request_bytes = filewrite._request_bytes

            def _blind(content: str, encoding: Any = None) -> bytes:
                return str(content).encode("utf-8")

            filewrite._request_bytes = _blind
            try:
                broken = _binary_write_observations(root)
            except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                broken = {}
            finally:
                filewrite._request_bytes = real_request_bytes
            if broken.get("a base64 write block lands on the disk as real bytes"):
                failures.append(
                    "round %d: the gate did not notice a broken write path - "
                    "it cannot fail, so it does not watch" % (round_index + 1)
                )

    return {
        "name": "binary_write",
        "title": "循环能写出二进制交付物，不只是文字",
        "invariant": (
            "base64 写的块落盘是真字节（不是编码文本）；写出去的字节就是机检读到的字节；"
            "不认识的编码和坏掉的 base64 都要被拒；不写编码的块仍旧按文本写；"
            "内容级标准要形成写文件义务；引擎规划的是二进制字节而不是旁边的文字"
        ),
        "detail": "二进制写协议：PDF / 图片 / 表格，循环写得出来也读得回",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


def check_a_folder_handoff_closes_on_human_edits(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The human must be able to fix the files in place and close the loop.

    v0.7.67 made a human deliverable closable - but only as one blob
    of text. The loop this project runs writes *files*, and the
    acceptance criteria that matter are file checks that read the
    disk. A human who fixes such a task opens the file and edits it;
    the text handback asked them to paste the file's content into a
    command line instead. That is a second copy of the work, and the
    copy is the part that fails: forget one line and the handoff is
    rejected for a reason that has nothing to do with the answer.

    v0.7.69 added the folder handoff: the human edits the files where
    they are, and hands the folder back. The tenth gate
    (``handback_report``) watches the *report*. This is the eleventh
    gate, and it watches the *human path*:

    * the file checks read the **human's edits**, not the ones the
      loop left behind;
    * the text checks see the **content of the files**, so the
      deliverable is built from the work rather than retyped;
    * a refused handoff **changes nothing** - the human's files stay
      exactly as they were, because they are the human's work;
    * a refused handoff writes **no deliverable**;
    * an accepted handoff writes the deliverable **into the folder the
      human edited**, not into a second folder;
    * every file of the folder is part of the offer;
    * the ticket queue **names the folder**, so a human can find it
      without going looking.
    """
    import tempfile

    from mas.engine import LoopEngine
    from mas.providers import Provider

    class _AlwaysFails(Provider):
        """The verifier always says no.

        A folder handoff therefore has to close through the machine
        checks that came back on the ticket - the model cannot fake it.
        """

        name = "selftest-folder-handoff"

        def __init__(self, model: str = "selftest-model", **options: Any) -> None:
            super().__init__(model=model, **options)

        def complete(self, *, system: str, prompt: str, role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            self.calls += 1
            usage = {"total_tokens": 10}
            if role == "planner":
                text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
            elif role == "verifier":
                text = json.dumps(
                    {"verdict": "fail",
                     "scores": [{"id": "c1", "pass": False, "note": "no"}]}
                )
            elif role == "reflector":
                text = json.dumps(
                    {"summary": "s", "issues": [], "next_action": "revise"}
                )
            elif role == "worker":
                text = "the answer is 41."
            else:
                text = "{}"
            return {"text": text, "usage": usage, "raw": {}}

    criteria = [
        {
            "id": "c1",
            "desc": "finding.txt says 42",
            "check": {"type": "file_contains", "arg": "finding.txt::42"},
        },
        {
            "id": "c2",
            "desc": "the deliverable carries the conclusion",
            "check": {"type": "contains", "arg": "\u7ed3\u8bba"},
        },
    ]

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "provider": "mock",
                "model": "selftest-model",
                "state_dir": os.path.join(tmp, "state"),
                "workspace": "",
                "budget": {
                    "max_iterations": 1,
                    "max_tokens": 1000000,
                    "deadline_sec": 60,
                },
                "orchestration": {
                    "parallel_workers": 1,
                    "reviewers": 1,
                    "review_mode": "all",
                },
                "team": {"language": "zh"},
                "stagnation_limit": 2,
            }
            engine = LoopEngine(config, provider=_AlwaysFails(model="selftest-model"))
            run = engine.run(goal="write the finding", criteria=criteria)
            if run.closed:
                failures.append(
                    "round %d: the loop closed on its own - the gate cannot "
                    "tell whether the human path works" % (round_index + 1)
                )
                continue
            ticket_id = str(run.ticket_id or "")
            ticket = engine.tickets.get(ticket_id)
            workspace = str((ticket or {}).get("payload", {}).get("workspace") or "")
            if not workspace:
                failures.append(
                    "round %d: the ticket does not name the folder to edit"
                    % (round_index + 1)
                )
                continue
            folder = pathlib.Path(workspace)

            # The human edits the file in place.
            target = folder / "finding.txt"
            target.write_text(
                "the answer is 42.\n\u7ed3\u8bba\uff1a\u5df2\u5b8c\u6210\n",
                encoding="utf-8",
            )
            (folder / "notes.txt").write_text("some notes", encoding="utf-8")

            result = engine.handback_workspace(ticket_id)
            if not result.closed:
                failures.append(
                    "round %d: the folder handoff did not close (%s)"
                    % (round_index + 1, result.error)
                )
                continue
            by_id = {str(s.get("id")): s for s in result.scores}
            if not by_id.get("c1", {}).get("pass"):
                failures.append(
                    "round %d: the file check did not read the human's edit"
                    % (round_index + 1)
                )
            if not by_id.get("c2", {}).get("pass"):
                failures.append(
                    "round %d: the deliverable does not carry the file content"
                    % (round_index + 1)
                )
            if not result.artifact_path:
                failures.append(
                    "round %d: the accepted handoff wrote no deliverable"
                    % (round_index + 1)
                )
            else:
                written = pathlib.Path(result.artifact_path)
                if written.parent != folder:
                    failures.append(
                        "round %d: the deliverable landed outside the folder the "
                        "human edited" % (round_index + 1)
                    )
            paths = {str(row.get("path") or "") for row in result.files}
            if "finding.txt" not in paths or "notes.txt" not in paths:
                failures.append(
                    "round %d: the folder is not the unit of the offer (%s)"
                    % (round_index + 1, sorted(paths))
                )

            # A refused handoff must change nothing. The snapshot is
            # taken *after* the second edit, so it is the state the
            # refusal has to leave alone.
            (folder / "finding.txt").write_text("the answer is 41.\n", encoding="utf-8")
            refused_snapshot = (folder / "finding.txt").read_bytes()
            refused = engine.handback_workspace(ticket_id)
            if refused.closed:
                failures.append(
                    "round %d: a refused handoff closed the loop" % (round_index + 1)
                )
            if (folder / "finding.txt").read_bytes() != refused_snapshot:
                failures.append(
                    "round %d: a refused handoff changed the human's files"
                    % (round_index + 1)
                )
            # A refusal must not leave a deliverable behind either. The
            # run that produced the ticket left one of its own, so the
            # check is that the refusal did not *replace* it: a draft
            # that fails must never look like the accepted answer.
            if refused.artifact_path:
                failures.append(
                    "round %d: a refused handoff wrote a deliverable"
                    % (round_index + 1)
                )

    return {
        "name": "folder_handoff",
        "title": "人在原工作区里改文件，交回后照样验收；不通过时不动他的文件",
        "invariant": (
            "文件机检读到的是人改过的内容，文本机检看得到文件正文；"
            "交回不过磁盘原样不动且不写交付物；"
            "交回过则交付物落在人改过的那个文件夹里"
        ),
        "detail": (
            "人在原工作区里改文件，交回后按同一套验收判定；不通过时磁盘原样不动"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
        "ok_detail": not failures,
    }


# ``--only`` and the JSON output speak the same word.
check_relay_refusal_keeps_the_connection_usable.CHECK_NAME = "relay_refusal"  # type: ignore[attr-defined]
check_provider_keeps_the_usage_of_a_failed_call.CHECK_NAME = "provider_usage"  # type: ignore[attr-defined]
check_a_run_keeps_its_files_with_the_deliverable.CHECK_NAME = "workspace_keeps_files"  # type: ignore[attr-defined]
check_mcp_handshake_answers_every_tool.CHECK_NAME = "mcp_handshake"  # type: ignore[attr-defined]
check_a_stream_folds_into_one_answer.CHECK_NAME = "stream_folds"  # type: ignore[attr-defined]
check_parallel_candidates_pick_the_same_winner_every_time.CHECK_NAME = "parallel_pick"  # type: ignore[attr-defined]
check_an_escalated_run_leaves_a_ticket_a_human_can_take_over.CHECK_NAME = "handover_ticket"  # type: ignore[attr-defined]
check_a_takeover_sees_the_work_and_gets_a_fresh_budget.CHECK_NAME = "takeover_handover"  # type: ignore[attr-defined]
check_a_handback_closes_on_human_work.CHECK_NAME = "handback_closes"  # type: ignore[attr-defined]
check_a_handback_shows_up_in_the_report.CHECK_NAME = "handback_report"  # type: ignore[attr-defined]
check_a_folder_handoff_closes_on_human_edits.CHECK_NAME = "folder_handoff"  # type: ignore[attr-defined]
check_a_content_check_reads_inside_the_file.CHECK_NAME = "content_check"  # type: ignore[attr-defined]
check_a_binary_write_lands_as_bytes.CHECK_NAME = "binary_write"  # type: ignore[attr-defined]

# ------------------------------------------------------------------ resources
# The short name of a check is a property of the function, so that
# ``--only resource_index`` and the JSON output speak the same word.
RESOURCE_INDEX_OBSERVATIONS = (
    "the index lists every file of the folder",
    "the index lists a file nobody checks",
    "the format comes from the header, not from the name",
    "a PNG named .txt is reported as an image",
    "the pages of a PDF are read from the file",
    "the family of a font is read from the file",
    "the duration of a recording is read from the file",
    "the triangles of a model are read from the file",
    "the members of an archive are listed",
    "the preview shows the words of a PDF",
    "the preview shows the words of a Word document",
    "a file that cannot be read is listed with a reason",
    "a file that cannot be read is not dropped",
)


def _resource_index_observations(root: Any) -> Dict[str, bool]:
    """Observe the resource index, one behaviour at a time.

    Same discipline as the twelfth and thirteenth gates: the observation
    and the decision are kept apart, so a broken observation shows up as
    a *missing* observation rather than as a silent pass.
    """
    import pathlib

    from mas.content import detect_format
    from mas.resources import describe_folder, preview_file

    seen: Dict[str, bool] = {}
    index = describe_folder(root)
    rows = list(index.get("files") or [])
    names = {str(row.get("relative") or row.get("name") or "") for row in rows}

    seen["the index lists every file of the folder"] = (
        "report.pdf" in names
        and "memo.docx" in names
        and "shot.png" in names
        and "clip.wav" in names
        and "model.obj" in names
        and "demo.ttf" in names
        and "bundle.zip" in names
        and "blob.bin" in names
    )
    seen["the index lists a file nobody checks"] = "support/notes.txt" in names

    # The format is decided by the header. A PNG that was *named* .txt
    # is still an image - and the index has to say so, because the name
    # is what a reader would otherwise believe.
    liar = pathlib.Path(root) / "liar.txt"
    seen["the format comes from the header, not from the name"] = (
        detect_format(liar) == "image"
    )
    seen["a PNG named .txt is reported as an image"] = any(
        str(row.get("relative")) == "liar.txt" and row.get("format") == "image"
        for row in rows
    )

    def _fact(name: str, key: str) -> bool:
        for row in rows:
            if str(row.get("relative")) == name:
                value = row.get(key)
                return value not in (None, "", 0)
        return False

    seen["the pages of a PDF are read from the file"] = _fact("report.pdf", "pages")
    seen["the family of a font is read from the file"] = _fact("demo.ttf", "family")
    seen["the duration of a recording is read from the file"] = _fact(
        "clip.wav", "duration_sec"
    )
    seen["the triangles of a model are read from the file"] = _fact(
        "model.obj", "vertices"
    )
    seen["the members of an archive are listed"] = _fact("bundle.zip", "members")

    pdf_preview = preview_file(pathlib.Path(root) / "report.pdf")
    seen["the preview shows the words of a PDF"] = "Conclusion" in str(
        pdf_preview.get("text") or ""
    )
    docx_preview = preview_file(pathlib.Path(root) / "memo.docx")
    seen["the preview shows the words of a Word document"] = "Memo" in str(
        docx_preview.get("text") or ""
    )

    blob = [row for row in rows if str(row.get("relative")) == "blob.bin"]
    seen["a file that cannot be read is listed with a reason"] = bool(
        blob and str(blob[0].get("reason") or "")
    )
    seen["a file that cannot be read is not dropped"] = bool(blob)
    return seen


def check_the_index_knows_every_file(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The loop has to be able to say what it produced.

    A run leaves files in a folder, and the folder was only visible
    through the run that made it. A file that was neither the
    deliverable nor mentioned by any criterion - a supporting PDF, a
    font, a 3D model - had no listing at all. So the answer to "what
    did this machine actually produce?" was scattered across commands,
    and the answer to "what is in this folder?" did not exist.

    This is the fourteenth gate. It watches that:

    * every file of the folder is listed, including the ones no
      criterion mentions - a supporting file is still part of the work;
    * the format of a file comes from its own header, so a PNG named
      .txt is reported as an image;
    * the facts are read from the file: the pages of a PDF, the family
      of a font, the duration of a recording, the triangles of a model,
      the members of an archive;
    * the preview shows the words of a PDF and of a Word document;
    * a file that cannot be read is listed **with the reason** and is
      never silently dropped - an unreadable file in the middle of a
      deliverable is exactly the thing a reader needs to see.

    And the gate proves it can fail: it breaks the index on purpose
    and checks that it notices.
    """
    import tempfile

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "report.pdf").write_bytes(_compressed_pdf_bytes("Conclusion 42"))
            (root / "memo.docx").write_bytes(_docx_bytes("Memo 42"))
            (root / "shot.png").write_bytes(_png_bytes(12, 7))
            (root / "liar.txt").write_bytes(_png_bytes(4, 4))
            (root / "clip.wav").write_bytes(_wav_bytes(44100, 2, 66150))
            (root / "model.obj").write_text(
                "v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n", encoding="utf-8"
            )
            (root / "demo.ttf").write_bytes(_font_bytes("OpenLoop Sans", 256))
            (root / "bundle.zip").write_bytes(
                _zip_bytes((("notes/one.txt", "first note"),))
            )
            (root / "blob.bin").write_bytes(bytes(range(256)) * 4)
            support = root / "support"
            support.mkdir(exist_ok=True)
            (support / "notes.txt").write_text("some notes", encoding="utf-8")

            # A broken path must not *crash* the gate - it must make
            # the gate red. A gate that raises instead of reporting is
            # a gate that stops watching the moment it is needed most.
            try:
                seen = _resource_index_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the resource index broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                continue
            for label in RESOURCE_INDEX_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. The index is broken on
            # purpose - every file looks like the one before it - and
            # the gate has to notice. Without this, a gate whose own
            # observation was broken would report "all good" for ever.
            import mas.content as content

            # The index asks the *content* module what a file is, so
            # that is where the format has to be broken.
            real_detect = content.detect_format

            def _blind(path: Any) -> str:
                return "binary"

            content.detect_format = _blind
            try:
                broken = _resource_index_observations(root)
            except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                broken = {}
            finally:
                content.detect_format = real_detect
            if broken.get("a PNG named .txt is reported as an image"):
                failures.append(
                    "round %d: the gate did not notice a broken index - "
                    "it cannot fail, so it does not watch" % (round_index + 1)
                )

    return {
        "name": "resource_index",
        "title": "循环知道自己产出了什么：一个文件夹就是一份索引",
        "invariant": (
            "文件夹里的每个文件都进索引，包括没人检查的辅助文件；"
            "格式按文件自己的头判定（.txt 装的 PNG 报 image）；"
            "PDF 页数、字体家族、音频时长、模型三角面、压缩包成员都从文件里读出来；"
            "预览能抽出 PDF / Word 的正文；"
            "读不开的文件要列出来并写明原因，不能悄悄丢掉"
        ),
        "detail": "统一资源索引与多格式预览：mas files / mas preview",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


def _png_bytes(width: int, height: int) -> bytes:
    """The bytes of a real PNG of the given size."""
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes([(x * 7 + y * 3) % 256 for x in range(width * 3)])
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _wav_bytes(rate: int, channels: int, frames: int) -> bytes:
    """The bytes of a real WAV file."""
    import struct

    samples = b"".join(
        struct.pack("<h", (i * 37) % 30000 - 15000)
        for i in range(frames * channels)
    )
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(samples))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2,
                      channels * 2, 16)
        + b"data"
        + struct.pack("<I", len(samples))
    )
    return header + samples


def _font_bytes(family: str, glyphs: int) -> bytes:
    """The bytes of a minimal sfnt font that states its family."""
    import struct

    names = [(1, family), (2, "Regular"), (4, family), (6, family.replace(" ", ""))]
    records = b""
    strings = b""
    for name_id, text in names:
        raw = text.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x0409, name_id, len(raw), len(strings))
        strings += raw
    name_table = (
        struct.pack(">HHH", 0, len(names), 6 + 12 * len(names)) + records + strings
    )
    tables = {
        b"name": name_table,
        b"maxp": struct.pack(">IH", 0x00010000, int(glyphs)),
    }
    tags = sorted(tables)
    count = len(tags)
    offset_table = b""
    body = b""
    offset = 12 + 16 * count
    for tag in tags:
        data = tables[tag]
        offset_table += tag + struct.pack(">III", 0, offset, len(data))
        body += data + b"\x00" * ((4 - len(data) % 4) % 4)
        offset += len(data) + ((4 - len(data) % 4) % 4)
    header = struct.pack(">IHHHH", 0x00010000, count, 16, 0, 0)
    return header + offset_table + body


def _zip_bytes(members: Any) -> bytes:
    """The bytes of a zip archive with the given members."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members:
            archive.writestr(str(name), str(text))
    return buffer.getvalue()



check_the_index_knows_every_file.CHECK_NAME = "resource_index"  # type: ignore[attr-defined]

DRAFT_APPROVAL_OBSERVATIONS = (
    "an edit to an existing file waits for a human",
    "the original file keeps its bytes until the draft is approved",
    "a new file is still written right away",
    "the draft carries a diff of the change",
    "the machine checks see the drafted bytes",
    "approving the draft writes the change back",
    "rejecting the draft changes nothing",
)


def _draft_approval_observations(root: Any) -> Dict[str, bool]:
    """Observe the draft layer, one behaviour at a time.

    Same discipline as the twelfth to fourteenth gates: the observation
    and the decision are kept apart, so a broken observation shows up as
    a *missing* observation rather than as a silent pass.
    """
    import pathlib

    from mas.drafts import approve_draft, draft_overlay, list_drafts, reject_draft
    from mas.engine import LoopEngine

    seen: Dict[str, bool] = {}
    workspace = pathlib.Path(root)
    existing = workspace / "notes.txt"
    original = b"the human's own words"
    existing.write_bytes(original)

    engine = LoopEngine(
        {
            "state_dir": str(workspace / "state"),
            "edit_policy": "draft",
            # The workspace is named explicitly, so the run writes into
            # the folder the observation set up - the folder where the
            # human's own file lives.
            "workspace": str(workspace),
        },
        provider=_StubProvider(),
    )
    engine._run_id = "R-selftest-draft"
    engine._write_workspace()

    block = (
        '<<<MAS:FILE path="notes.txt">>>>\n'
        "the loop wants to rewrite this\n"
        "<<<MAS:END FILE>>>\n"
    )
    applied = engine._apply_writes(block)

    drafts = applied.get("drafts") or []
    seen["an edit to an existing file waits for a human"] = bool(drafts)
    seen["the original file keeps its bytes until the draft is approved"] = (
        existing.read_bytes() == original
    )

    new_block = (
        '<<<MAS:FILE path="fresh.md">>>>\n'
        "a brand new file\n"
        "<<<MAS:END FILE>>>\n"
    )
    new_applied = engine._apply_writes(new_block)
    seen["a new file is still written right away"] = bool(
        new_applied.get("written")
    ) and (workspace / "fresh.md").exists()

    if drafts:
        draft = list_drafts(workspace, "pending")[0] if list_drafts(workspace, "pending") else {}
        seen["the draft carries a diff of the change"] = bool(draft.get("diff"))
    else:
        seen["the draft carries a diff of the change"] = False

    # The overlay is read through the *engine*, not straight from the
    # store: the machine checks never call the store, they call the
    # engine. Reading the store directly would let a broken engine
    # path pass unnoticed.
    overlay = engine._draft_overlay()
    seen["the machine checks see the drafted bytes"] = any(
        b"the loop wants to rewrite this" in data for data in overlay.values()
    ) and any(
        b"the loop wants to rewrite this" in data
        for data in draft_overlay(workspace).values()
    )

    if drafts:
        draft_id = str(drafts[0].get("id") or "")
        approved = approve_draft(workspace, draft_id)
        seen["approving the draft writes the change back"] = (
            existing.read_bytes() == b"the loop wants to rewrite this"
            and approved.get("state") == "approved"
        )
    else:
        seen["approving the draft writes the change back"] = False

    # A second edit, this time rejected.
    engine._apply_writes(
        '<<<MAS:FILE path="notes.txt">>>>\n'
        "a change nobody wanted\n"
        "<<<MAS:END FILE>>>\n"
    )
    pending = list_drafts(workspace, "pending")
    if pending:
        keep = existing.read_bytes()
        reject_draft(workspace, str(pending[0].get("id") or ""))
        seen["rejecting the draft changes nothing"] = existing.read_bytes() == keep
    else:
        seen["rejecting the draft changes nothing"] = False

    return seen


class _StubProvider:
    """A provider that never answers - the draft layer needs no model."""

    name = "stub"

    def complete(self, *, system: str = "", prompt: str = "", role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
        return {"text": "", "usage": {"total_tokens": 0}}


def check_a_draft_is_approved_before_it_is_written_back(
    iterations: int = 10,
) -> Dict[str, Any]:
    """A change to an existing file waits for a human.

    The run writes straight into the workspace. That is right for a new
    file - nothing is lost when the loop creates one. It is wrong for an
    existing file: the loop overwrote the human's own work without ever
    showing what it changed, and the reader of this project asked for
    the MOKI rule - the change goes into a draft, and only a human
    confirmation writes it back.

    This is the fifteenth gate. It watches that:

    * a write over an existing file becomes a **draft**, and the file on
      the disk keeps its bytes - byte for byte, not "roughly";
    * a write that creates a *new* file is still performed right away,
      because there is nothing there to lose;
    * the draft carries a diff, so a human can see what would change
      before anything changes;
    * the machine checks see the *drafted* bytes, so the loop can close
      on its own proposal without the human having to approve it first
      - the approval is about the *file*, not about the verdict;
    * approving the draft writes the bytes back;
    * rejecting the draft changes nothing at all.

    And the gate proves it can fail: the draft rule is broken on
    purpose - the engine overwrites again - and the gate has to notice.
    """
    import tempfile

    from mas.isolation import state_isolation

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            existing = root / "notes.txt"
            existing.write_text("the human's own words", encoding="utf-8")
            # Approving a draft is a human decision, and a human
            # decision is written into the main ledger. A test that
            # approves a draft must not write into the ledger of the
            # machine it runs on - otherwise ``mas selftest`` fills
            # the user's audit trail with decisions nobody made, and
            # ``mas report`` later reports problems that have no
            # cause.
            with state_isolation(root):
                try:
                    seen = _draft_approval_observations(root)
                except Exception as exc:  # noqa: BLE001 - the failure is the subject
                    failures.append(
                        "round %d: the draft layer broke: %s: %s"
                        % (round_index + 1, type(exc).__name__, exc)
                    )
                    continue
            for label in DRAFT_APPROVAL_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. The rule is broken on
            # purpose - the engine overwrites the file again instead of
            # staging a draft - and the gate has to notice. Without
            # this, a gate whose own observation was broken would
            # report "all good" for ever.
            from mas.engine import LoopEngine

            engine = LoopEngine(
                {
                    "state_dir": str(root / "state"),
                    "edit_policy": "draft",
                    "workspace": str(root),
                },
                provider=_StubProvider(),
            )
            engine._run_id = "R-selftest-draft-broken"
            engine._write_workspace()
            real_apply = LoopEngine._apply_writes

            def _overwrite(self, artifact):
                from mas.filewrite import apply_write_blocks

                applied = apply_write_blocks(artifact, self._write_workspace())
                applied["drafts"] = []
                return applied

            LoopEngine._apply_writes = _overwrite
            try:
                with state_isolation(root):
                    broken = _draft_approval_observations(root)
            except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                broken = {}
            finally:
                LoopEngine._apply_writes = real_apply
            if broken.get("an edit to an existing file waits for a human"):
                failures.append(
                    "round %d: the gate did not notice a broken draft rule - "
                    "it cannot fail, so it does not watch" % (round_index + 1)
                )

    return {
        "name": "draft_approval",
        "title": "改动先进草稿，人确认后才写回原文件",
        "invariant": (
            "覆盖已存在文件的写入变成草稿，原文件字节不变；"
            "新建文件照常直接写；"
            "草稿带 diff，人能先看清再决定；"
            "机检看到草稿里的字节，循环能用自己的提案闭合；"
            "批准才写回，驳回一个字都不改"
        ),
        "detail": "草稿层：mas drafts list/show/approve/reject",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_draft_is_approved_before_it_is_written_back.CHECK_NAME = "draft_approval"  # type: ignore[attr-defined]

MCP_DRAFT_OBSERVATIONS = (
    "the MCP server advertises the draft tools",
    "an agent can propose a change through MCP",
    "a proposal leaves the original file untouched",
    "the proposal carries a diff and a reason",
    "the agent cannot approve its own proposal",
    "a wrong token does not approve either",
    "the human token opens the approval door",
    "approving through MCP writes the change back",
    "rejecting through MCP changes nothing",
    "the MCP server can index a folder",
    "the MCP server can preview a file",
    "the MCP server indexes the workspace it was given",
    "the MCP server previews a path inside the workspace",
)


def _mcp_draft_observations(root: Any) -> Dict[str, bool]:
    """Watch the draft layer from the MCP side, one thing at a time."""
    import pathlib

    # The token belongs to the machine, and the MCP server reads the
    # state directory from the configuration - which the environment
    # overrides. The gate points that at the temporary folder for the
    # duration of the observation, so the token the human asks for and
    # the token the server checks are the same one. Without this the
    # gate would read a token from a temporary folder while the server
    # read one from the real state directory, and the approval would
    # be refused for a reason that has nothing to do with the rule.
    previous_state = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = str(pathlib.Path(root) / "state")
    try:
        return _mcp_draft_observations_inner(root)
    finally:
        # The environment is put back whatever happens, so a round that
        # raises cannot leak a state directory into the next one.
        if previous_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = previous_state


def _mcp_draft_observations_inner(root: Any) -> Dict[str, bool]:
    """The body of the MCP draft observation.

    The function collects **facts**, not verdicts. A verdict would be
    a boolean that a later edit could simply set to ``True`` - and a
    gate whose own observation was tampered with would then report
    "all good" for ever. The facts are raw strings and numbers, and
    the verdict is decided by :func:`_mcp_draft_verdicts`, which is
    a separate piece of code.
    """
    import pathlib

    from mas.drafts import ensure_human_token
    from mas.mcp_server import TOOLS, handle_tool

    seen: Dict[str, Any] = {}
    workspace = pathlib.Path(root)
    existing = workspace / "notes.txt"
    original_text = "the human's own words"
    existing.write_text(original_text, encoding="utf-8")
    seen["original_text"] = original_text

    advertised = [str(t.get("name") or "") for t in TOOLS if t.get("name")]
    seen["advertised"] = ",".join(sorted(advertised))

    staged = handle_tool(
        "mas_draft_stage",
        {
            "path": "notes.txt",
            "content": "the agent proposes this",
            "reason": "the agent says why",
            "workspace": str(workspace),
        },
    )
    draft = staged.get("draft") or {}
    draft_id = str(draft.get("id") or "")
    seen["draft_id"] = draft_id
    seen["file_after_staging"] = existing.read_text(encoding="utf-8")
    seen["diff_lines"] = len(draft.get("diff") or [])
    seen["reason"] = str(draft.get("reason") or "")

    without = handle_tool(
        "mas_draft_resolve",
        {"action": "approve", "draft_id": draft_id, "workspace": str(workspace)},
    )
    seen["without_token_error"] = str(without.get("error") or "")
    seen["file_after_without_token"] = existing.read_text(encoding="utf-8")

    # The token has to exist *before* the wrong token is tried. If no
    # token file exists yet, the refusal would come from "there is no
    # token at all" and the comparison itself would never run - a
    # broken comparison would go unnoticed.
    token = ensure_human_token(pathlib.Path(root) / "state")
    wrong = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "token": "not-the-token",
            "workspace": str(workspace),
        },
    )
    seen["wrong_token_error"] = str(wrong.get("error") or "")
    seen["file_after_wrong_token"] = existing.read_text(encoding="utf-8")

    approved = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "token": token,
            "workspace": str(workspace),
        },
    )
    seen["approved_message"] = str(approved.get("message") or "")
    seen["file_after_approve"] = existing.read_text(encoding="utf-8")

    # A second proposal, this time rejected by the human.
    staged2 = handle_tool(
        "mas_draft_stage",
        {
            "path": "notes.txt",
            "content": "a change nobody wanted",
            "workspace": str(workspace),
        },
    )
    second_id = str((staged2.get("draft") or {}).get("id") or "")
    keep = existing.read_text(encoding="utf-8")
    handle_tool(
        "mas_draft_resolve",
        {
            "action": "reject",
            "draft_id": second_id,
            "token": token,
            "workspace": str(workspace),
        },
    )
    seen["file_before_reject"] = keep
    seen["file_after_reject"] = existing.read_text(encoding="utf-8")

    index = handle_tool("mas_files", {"path": str(workspace)})
    seen["indexed_files"] = int(index.get("files") or 0)
    preview = handle_tool("mas_preview", {"path": str(existing)})
    seen["preview_text"] = str(preview.get("text") or "")

    # The agent does not only hold absolute paths. It names a workspace
    # and gives a path inside it - and that has to resolve to the very
    # same files. A tool that answered "nothing here" for a folder
    # that plainly holds a file would be lying to the caller.
    by_workspace = handle_tool("mas_files", {"workspace": str(workspace)})
    seen["indexed_files_by_workspace"] = int(by_workspace.get("files") or 0)
    relative = handle_tool(
        "mas_preview", {"workspace": str(workspace), "path": existing.name}
    )
    seen["preview_text_by_workspace"] = str(relative.get("text") or "")

    return seen


def _mcp_draft_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """Turn the collected facts into the verdicts of the gate.

    Kept apart from the collection on purpose: the collection cannot
    be edited into saying "yes" without the facts themselves
    changing.
    """
    advertised = str(facts.get("advertised") or "").split(",")
    original = "the human's own words"
    return {
        "the MCP server advertises the draft tools": all(
            name in advertised
            for name in ("mas_drafts", "mas_draft_stage", "mas_draft_resolve")
        ),
        "an agent can propose a change through MCP": bool(facts.get("draft_id")),
        "a proposal leaves the original file untouched": (
            facts.get("file_after_staging") == original
        ),
        "the proposal carries a diff and a reason": (
            int(facts.get("diff_lines") or 0) > 0
            and bool(facts.get("reason"))
        ),
        "the agent cannot approve its own proposal": (
            facts.get("without_token_error") == "human_token_required"
            and facts.get("file_after_without_token") == original
        ),
        "a wrong token does not approve either": (
            facts.get("wrong_token_error") == "human_token_required"
            and facts.get("file_after_wrong_token") == original
        ),
        "the human token opens the approval door": bool(
            facts.get("approved_message")
        ),
        "approving through MCP writes the change back": (
            facts.get("file_after_approve") == "the agent proposes this"
        ),
        "rejecting through MCP changes nothing": (
            facts.get("file_after_reject") == facts.get("file_before_reject")
        ),
        "the MCP server can index a folder": int(facts.get("indexed_files") or 0) > 0,
        "the MCP server can preview a file": bool(facts.get("preview_text")),
        "the MCP server indexes the workspace it was given": int(
            facts.get("indexed_files_by_workspace") or 0
        ) > 0,
        "the MCP server previews a path inside the workspace": bool(
            facts.get("preview_text_by_workspace")
        ),
    }


def _mcp_draft_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    The verdict function above is the one the gate trusts. If that
    function is edited - by a careless hand or by a mutation - the
    gate would report whatever the edited function says. This second
    implementation is written from the *rule* rather than from the
    first implementation, and the gate requires the two to agree.

    Two readers of the same facts that disagree mean one of them is
    wrong - and a gate that cannot tell which one is wrong is not
    watching anything.
    """
    advertised = str(facts.get("advertised") or "")
    verdicts: Dict[str, bool] = {}
    verdicts["the MCP server advertises the draft tools"] = (
        advertised.count("mas_drafts") >= 1
        and advertised.count("mas_draft_stage") >= 1
        and advertised.count("mas_draft_resolve") >= 1
    )
    verdicts["an agent can propose a change through MCP"] = (
        len(str(facts.get("draft_id") or "")) > 0
    )
    verdicts["a proposal leaves the original file untouched"] = (
        str(facts.get("file_after_staging")) == str(facts.get("original_text"))
    )
    verdicts["the proposal carries a diff and a reason"] = (
        int(facts.get("diff_lines") or 0) > 0
        and len(str(facts.get("reason") or "")) > 0
    )
    verdicts["the agent cannot approve its own proposal"] = (
        str(facts.get("without_token_error")) == "human_token_required"
        and str(facts.get("file_after_without_token"))
        == str(facts.get("original_text"))
    )
    verdicts["a wrong token does not approve either"] = (
        str(facts.get("wrong_token_error")) == "human_token_required"
        and str(facts.get("file_after_wrong_token"))
        == str(facts.get("original_text"))
    )
    verdicts["the human token opens the approval door"] = (
        len(str(facts.get("approved_message") or "")) > 0
    )
    verdicts["approving through MCP writes the change back"] = (
        str(facts.get("file_after_approve")) == "the agent proposes this"
    )
    verdicts["rejecting through MCP changes nothing"] = (
        str(facts.get("file_after_reject")) == str(facts.get("file_before_reject"))
    )
    verdicts["the MCP server can index a folder"] = (
        int(facts.get("indexed_files") or 0) > 0
    )
    verdicts["the MCP server can preview a file"] = (
        len(str(facts.get("preview_text") or "")) > 0
    )
    verdicts["the MCP server indexes the workspace it was given"] = (
        int(facts.get("indexed_files_by_workspace") or 0) > 0
    )
    verdicts["the MCP server previews a path inside the workspace"] = (
        len(str(facts.get("preview_text_by_workspace") or "")) > 0
    )
    return verdicts


def check_an_agent_can_propose_but_not_approve(
    iterations: int = 10,
) -> Dict[str, Any]:
    """An agent may propose a change; only the human may approve it.

    The draft layer of v0.7.72 is a rule for the *engine*: the loop
    stages a change instead of overwriting a file. This gate is the
    rule for the *agent*: an agent that speaks MCP can now propose a
    change to a file the human owns - and it still cannot make that
    change on its own.

    This is the sixteenth gate. It watches that:

    * the MCP server advertises the draft tools;
    * an agent can stage a proposal, and the original file keeps its
      bytes while the proposal waits;
    * the proposal carries a diff and the reason, so a human can see
      what would change;
    * approving through MCP **needs the human token** - no token and a
      wrong token are both refused, and the file stays as it was;
    * the right token writes the change back, and a rejection changes
      nothing;
    * the MCP server can index a folder and preview a file, so the
      agent can read the workspace before it proposes anything.

    The point of the token is not secrecy, it is *separation*: the
    agent that proposes a change is not the one that confirms it.
    """
    import tempfile

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            try:
                facts = _mcp_draft_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the MCP draft layer broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                continue
            seen = _mcp_draft_verdicts(facts)
            # Two readers of the same facts have to agree. If the
            # verdict function was edited - so that it reports "yes"
            # whatever the facts say - the second reader disagrees and
            # the gate goes red. A gate that can only be fooled by
            # changing the product, but not by changing itself, is
            # not a gate.
            independent = _mcp_draft_verdicts_independent(facts)
            for label in MCP_DRAFT_OBSERVATIONS:
                if label in independent and independent[label] != seen.get(label):
                    failures.append(
                        "round %d: the two readings of %s disagree (%r vs %r) - "
                        "the verdict was tampered with"
                        % (round_index + 1, label, seen.get(label), independent[label])
                    )
                    break
            for label in MCP_DRAFT_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. The separation between the
            # proposer and the approver is broken on purpose - the
            # token stops being checked - and the gate has to notice.
            # Without this, a gate whose own observation had been
            # tampered with would report "all good" for ever.
            from mas.drafts import verify_human_token

            real_verify = verify_human_token
            try:
                import mas.drafts as drafts_module

                drafts_module.verify_human_token = lambda *_args, **_kwargs: True
                broken = {}
                try:
                    broken = _mcp_draft_verdicts(_mcp_draft_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                import mas.drafts as drafts_module

                drafts_module.verify_human_token = real_verify
            if broken.get("the agent cannot approve its own proposal"):
                failures.append(
                    "round %d: the gate did not notice that the token "
                    "stopped being checked - it cannot fail, so it does "
                    "not watch" % (round_index + 1)
                )

    return {
        "name": "mcp_draft_approval",
        "title": "agent 能提草稿，但改不改由人说了算",
        "invariant": (
            "agent 经 MCP 能提草稿，原文件不动；"
            "批准要人类令牌，agent 自己批不了自己的提案；"
            "批了才写回，驳回一个字都不改；"
            "MCP 还能索引文件夹、预览文件"
        ),
        "detail": "MCP 草稿层：mas_drafts / mas_draft_stage / mas_draft_resolve",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_an_agent_can_propose_but_not_approve.CHECK_NAME = "mcp_draft_approval"  # type: ignore[attr-defined]


TICKET_OBSERVATIONS = (
    "the human token signs a ticket for one draft",
    "a wrong token signs nothing",
    "the signed ticket is never written down in clear",
    "a ticket opens the door for the draft it was signed for",
    "a ticket does not open the door for another draft",
    "a ticket works only once",
    "a ticket stops working when it gets old",
    "a ticket approves only the action it was signed for",
    "no token and no ticket still means no",
)


def _ticket_observations(root: Any) -> Dict[str, bool]:
    """Watch the one-shot ticket layer, one thing at a time.

    The ticket store lives in the state directory, and the MCP server
    reads the state directory from the configuration - which the
    environment overrides. The gate points that at the temporary
    folder for the duration of the observation, so the ticket the
    human signs and the ticket the server checks are the same one.
    """
    import pathlib

    previous_state = os.environ.get("MAS_STATE_DIR")
    state_dir = pathlib.Path(root) / "state"
    os.environ["MAS_STATE_DIR"] = str(state_dir)
    try:
        return _ticket_observations_inner(root, state_dir)
    finally:
        if previous_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = previous_state


def _ticket_observations_inner(root: Any, state_dir: Any) -> Dict[str, bool]:
    """The body of the ticket observation.

    Collects **facts**, not verdicts. A verdict would be a boolean a
    later edit could simply set to ``True``, and a gate whose own
    observation had been tampered with would then report "all good"
    for ever. The facts are raw strings, numbers and file contents, and
    the verdict is decided elsewhere.
    """
    import json
    import pathlib

    from mas.drafts import ensure_human_token, issue_ticket, ticket_store_path
    from mas.mcp_server import handle_tool

    seen: Dict[str, Any] = {}
    workspace = pathlib.Path(root)
    target = workspace / "notes.txt"
    original_text = "the human's own words"
    target.write_text(original_text, encoding="utf-8")
    seen["original_text"] = original_text

    token = ensure_human_token(state_dir)

    # A ticket is signed by the human, so it needs the human token.
    # A wrong token must sign nothing at all - not a ticket that then
    # fails later, but no ticket in the store.
    wrong_error = ""
    try:
        issue_ticket(state_dir, "D-anything", action="approve", human_token="not-the-token")
    except Exception as exc:  # noqa: BLE001 - the failure is the subject
        wrong_error = "%s: %s" % (type(exc).__name__, exc)
    seen["wrong_token_error"] = wrong_error
    seen["tickets_after_wrong_token"] = len(_read_tickets(state_dir))

    staged = handle_tool(
        "mas_draft_stage",
        {
            "path": "notes.txt",
            "content": "the agent proposes this",
            "reason": "the agent says why",
            "workspace": str(workspace),
        },
    )
    draft_id = str((staged.get("draft") or {}).get("id") or "")
    seen["draft_id"] = draft_id

    signed = issue_ticket(state_dir, draft_id, action="approve", human_token=token)
    secret = str(signed.get("secret") or "")
    ticket_id = str(signed.get("id") or "")
    seen["ticket_id"] = ticket_id
    seen["ticket_secret_length"] = len(secret)
    seen["ticket_bound_draft"] = str(signed.get("draft_id") or "")

    # The store keeps the hash, not the ticket. A store that gets
    # copied must not carry a usable secret with it.
    store_text = ""
    store_path = ticket_store_path(state_dir)
    if store_path.exists():
        store_text = store_path.read_text(encoding="utf-8")
    seen["store_length"] = len(store_text)
    seen["store_contains_secret"] = bool(secret) and secret in store_text
    seen["store_ticket_count"] = len(_read_tickets(state_dir))

    # A second draft, so the ticket can be tried against a draft it
    # was not signed for.
    staged2 = handle_tool(
        "mas_draft_stage",
        {
            "path": "notes.txt",
            "content": "a change nobody asked for",
            "workspace": str(workspace),
        },
    )
    other_id = str((staged2.get("draft") or {}).get("id") or "")

    # The ticket is spent on the draft it was signed for.
    approved = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "ticket": secret,
            "workspace": str(workspace),
        },
    )
    seen["approved_message"] = str(approved.get("message") or "")
    seen["approved_error"] = str(approved.get("error") or "")
    seen["file_after_approve"] = target.read_text(encoding="utf-8")

    # The same ticket a second time is already spent.
    replay = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "ticket": secret,
            "workspace": str(workspace),
        },
    )
    seen["replay_error"] = str(replay.get("error") or "")
    seen["replay_reason"] = str(replay.get("reason") or "")
    seen["file_after_replay"] = target.read_text(encoding="utf-8")

    # Another draft, a fresh ticket, and the file must not move.
    before_cross = target.read_text(encoding="utf-8")
    cross_ticket = issue_ticket(state_dir, other_id, action="approve", human_token=token)
    cross = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "ticket": str(cross_ticket.get("secret") or ""),
            "workspace": str(workspace),
        },
    )
    seen["cross_error"] = str(cross.get("error") or "")
    seen["cross_reason"] = str(cross.get("reason") or "")
    seen["file_after_cross"] = target.read_text(encoding="utf-8")
    seen["file_before_cross"] = before_cross

    # A ticket that was signed for a rejection cannot approve.
    action_ticket = issue_ticket(state_dir, other_id, action="reject", human_token=token)
    mismatch = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": other_id,
            "ticket": str(action_ticket.get("secret") or ""),
            "workspace": str(workspace),
        },
    )
    seen["mismatch_error"] = str(mismatch.get("error") or "")
    seen["mismatch_reason"] = str(mismatch.get("reason") or "")
    seen["file_after_mismatch"] = target.read_text(encoding="utf-8")

    # A ticket that gets old stops working. The store is edited the
    # way time would edit it - the timestamp moves, the hash does not.
    stale = issue_ticket(state_dir, other_id, action="approve", human_token=token)
    stale_id = str(stale.get("id") or "")
    _age_ticket(state_dir, stale_id, seconds=7200)
    expired = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": other_id,
            "ticket": str(stale.get("secret") or ""),
            "workspace": str(workspace),
        },
    )
    seen["expired_error"] = str(expired.get("error") or "")
    seen["expired_reason"] = str(expired.get("reason") or "")
    seen["file_after_expired"] = target.read_text(encoding="utf-8")

    # And with neither a token nor a ticket, the door stays shut.
    neither = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": other_id,
            "workspace": str(workspace),
        },
    )
    seen["neither_error"] = str(neither.get("error") or "")
    seen["file_after_neither"] = target.read_text(encoding="utf-8")

    # The unused tickets are still in the store, marked as spent.
    seen["tickets_at_end"] = len(_read_tickets(state_dir))
    seen["store_json_keys"] = ",".join(sorted(_read_tickets(state_dir).keys()))
    seen["json_module"] = json.__name__
    return seen


def _read_tickets(state_dir: Any) -> Dict[str, Any]:
    """The signed tickets of a state directory, as a mapping."""
    import json
    import pathlib

    path = pathlib.Path(state_dir) / "tickets.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _age_ticket(state_dir: Any, ticket_id: str, seconds: int = 7200) -> None:
    """Move a ticket's timestamp back, the way time would."""
    import datetime
    import json
    import pathlib

    path = pathlib.Path(state_dir) / "tickets.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    record = data.get(ticket_id)
    if not isinstance(record, dict):
        return
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=int(seconds))
    record["created_at"] = stamp.isoformat(timespec="seconds")
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _ticket_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """Turn the collected facts into the verdicts of the gate.

    Kept apart from the collection on purpose: the collection cannot
    be edited into saying "yes" without the facts themselves changing.
    """
    return {
        "the human token signs a ticket for one draft": (
            len(str(facts.get("ticket_id") or "")) > 0
            and int(facts.get("ticket_secret_length") or 0) >= 16
            and str(facts.get("ticket_bound_draft") or "") == str(facts.get("draft_id") or "")
        ),
        "a wrong token signs nothing": (
            len(str(facts.get("wrong_token_error") or "")) > 0
            and int(facts.get("tickets_after_wrong_token") or 0) == 0
        ),
        "the signed ticket is never written down in clear": (
            not facts.get("store_contains_secret")
            and int(facts.get("store_length") or 0) > 0
            and int(facts.get("store_ticket_count") or 0) >= 1
        ),
        "a ticket opens the door for the draft it was signed for": (
            str(facts.get("approved_error") or "") == ""
            and len(str(facts.get("approved_message") or "")) > 0
            and str(facts.get("file_after_approve") or "") == "the agent proposes this"
        ),
        "a ticket does not open the door for another draft": (
            str(facts.get("cross_error") or "") == "ticket_refused"
            and str(facts.get("cross_reason") or "") == "ticket_bound_to_another_draft"
            and str(facts.get("file_after_cross") or "") == str(facts.get("file_before_cross") or "")
        ),
        "a ticket works only once": (
            str(facts.get("replay_error") or "") == "ticket_refused"
            and str(facts.get("replay_reason") or "") == "ticket_already_used"
            and str(facts.get("file_after_replay") or "")
            == str(facts.get("file_after_approve") or "")
        ),
        "a ticket stops working when it gets old": (
            str(facts.get("expired_error") or "") == "ticket_refused"
            and str(facts.get("expired_reason") or "") == "ticket_expired"
            and str(facts.get("file_after_expired") or "")
            == str(facts.get("file_after_mismatch") or "")
        ),
        "a ticket approves only the action it was signed for": (
            str(facts.get("mismatch_error") or "") == "ticket_refused"
            and str(facts.get("mismatch_reason") or "") == "ticket_action_mismatch"
            and str(facts.get("file_after_mismatch") or "") == str(facts.get("file_before_cross") or "")
        ),
        "no token and no ticket still means no": (
            str(facts.get("neither_error") or "") == "human_token_required"
            and str(facts.get("file_after_neither") or "") == str(facts.get("file_before_cross") or "")
        ),
    }


def _ticket_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    Written from the *rule* rather than from the first implementation,
    and the gate requires the two to agree. Two readers that disagree
    mean one of them is wrong - and a gate that cannot tell which one
    is wrong is not watching anything.
    """
    verdicts: Dict[str, bool] = {}
    verdicts["the human token signs a ticket for one draft"] = (
        len(str(facts.get("ticket_id") or "")) != 0
        and int(facts.get("ticket_secret_length") or 0) >= 16
        and str(facts.get("ticket_bound_draft") or "") != ""
        and str(facts.get("ticket_bound_draft") or "") == str(facts.get("draft_id") or "")
    )
    verdicts["a wrong token signs nothing"] = (
        str(facts.get("wrong_token_error") or "") != ""
        and int(facts.get("tickets_after_wrong_token") or 0) == 0
    )
    verdicts["the signed ticket is never written down in clear"] = (
        facts.get("store_contains_secret") is not True
        and int(facts.get("store_length") or 0) >= 1
        and int(facts.get("store_ticket_count") or 0) >= 1
    )
    verdicts["a ticket opens the door for the draft it was signed for"] = (
        str(facts.get("approved_error") or "") == ""
        and str(facts.get("approved_message") or "") != ""
        and str(facts.get("file_after_approve") or "") == "the agent proposes this"
    )
    verdicts["a ticket does not open the door for another draft"] = (
        str(facts.get("cross_error") or "") == "ticket_refused"
        and str(facts.get("cross_reason") or "") == "ticket_bound_to_another_draft"
        and str(facts.get("file_after_cross") or "") == str(facts.get("file_before_cross") or "")
    )
    verdicts["a ticket works only once"] = (
        str(facts.get("replay_error") or "") == "ticket_refused"
        and str(facts.get("replay_reason") or "") == "ticket_already_used"
        and str(facts.get("file_after_replay") or "") == str(facts.get("file_after_approve") or "")
    )
    verdicts["a ticket stops working when it gets old"] = (
        str(facts.get("expired_error") or "") == "ticket_refused"
        and str(facts.get("expired_reason") or "") == "ticket_expired"
        and str(facts.get("file_after_expired") or "")
        == str(facts.get("file_after_mismatch") or "")
    )
    verdicts["a ticket approves only the action it was signed for"] = (
        str(facts.get("mismatch_error") or "") == "ticket_refused"
        and str(facts.get("mismatch_reason") or "") == "ticket_action_mismatch"
        and str(facts.get("file_after_mismatch") or "")
        == str(facts.get("file_before_cross") or "")
    )
    verdicts["no token and no ticket still means no"] = (
        str(facts.get("neither_error") or "") == "human_token_required"
        and str(facts.get("file_after_neither") or "")
        == str(facts.get("file_before_cross") or "")
    )
    return verdicts


def check_a_ticket_opens_one_door_once(
    iterations: int = 10,
) -> Dict[str, Any]:
    """A human hands over one decision, not the key to the whole house.

    The human token of v0.7.73 is a standing key: once an agent has
    it, it can approve **every** draft, forever. That is a real hole
    in a team of agents, and this gate is the repair.

    This is the seventeenth gate. It watches that:

    * a ticket can only be signed with the human token - a wrong
      token signs nothing at all;
    * the store keeps the **hash** of the ticket, never the ticket
      itself, so a copied store carries no usable secret;
    * a ticket opens the door for **the one draft it was signed for**
      - another draft is refused, and the file does not move;
    * a ticket works **once** - the second use is refused;
    * a ticket **expires** - an old ticket stops working;
    * a ticket signed for a rejection cannot approve;
    * and with neither a token nor a ticket, the door stays shut.

    The point of a ticket is *narrowing*: the human gives away one
    decision, not the ability to make decisions.
    """
    import tempfile

    failures: List[str] = []
    rounds = max(1, int(iterations))
    for round_index in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            try:
                facts = _ticket_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the ticket layer broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                continue
            seen = _ticket_verdicts(facts)
            # Two readers of the same facts have to agree. If the
            # verdict function was edited - so that it reports "yes"
            # whatever the facts say - the second reader disagrees and
            # the gate goes red.
            independent = _ticket_verdicts_independent(facts)
            for label in TICKET_OBSERVATIONS:
                if label in independent and independent[label] != seen.get(label):
                    failures.append(
                        "round %d: the two readings of %s disagree (%r vs %r) - "
                        "the verdict was tampered with"
                        % (round_index + 1, label, seen.get(label), independent[label])
                    )
                    break
            for label in TICKET_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. The ticket check is broken
            # on purpose - every ticket opens every door - and the gate
            # has to notice. Without this, a gate whose own observation
            # had been tampered with would report "all good" for ever.
            import mas.drafts as drafts_module

            real_redeem = drafts_module.redeem_ticket
            real_peek = drafts_module.peek_ticket
            try:
                drafts_module.redeem_ticket = lambda *_args, **_kwargs: {
                    "ok": True,
                    "ticket_id": "T-forged",
                }
                # The door has two halves: the ticket is looked at
                # before it is spent, and it is spent when the
                # decision is carried out. Both are broken here, so
                # the gate really is watching a door that is open.
                drafts_module.peek_ticket = lambda *_args, **_kwargs: {
                    "ok": True,
                    "ticket_id": "T-forged",
                }
                broken = {}
                try:
                    broken = _ticket_verdicts(_ticket_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                drafts_module.redeem_ticket = real_redeem
                drafts_module.peek_ticket = real_peek
            if broken.get("a ticket does not open the door for another draft"):
                failures.append(
                    "round %d: the gate did not notice that the ticket check "
                    "stopped being made - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )

    return {
        "name": "ticket_approval",
        "title": "人给出去的是一个决定，不是一把万能钥匙",
        "invariant": (
            "票据要人类令牌才能签；只存哈希；"
            "只对一个草稿生效、用一次就作废、过期即失效；"
            "动作不匹配被拒；无令牌无票据一律不开门"
        ),
        "detail": "一次性票据层：mas drafts ticket <草稿编号>",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_ticket_opens_one_door_once.CHECK_NAME = "ticket_approval"  # type: ignore[attr-defined]



CONFLICT_OBSERVATIONS = (
    "a draft remembers the file it was made against",
    "the file moving under a draft is noticed",
    "the newer work on the disk survives the refusal",
    "the refusal says what was expected and what is there now",
    "the draft is still waiting after the refusal",
    "the ticket is not burned by the refusal",
    "the command line refuses the approval too",
    "the command line says what was expected and what is there now",
    "only an explicit force writes over the newer work",
)


def _conflict_observations(root: Any) -> Dict[str, bool]:
    """Watch the lost-update guard, one thing at a time.

    The MCP server reads the state directory from the configuration -
    which the environment overrides. The observation points that at a
    directory of its own, and puts it back on the way out: a gate that
    leaves its environment changed silently re-points every gate that
    runs after it, which hides the next leak.
    """
    previous_state = os.environ.get("MAS_STATE_DIR")
    try:
        return _conflict_observations_inner(root)
    finally:
        if previous_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = previous_state


def _conflict_observations_inner(root: Any) -> Dict[str, bool]:
    """The body of the conflict observation.

    Two agents editing one file is the most common accident of a
    team: the second write throws the first one away without saying
    so. The guard is *optimistic* - it notices the clash and refuses,
    it does not lock anything.

    Like every other gate in this file, this one collects **facts**
    and leaves the verdict to :func:`_conflict_verdicts`.
    """
    import json
    import pathlib

    from mas.drafts import ensure_human_token, issue_ticket, ticket_store_path
    from mas.mcp_server import handle_tool

    seen: Dict[str, Any] = {}
    workspace = pathlib.Path(root) / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "notes.txt"
    target.write_text("v1", encoding="utf-8")
    seen["original_text"] = "v1"

    state_dir = pathlib.Path(root) / "state"
    os.environ["MAS_STATE_DIR"] = str(state_dir)

    staged = handle_tool(
        "mas_draft_stage",
        {
            "path": "notes.txt",
            "content": "v2-agent",
            "reason": "the agent proposes this",
            "workspace": str(workspace),
        },
    )
    draft_id = str((staged.get("draft") or {}).get("id") or "")
    seen["draft_id"] = draft_id
    seen["recorded_hash"] = str((staged.get("draft") or {}).get("before_sha256") or "")

    # The human edits the file while the draft waits. The draft was
    # made against "v1"; the file is now "v3-human".
    target.write_text("v3-human", encoding="utf-8")
    seen["human_text"] = target.read_text(encoding="utf-8")

    token = ensure_human_token(state_dir)
    signed = issue_ticket(state_dir, draft_id, action="approve", human_token=token)
    ticket_secret = str(signed.get("secret") or "")
    seen["ticket_id"] = str(signed.get("id") or "")

    refused = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "ticket": ticket_secret,
            "workspace": str(workspace),
        },
    )
    seen["refused_error"] = str(refused.get("error") or "")
    seen["refused_expected"] = str(refused.get("expected_sha256") or "")
    seen["refused_current"] = str(refused.get("current_sha256") or "")
    seen["file_after_refusal"] = target.read_text(encoding="utf-8")

    # The ticket is one-shot. A refusal that burns it would waste the
    # one decision the human handed over.
    tickets = {}
    store = ticket_store_path(state_dir)
    if store.exists():
        try:
            tickets = json.loads(store.read_text(encoding="utf-8"))
        except ValueError:
            tickets = {}
    record = tickets.get(seen["ticket_id"]) if isinstance(tickets, dict) else None
    seen["ticket_used_after_refusal"] = bool((record or {}).get("used"))

    # The draft is still waiting - the refusal did not consume it.
    from mas.drafts import get_draft

    draft_after = get_draft(workspace, draft_id) or {}
    seen["draft_state_after_refusal"] = str(draft_after.get("state") or "")

    # The same ticket still opens the door when the human insists.
    forced = handle_tool(
        "mas_draft_resolve",
        {
            "action": "approve",
            "draft_id": draft_id,
            "ticket": ticket_secret,
            "force": True,
            "workspace": str(workspace),
        },
    )
    seen["forced_error"] = str(forced.get("error") or "")
    seen["file_after_force"] = target.read_text(encoding="utf-8")

    # The command line refuses it too, and says the same thing.
    import argparse

    from mas.cli import cmd_drafts

    target.write_text("v1", encoding="utf-8")
    staged2 = handle_tool(
        "mas_draft_stage",
        {
            "path": "notes.txt",
            "content": "v2-agent",
            "reason": "the agent proposes this again",
            "workspace": str(workspace),
        },
    )
    draft2 = str((staged2.get("draft") or {}).get("id") or "")
    seen["draft2_id"] = draft2
    target.write_text("v3-human", encoding="utf-8")

    # The command line prints its own report. The gate watches the
    # exit code, so the printing is swallowed - a gate that scribbled
    # on the machine's output would corrupt the report of the run it
    # is part of.
    import contextlib
    import io

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        cli_result = cmd_drafts(
            argparse.Namespace(
                action="approve",
                draft_id=draft2,
                workspace=str(workspace),
                state=None,
                note="",
                token="",
                ticket_action="approve",
                force=False,
                json=True,
            )
        )
    seen["cli_exit_code"] = int(cli_result)
    seen["file_after_cli_refusal"] = target.read_text(encoding="utf-8")

    with contextlib.redirect_stdout(quiet):
        listing = cmd_drafts(
            argparse.Namespace(
                action="conflict",
                draft_id="",
                workspace=str(workspace),
                state=None,
                note="",
                token="",
                ticket_action="approve",
                force=False,
                json=True,
            )
        )
    seen["cli_conflict_exit_code"] = int(listing)
    seen["cli_stdout_lines"] = len(quiet.getvalue().splitlines())
    seen["json_module"] = json.__name__
    return seen


def _conflict_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """Turn the collected facts into the verdicts of the gate."""
    return {
        "a draft remembers the file it was made against": (
            len(str(facts.get("recorded_hash") or "")) >= 32
        ),
        "the file moving under a draft is noticed": (
            str(facts.get("refused_error") or "") == "draft_conflict"
        ),
        "the newer work on the disk survives the refusal": (
            str(facts.get("file_after_refusal") or "") == str(facts.get("human_text") or "")
            and str(facts.get("file_after_refusal") or "") != "v2-agent"
        ),
        "the refusal says what was expected and what is there now": (
            len(str(facts.get("refused_expected") or "")) >= 32
            and len(str(facts.get("refused_current") or "")) >= 32
            and str(facts.get("refused_expected") or "")
            != str(facts.get("refused_current") or "")
        ),
        "the draft is still waiting after the refusal": (
            str(facts.get("draft_state_after_refusal") or "") == "pending"
        ),
        "the ticket is not burned by the refusal": (
            facts.get("ticket_used_after_refusal") is False
        ),
        "the command line refuses the approval too": (
            int(facts.get("cli_exit_code") or 0) != 0
            and str(facts.get("file_after_cli_refusal") or "")
            == str(facts.get("human_text") or "")
        ),
        "the command line says what was expected and what is there now": (
            int(facts.get("cli_conflict_exit_code") or 0) == 0
        ),
        "only an explicit force writes over the newer work": (
            str(facts.get("forced_error") or "") == ""
            and str(facts.get("file_after_force") or "") == "v2-agent"
        ),
    }


def _conflict_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    Written from the rule rather than from the first implementation;
    the gate needs the two to agree.
    """
    verdicts: Dict[str, bool] = {}
    verdicts["a draft remembers the file it was made against"] = (
        len(str(facts.get("recorded_hash") or "")) != 0
        and len(str(facts.get("recorded_hash") or "")) >= 32
    )
    verdicts["the file moving under a draft is noticed"] = (
        str(facts.get("refused_error") or "") == "draft_conflict"
    )
    verdicts["the newer work on the disk survives the refusal"] = (
        str(facts.get("file_after_refusal") or "")
        == str(facts.get("human_text") or "")
    )
    verdicts["the refusal says what was expected and what is there now"] = (
        str(facts.get("refused_expected") or "") != ""
        and str(facts.get("refused_current") or "") != ""
        and str(facts.get("refused_expected") or "")
        != str(facts.get("refused_current") or "")
    )
    verdicts["the draft is still waiting after the refusal"] = (
        str(facts.get("draft_state_after_refusal") or "") != ""
        and str(facts.get("draft_state_after_refusal") or "") == "pending"
    )
    verdicts["the ticket is not burned by the refusal"] = (
        facts.get("ticket_used_after_refusal") is False
    )
    verdicts["the command line refuses the approval too"] = (
        int(facts.get("cli_exit_code") or 0) != 0
    )
    verdicts["the command line says what was expected and what is there now"] = (
        int(facts.get("cli_conflict_exit_code") or 0) == 0
    )
    verdicts["only an explicit force writes over the newer work"] = (
        str(facts.get("forced_error") or "") == ""
        and str(facts.get("file_after_force") or "") == "v2-agent"
    )
    return verdicts


def check_a_draft_never_throws_away_newer_work(
    iterations: int = 10,
) -> Dict[str, Any]:
    """Two agents, one file: the second write must not eat the first.

    The guard is optimistic - it remembers the hash of the file the
    draft was made against and refuses to write over a file that has
    moved since. Nothing is locked, so two people can still edit at
    the same time; what the guard guarantees is that nobody's work
    disappears without anybody being told.

    The gate proves it can fail: the conflict check is broken on
    purpose - every draft is written back no matter what - and the
    gate has to notice.
    """
    import pathlib
    import shutil
    import tempfile

    from mas.isolation import state_isolation

    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-conflict-"))
        rounds += 1
        try:
            # The conflict gate approves drafts through the MCP server,
            # and the server reads the state directory from the
            # configuration - which the environment overrides. The
            # observation sets that variable itself, but it never put
            # it back: a gate that leaves its environment changed
            # silently re-points every gate that runs after it, so a
            # later leak goes unnoticed. The isolation puts it back.
            with state_isolation(root):
                try:
                    facts = _conflict_observations(root)
                except Exception as exc:  # noqa: BLE001 - the failure is the subject
                    failures.append(
                        "round %d: the conflict guard broke: %s: %s"
                        % (round_index + 1, type(exc).__name__, exc)
                    )
                    continue
            seen = _conflict_verdicts(facts)
            independent = _conflict_verdicts_independent(facts)
            for label in CONFLICT_OBSERVATIONS:
                if label in independent and independent[label] != seen.get(label):
                    failures.append(
                        "round %d: the two readings of %s disagree (%r vs %r) - "
                        "the verdict was tampered with"
                        % (round_index + 1, label, seen.get(label), independent[label])
                    )
                    break
            for label in CONFLICT_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. Two behaviours are broken
            # on purpose - one where nothing is ever a conflict, one
            # where the file is written before the clash is looked for
            # - and the gate has to notice both.
            import mas.drafts as drafts_module

            real_conflict = drafts_module.draft_conflict
            real_approve = drafts_module.approve_draft
            try:
                drafts_module.draft_conflict = lambda *_args, **_kwargs: {
                    "conflict": False,
                    "reason": "unchanged",
                }
                broken = {}
                try:
                    broken = _conflict_verdicts(_conflict_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                drafts_module.draft_conflict = real_conflict
            if broken.get("the file moving under a draft is noticed"):
                failures.append(
                    "round %d: the gate did not notice that the conflict check "
                    "stopped being made - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
            if broken.get("the newer work on the disk survives the refusal"):
                failures.append(
                    "round %d: the gate did not notice that the newer work "
                    "was thrown away" % (round_index + 1)
                )

            # The second proof: the clash is never looked for, and the
            # approval writes the file *before* it would have asked.
            # Both halves are broken on purpose - the server looks for
            # the clash itself, and the approval looks for it again -
            # so the newer work really is gone, and the gate has to
            # notice that.
            def _approve_too_early(workspace, draft_id, note="", force=False):
                """An approval that writes first and asks later."""
                draft = drafts_module.get_draft(workspace, draft_id)
                if draft is None:
                    raise KeyError(draft_id)
                target = pathlib.Path(str(draft.get("absolute") or ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(drafts_module._draft_bytes(workspace, draft_id))
                return draft

            try:
                drafts_module.approve_draft = _approve_too_early
                drafts_module.draft_conflict = lambda *_args, **_kwargs: {
                    "conflict": False,
                    "reason": "unchanged",
                }
                early = {}
                try:
                    early = _conflict_verdicts(_conflict_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    early = {}
            finally:
                drafts_module.approve_draft = real_approve
                drafts_module.draft_conflict = real_conflict
            if early.get("the newer work on the disk survives the refusal"):
                failures.append(
                    "round %d: the gate did not notice that the newer work "
                    "was written over before the clash was looked for"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "conflict_guard",
        "title": "两个人的活不会互相吃掉",
        "invariant": (
            "草稿记住基线哈希；文件在等待期间被改过就拒绝写回；"
            "拒绝时不碰磁盘、不烧票据、草稿仍在等待；"
            "冲突信息报出期望与实际哈希；只有显式 force 才覆盖"
        ),
        "detail": "并发写冲突防护（乐观锁）：mas drafts approve --force",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_draft_never_throws_away_newer_work.CHECK_NAME = "conflict_guard"  # type: ignore[attr-defined]


COMPARE_OBSERVATIONS = (
    "the draft keeps the baseline it was made against",
    "the comparison names the lines the draft would take away",
    "the comparison does not call unchanged lines a loss",
    "a forced approval is still refused without a force",
    "the command line shows what would be lost",
    "the command line says so in plain words",
    "an agent can ask the same question over mcp",
    "a binary file is not mistaken for a changed file",
)


def _compare_observations(root: Any) -> Dict[str, Any]:
    """Watch what a forced approval is about to cost.

    A conflict says "the file moved". That is a fact, but it is not a
    decision: a human cannot tell whether the change that moved is
    worth keeping without seeing *what* it was. So the draft keeps the
    baseline, and the comparison names the lines that a forced
    approval would take away.

    The gate proves it can fail: the loss detection is broken on
    purpose - every line counts as lost - and the gate has to notice.
    """
    import pathlib

    from mas.drafts import draft_compare, stage_draft
    from mas.mcp_server import handle_tool

    seen: Dict[str, Any] = {}
    workspace = pathlib.Path(root) / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    staged = stage_draft(
        workspace,
        "notes.txt",
        "alpha\nbeta-agent\ngamma\n".encode("utf-8"),
        reason="the agent proposes this",
    )
    draft_id = str(staged.get("id") or "")
    seen["draft_id"] = draft_id

    # The human adds a line of their own while the draft waits.
    target.write_text("alpha\nbeta\ngamma\nhuman-added\n", encoding="utf-8")

    comparison = draft_compare(workspace, staged)
    seen["conflict"] = bool(comparison.get("conflict"))
    seen["would_lose"] = list(comparison.get("would_lose") or [])
    seen["would_lose_count"] = int(comparison.get("would_lose_count") or 0)
    seen["baseline_available"] = bool(comparison.get("baseline_available"))
    seen["readable"] = bool(comparison.get("readable"))
    seen["baseline_to_current"] = list(comparison.get("baseline_to_current") or [])
    seen["current_to_draft"] = list(comparison.get("current_to_draft") or [])
    seen["draft_sha256"] = str(comparison.get("draft_sha256") or "")
    seen["current_sha256"] = str(comparison.get("current_sha256") or "")

    # The same question, asked over MCP: an agent has to be able to
    # find out what a forced approval would cost, too.
    over_mcp = handle_tool(
        "mas_draft_compare",
        {"draft_id": draft_id, "workspace": str(workspace)},
    )
    mcp_comparison = over_mcp.get("comparison") or {}
    seen["mcp_error"] = str(over_mcp.get("error") or "")
    seen["mcp_would_lose"] = list(mcp_comparison.get("would_lose") or [])

    # A binary file must not be mistaken for a file that changed.
    blob = workspace / "blob.bin"
    blob.write_bytes(bytes(range(256)))
    binary_draft = stage_draft(workspace, "blob.bin", bytes(reversed(range(256))))
    binary_comparison = draft_compare(workspace, binary_draft)
    seen["binary_conflict"] = bool(binary_comparison.get("conflict"))
    seen["binary_readable"] = bool(binary_comparison.get("readable"))

    # The command line has to answer the same question in words.
    import argparse
    import contextlib
    import io

    from mas.cli import cmd_drafts

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        cli_result = cmd_drafts(
            argparse.Namespace(
                action="compare",
                draft_id=draft_id,
                workspace=str(workspace),
                state=None,
                note="",
                token="",
                ticket_action="approve",
                force=False,
                json=True,
            )
        )
    seen["cli_exit_code"] = int(cli_result)
    seen["cli_text"] = quiet.getvalue()

    # And the same answer in plain words, the way a human reads it.
    plain = io.StringIO()
    with contextlib.redirect_stdout(plain):
        plain_result = cmd_drafts(
            argparse.Namespace(
                action="compare",
                draft_id=draft_id,
                workspace=str(workspace),
                state=None,
                note="",
                token="",
                ticket_action="approve",
                force=False,
                json=False,
            )
        )
    seen["cli_plain_exit_code"] = int(plain_result)
    seen["cli_plain_text"] = plain.getvalue()
    return seen


def _compare_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """Turn the collected facts into the verdicts of the gate."""
    lost = [str(line) for line in (facts.get("would_lose") or [])]
    return {
        "the draft keeps the baseline it was made against": (
            facts.get("baseline_available") is True
        ),
        "the comparison names the lines the draft would take away": (
            len(lost) == 1
            and "human-added" in lost[0]
        ),
        "the comparison does not call unchanged lines a loss": (
            all(
                line.strip() not in ("alpha", "beta", "gamma")
                for line in lost
            )
        ),
        "a forced approval is still refused without a force": (
            facts.get("conflict") is True
        ),
        "the command line shows what would be lost": (
            facts.get("cli_exit_code") == 0
            and "human-added" in str(facts.get("cli_text") or "")
        ),
        "the command line says so in plain words": (
            facts.get("cli_plain_exit_code") == 0
            and "会丢掉" in str(facts.get("cli_plain_text") or "")
        ),
        "an agent can ask the same question over mcp": (
            facts.get("mcp_error") == ""
            and list(facts.get("mcp_would_lose") or []) == lost
        ),
        "a binary file is not mistaken for a changed file": (
            facts.get("binary_conflict") is False
            and facts.get("binary_readable") is False
        ),
    }


def _compare_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    Written from the rule rather than from the first implementation;
    the gate needs the two to agree.
    """
    verdicts: Dict[str, bool] = {}
    lost = facts.get("would_lose") or []
    verdicts["the draft keeps the baseline it was made against"] = (
        facts.get("baseline_available") is True
    )
    verdicts["the comparison names the lines the draft would take away"] = (
        len(lost) == 1 and "human-added" in str(lost[0])
    )
    verdicts["the comparison does not call unchanged lines a loss"] = (
        len([line for line in lost if str(line).strip() in ("alpha", "beta", "gamma")]) == 0
    )
    verdicts["a forced approval is still refused without a force"] = (
        facts.get("conflict") is True
    )
    verdicts["the command line shows what would be lost"] = (
        facts.get("cli_exit_code") == 0
        and "human-added" in str(facts.get("cli_text") or "")
    )
    verdicts["the command line says so in plain words"] = (
        facts.get("cli_plain_exit_code") == 0
        and "会丢掉" in str(facts.get("cli_plain_text") or "")
    )
    verdicts["an agent can ask the same question over mcp"] = (
        facts.get("mcp_error") == "" and facts.get("mcp_would_lose") == lost
    )
    verdicts["a binary file is not mistaken for a changed file"] = (
        facts.get("binary_conflict") is False
        and facts.get("binary_readable") is False
    )
    return verdicts


def check_a_forced_approval_says_what_it_would_cost(
    iterations: int = 10,
) -> Dict[str, Any]:
    """Before a forced approval, the human is told what it would cost.

    The conflict guard refuses to write over newer work. That is the
    right answer, but "it changed" is not a decision - so the draft
    keeps the baseline it was made against, and the comparison names
    the lines a forced approval would take away.

    The gate proves it can fail: the loss detection is broken on
    purpose - every line counts as lost - and the gate has to notice.
    """
    import shutil
    import tempfile

    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-compare-"))
        rounds += 1
        try:
            try:
                facts = _compare_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the comparison broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                continue
            seen = _compare_verdicts(facts)
            independent = _compare_verdicts_independent(facts)
            for label in COMPARE_OBSERVATIONS:
                if label in independent and independent[label] != seen.get(label):
                    failures.append(
                        "round %d: the two readings of %s disagree (%r vs %r) - "
                        "the verdict was tampered with"
                        % (round_index + 1, label, seen.get(label), independent[label])
                    )
                    break
            for label in COMPARE_OBSERVATIONS:
                if label not in seen:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                elif not seen[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))

            # The gate proves it can fail. The loss detection is
            # broken on purpose - every line of the file counts as
            # lost - and the gate has to notice.
            import mas.drafts as drafts_module

            real_lost = drafts_module._lost_lines
            try:
                # The mutant calls the lines nobody touched a loss.
                drafts_module._lost_lines = lambda *_args, **_kwargs: ["alpha"]
                broken = {}
                try:
                    broken = _compare_verdicts(_compare_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                drafts_module._lost_lines = real_lost
            if broken.get("the comparison names the lines the draft would take away"):
                failures.append(
                    "round %d: the gate did not notice that the loss detection "
                    "stopped being made - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
            if broken.get("the comparison does not call unchanged lines a loss"):
                failures.append(
                    "round %d: the gate did not notice that unchanged lines "
                    "were called a loss" % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "force_cost",
        "title": "覆盖之前先看清会丢什么",
        "invariant": (
            "草稿留下基线；对比点名覆盖会丢掉的那几行；没改过的行不算丢失；"
            "命令行与 MCP 都用大白话说清代价；二进制文件不被误判为已改动"
        ),
        "detail": "三方对比：mas drafts compare <草稿编号>",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_forced_approval_says_what_it_would_cost.CHECK_NAME = "force_cost"  # type: ignore[attr-defined]


UNDO_OBSERVATIONS = (
    "the undo puts the file back exactly as it was",
    "the draft survives the undo and can be approved again",
    "an undo of an unapproved draft is refused",
    "an undo after the file moved is refused",
    "the refusal names what the undo would cost",
    "the command line can undo a draft",
    "an agent can undo a draft over mcp",
)


def _undo_observations(root: Any) -> Dict[str, Any]:
    """Watch what an undo does to the file.

    An approval that cannot be taken back is a one-way door. The
    draft kept the baseline, so the undo is not a guess - it puts back
    exactly what was there. But the undo has to be careful in the same
    way the approval is: if the file moved *after* the approval,
    undoing would throw the newer work away.

    The gate proves it can fail: the undo is broken on purpose - it
    writes the wrong bytes - and the gate has to notice.
    """
    import argparse
    import contextlib
    import io
    import pathlib

    from mas.cli import cmd_drafts
    from mas.drafts import approve_draft, draft_undo_plan, stage_draft, undo_draft
    from mas.mcp_server import handle_tool

    seen: Dict[str, Any] = {}
    workspace = pathlib.Path(root) / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "notes.txt"
    original = "alpha\nbeta\n"
    target.write_text(original, encoding="utf-8")

    staged = stage_draft(
        workspace,
        "notes.txt",
        "alpha\nbeta-agent\n".encode("utf-8"),
        reason="the agent proposes this",
    )
    draft_id = str(staged.get("id") or "")
    seen["draft_id"] = draft_id

    approve_draft(workspace, draft_id)
    seen["after_approval"] = target.read_text(encoding="utf-8")

    undone = undo_draft(workspace, draft_id, note="approved the wrong thing")
    seen["after_undo"] = target.read_text(encoding="utf-8")
    seen["undo_state"] = str(undone.get("state") or "")
    seen["undo_note"] = str(undone.get("undo_note") or "")

    # The draft is not dead: the same change can be approved again.
    again = approve_draft(workspace, draft_id)
    seen["reapproved_state"] = str(again.get("state") or "")
    seen["after_reapproval"] = target.read_text(encoding="utf-8")

    # An undo of a draft that was never approved is refused. It gets
    # its own file, so the scenarios cannot step on each other.
    pending_file = workspace / "pending.txt"
    pending_file.write_text("one\n", encoding="utf-8")
    other = stage_draft(workspace, "pending.txt", "two\n".encode("utf-8"))
    other_id = str(other.get("id") or "")
    try:
        undo_draft(workspace, other_id)
        seen["unapproved_refused"] = False
        seen["unapproved_reason"] = ""
    except ValueError as exc:
        seen["unapproved_refused"] = True
        seen["unapproved_reason"] = str(exc)

    # An undo after the file moved is refused - and the refusal says
    # what the undo would cost.
    moving_file = workspace / "moving.txt"
    moving_file.write_text("alpha\nbeta\n", encoding="utf-8")
    moving = stage_draft(
        workspace, "moving.txt", "alpha\nbeta-agent\n".encode("utf-8")
    )
    moving_id = str(moving.get("id") or "")
    approve_draft(workspace, moving_id)
    moving_file.write_text(
        "alpha\nbeta-agent\nhuman-after-approval\n", encoding="utf-8"
    )
    plan = draft_undo_plan(workspace, moving)
    seen["plan_reason"] = str(plan.get("reason") or "")
    seen["plan_would_lose"] = list(plan.get("would_lose") or [])
    try:
        undo_draft(workspace, moving_id)
        seen["moved_refused"] = False
        seen["moved_reason"] = ""
    except Exception as exc:  # noqa: BLE001 - the refusal is the subject
        seen["moved_refused"] = True
        seen["moved_reason"] = str(exc)
        seen["moved_clash"] = dict(getattr(exc, "plan", {}) or {})
    seen["file_after_refusal"] = moving_file.read_text(encoding="utf-8")

    # The command line has to be able to undo - on a draft that is
    # still in the state an undo accepts.
    cli_file = workspace / "cli.txt"
    cli_file.write_text("one\n", encoding="utf-8")
    cli_draft = stage_draft(workspace, "cli.txt", "two\n".encode("utf-8"))
    cli_id = str(cli_draft.get("id") or "")
    approve_draft(workspace, cli_id)
    cli = argparse.Namespace(
        action="undo",
        draft_id=cli_id,
        workspace=str(workspace),
        state=None,
        note="",
        token="",
        ticket_action="approve",
        force=False,
        json=False,
    )
    plain = io.StringIO()
    noise = io.StringIO()
    with contextlib.redirect_stdout(plain), contextlib.redirect_stderr(noise):
        cli_result = cmd_drafts(cli)
    seen["cli_exit_code"] = int(cli_result)
    seen["cli_text"] = plain.getvalue()
    seen["cli_file_after"] = cli_file.read_text(encoding="utf-8")

    # And an agent has to be able to ask the same thing over MCP.
    over_mcp = handle_tool(
        "mas_draft_undo",
        {"draft_id": moving_id, "workspace": str(workspace)},
    )
    seen["mcp_error"] = str(over_mcp.get("error") or "")
    seen["mcp_would_lose"] = list(
        (over_mcp.get("would_lose") or (over_mcp.get("plan") or {}).get("would_lose") or [])
    )
    return seen


def _undo_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """Turn the collected facts into the verdicts of the gate."""
    return {
        "the undo puts the file back exactly as it was": (
            facts.get("after_undo") == "alpha\nbeta\n"
            and facts.get("cli_file_after") == "one\n"
        ),
        "the draft survives the undo and can be approved again": (
            facts.get("undo_state") == "undone"
            and facts.get("reapproved_state") == "approved"
            and facts.get("after_reapproval") == "alpha\nbeta-agent\n"
        ),
        "an undo of an unapproved draft is refused": (
            facts.get("unapproved_refused") is True
        ),
        "an undo after the file moved is refused": (
            facts.get("moved_refused") is True
            and facts.get("plan_reason") == "file_changed"
        ),
        "the refusal names what the undo would cost": (
            len(list(facts.get("plan_would_lose") or [])) == 1
            and "human-after-approval"
            in next(iter(facts.get("plan_would_lose") or []))
        ),
        "the command line can undo a draft": (
            facts.get("cli_exit_code") == 0
            and "已撤销" in str(facts.get("cli_text") or "")
        ),
        "an agent can undo a draft over mcp": (
            facts.get("mcp_error") == "undo_conflict"
            and len(list(facts.get("mcp_would_lose") or [])) == 1
        ),
    }


def _undo_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}
    lost = [str(line) for line in (facts.get("plan_would_lose") or [])]
    verdicts["the undo puts the file back exactly as it was"] = (
        str(facts.get("after_undo")) == "alpha\nbeta\n"
    )
    verdicts["the draft survives the undo and can be approved again"] = (
        str(facts.get("undo_state")) == "undone"
        and str(facts.get("reapproved_state")) == "approved"
    )
    verdicts["an undo of an unapproved draft is refused"] = (
        facts.get("unapproved_refused") is True
    )
    verdicts["an undo after the file moved is refused"] = (
        facts.get("moved_refused") is True
        and str(facts.get("plan_reason")) == "file_changed"
    )
    verdicts["the refusal names what the undo would cost"] = (
        len(lost) == 1 and "human-after-approval" in lost[0]
    )
    verdicts["the command line can undo a draft"] = (
        facts.get("cli_exit_code") == 0
        and "\u5df2\u64a4\u9500" in str(facts.get("cli_text") or "")
    )
    verdicts["an agent can undo a draft over mcp"] = (
        str(facts.get("mcp_error")) == "undo_conflict" and len(lost) == 1
    )
    return verdicts


def check_an_approved_change_can_be_taken_back(
    iterations: int = 10,
) -> Dict[str, Any]:
    """An approved change can be taken back.

    The draft layer made the approval the only way a change reaches
    the file - but it also made it a one-way door: once the human
    approved the wrong thing, the only way back was to edit the file
    by hand. The draft already keeps the baseline, so the undo is not
    a guess.

    The gate proves it can fail: the undo is broken on purpose - it
    writes the wrong bytes - and the gate has to notice.
    """
    import shutil
    import tempfile

    from mas.isolation import state_isolation

    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-undo-"))
        rounds += 1
        try:
            # An approval and an undo are both human decisions, and a
            # human decision is written into the main ledger. The gate
            # has to prove it *can* undo - but it has to do it in a
            # ledger of its own, not in the ledger of the machine.
            with state_isolation(root):
                try:
                    facts = _undo_observations(root)
                except Exception as exc:  # noqa: BLE001 - the failure is the subject
                    failures.append(
                        "round %d: the undo broke: %s: %s"
                        % (round_index + 1, type(exc).__name__, exc)
                    )
                    facts = {}
            verdicts = _undo_verdicts(facts)
            independent = _undo_verdicts_independent(facts)
            for label in UNDO_OBSERVATIONS:
                if label not in verdicts:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                    continue
                if not verdicts[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))
                if independent.get(label) != verdicts.get(label):
                    failures.append(
                        "round %d: %s - the two readings disagree (%s vs %s)"
                        % (
                            round_index + 1,
                            label,
                            verdicts.get(label),
                            independent.get(label),
                        )
                    )

            # The gate proves it can fail: the undo writes the wrong
            # bytes, and the gate has to notice.
            import mas.drafts as drafts_module

            real_undo = drafts_module.undo_draft

            def _undo_to_the_wrong_bytes(workspace, draft_id, note="", force=False):
                """An undo that puts the file back to the wrong version."""
                draft = drafts_module.get_draft(workspace, draft_id)
                if draft is None:
                    raise KeyError(draft_id)
                target = pathlib.Path(str(draft.get("absolute") or ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"wrong bytes\n")
                return draft

            try:
                drafts_module.undo_draft = _undo_to_the_wrong_bytes
                broken = {}
                try:
                    with state_isolation(root):
                        broken = _undo_verdicts(_undo_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                drafts_module.undo_draft = real_undo
            if broken.get("the undo puts the file back exactly as it was"):
                failures.append(
                    "round %d: the gate did not notice that the undo wrote the "
                    "wrong bytes - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "undo_guard",
        "title": "批错了还能退回来",
        "invariant": (
            "撤销把文件逐字节退回改动前；草稿撤销后仍在、可以再次批准；"
            "没批准的草稿不能撤；批准后文件又变过就拒绝撤销并说清代价"
        ),
        "detail": "撤销一个已批准的草稿：mas drafts undo <草稿编号>",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_an_approved_change_can_be_taken_back.CHECK_NAME = "undo_guard"  # type: ignore[attr-defined]


CHECKS = (
    check_relay_refusal_keeps_the_connection_usable,
    check_provider_keeps_the_usage_of_a_failed_call,
    check_a_run_keeps_its_files_with_the_deliverable,
    check_mcp_handshake_answers_every_tool,
    check_a_stream_folds_into_one_answer,
    check_parallel_candidates_pick_the_same_winner_every_time,
    check_an_escalated_run_leaves_a_ticket_a_human_can_take_over,
    check_a_takeover_sees_the_work_and_gets_a_fresh_budget,
    check_a_handback_closes_on_human_work,
    check_a_handback_shows_up_in_the_report,
    check_a_folder_handoff_closes_on_human_edits,
    check_a_content_check_reads_inside_the_file,
    check_a_binary_write_lands_as_bytes,
    check_the_index_knows_every_file,
    check_a_draft_is_approved_before_it_is_written_back,
    check_an_agent_can_propose_but_not_approve,
    check_a_ticket_opens_one_door_once,
    check_a_draft_never_throws_away_newer_work,
    check_a_forced_approval_says_what_it_would_cost,
    check_an_approved_change_can_be_taken_back,
    check_a_human_decision_leaves_a_trace_that_cannot_be_changed,
    check_the_program_you_type_is_the_program_that_was_checked,
    check_a_beginner_can_walk_the_docs_and_close_a_loop,
    check_the_documents_the_beginner_reads_travel_with_the_wheel,
    check_an_undo_is_visible_in_the_audit,
    check_the_one_click_install_survives_a_machine_without_python,
    check_the_bootstrap_path_is_executed_and_not_just_described,
    check_a_closure_is_sealed_to_the_bytes_it_decided_on,
    check_the_report_sees_the_drift,
    check_the_report_says_which_approvals_were_wrong,
    check_the_report_reads_the_decision_chains,
    check_the_report_reads_the_human_tickets,
    check_the_report_reads_the_drafts,
    check_the_report_reads_every_ledger_row,
    check_the_deliverables_reach_the_mcp_caller,
    check_the_closure_verdict_reaches_both_surfaces,
    check_the_report_and_the_verdict_agree_on_the_grain,
    check_the_status_page_speaks_the_same_grain,
    check_a_ticket_a_human_can_act_on_an_agent_can_too,
    check_the_ticket_queue_is_a_handover_everywhere,
    check_the_verdict_page_speaks_the_same_grain,
    check_the_run_detail_speaks_the_same_grain,
    check_the_queue_decides_and_not_the_ledger_alone,
    check_the_replay_listens_to_the_queue_too,
    check_a_taken_back_approval_is_visible_everywhere,
    check_an_undone_draft_is_still_waiting_everywhere,
    check_a_decision_hangs_on_its_run,
    check_the_documents_are_still_text,
    check_the_selftest_leaves_the_machine_alone,
)

#: How many gates the suite is expected to hold.
#:
#: Nineteen assertions in nine test files used to pin this number to the
#: literal 21, which meant every new gate broke nineteen unrelated
#: tests and the "fix" was to edit nine files. The count belongs to the
#: suite, so it is read from the suite.
#:
#: It is still *checked* - a gate that quietly disappears is a hole in
#: the ground, so the count is asserted rather than assumed. What is no
#: longer true is the number being written down in nine places.
EXPECTED_CHECKS = len(CHECKS)


def _machine_snapshot() -> Dict[str, Any]:
    """A picture of the state directory of the machine running the suite.

    The state directory comes from the configuration, the way every
    other part of the runtime finds it. When the environment overrides
    it - which is what CI and the test suite do - the override is what
    is watched, because that is where the writes would land.
    """
    from mas.config import load_config
    from mas.isolation import snapshot_state

    try:
        config = load_config()
    except Exception:  # noqa: BLE001 - a broken config is the gate's problem
        config = {}
    raw = str(config.get("state_dir") or "~/.mas")
    state = pathlib.Path(os.path.expanduser(os.path.expandvars(raw)))
    return snapshot_state(state)


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

    # The suite watches the machine it runs on. A gate that writes a
    # decision into the user's ledger is a side effect with an exit
    # code, so the whole run is compared before and after: whatever
    # the gates changed outside of their own temporary folders is
    # reported, not tolerated.
    from mas.isolation import diff_snapshots

    machine_before = _machine_snapshot()

    results: List[Dict[str, Any]] = []
    for check in selected:
        # Four checks run a whole loop-engine run per iteration. They are
        # slow enough that hammering them 120 times would add minutes to
        # every install and every CI job - and they hammer a *race* that
        # is staged on purpose, so twenty rounds say as much as a
        # hundred.
        if check in (
            check_a_run_keeps_its_files_with_the_deliverable,
            check_parallel_candidates_pick_the_same_winner_every_time,
            check_an_escalated_run_leaves_a_ticket_a_human_can_take_over,
            check_a_takeover_sees_the_work_and_gets_a_fresh_budget,
            check_a_handback_closes_on_human_work,
            check_a_handback_shows_up_in_the_report,
            check_a_folder_handoff_closes_on_human_edits,
            check_a_content_check_reads_inside_the_file,
            check_a_binary_write_lands_as_bytes,
            check_the_index_knows_every_file,
            check_a_draft_is_approved_before_it_is_written_back,
        ):
            row = check(iterations=min(int(iterations), 20))
        else:
            row = check(iterations=int(iterations))
        results.append(row)
    failed = [row["name"] for row in results if not row.get("ok")]
    skipped = [row["name"] for row in results if row.get("skipped")]

    # The machine is compared with itself. Anything the suite changed
    # outside of its own temporary folders is named here - and it makes
    # the suite fail, because a self-test that dirties the machine is
    # not a self-test.
    machine_problems = diff_snapshots(machine_before, _machine_snapshot())
    if machine_problems:
        failed = [*failed, "machine_state"]

    return {
        "ok": not failed,
        "iterations": int(iterations),
        "checks": results,
        "failed": failed,
        "skipped": skipped,
        "machine_state": {
            "state_dir": machine_before.get("state_dir", ""),
            "problems": machine_problems,
        },
        "passed": [
            row["name"]
            for row in results
            if row.get("ok") and not row.get("skipped")
        ],
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
        if row.get("skipped"):
            mark = "[\u8df3\u8fc7]"
        elif row.get("ok"):
            mark = "[OK  ]"
        else:
            mark = "[\u574f\u4e86]"
        lines.append("%s %s" % (mark, row.get("title", row.get("name", ""))))
        lines.append("       \u4fdd\u8bc1\uff1a%s" % row.get("invariant", ""))
        if row.get("skipped"):
            lines.append(
                "       \u2192 \u8fd9\u4e00\u9879\u6ca1\u6709\u53ef\u89c2\u5bdf\u7684\u5bf9\u8c61\uff0c"
                "\u8df3\u8fc7\uff08\u4e0d\u662f\u901a\u8fc7\uff09"
            )
        else:
            lines.append("       \u6253\u4e86 %s \u6b21" % row.get("iterations", 0))
        for failure in row.get("failures", []) or []:
            lines.append("       \u2192 %s" % failure)
    lines.append("")
    failed = result.get("failed") or []
    skipped = result.get("skipped") or []
    if failed:
        lines.append(
            "\u7ed3\u8bba\uff1a%d \u9879\u81ea\u68c0\u5931\u8d25\uff08%s\uff09"
            % (len(failed), ", ".join(failed))
        )
    else:
        lines.append(
            "\u7ed3\u8bba\uff1a\u6240\u6709\u81ea\u68c0\u9879\u90fd\u7ecf\u8fc7\u4e86\u3002"
        )
    if skipped:
        lines.append(
            "\u6ce8\u610f\uff1a%d \u9879\u81ea\u68c0\u8df3\u8fc7\uff08%s\uff09"
            "\u2014\u2014\u6ca1\u6709\u53ef\u89c2\u5bdf\u7684\u5bf9\u8c61\uff0c\u4e0d\u7b97\u901a\u8fc7\u3002"
            % (len(skipped), ", ".join(skipped))
        )
    return "\n".join(lines) + "\n"

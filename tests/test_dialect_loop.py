"""End-to-end dialect coverage: a whole loop over a non-OpenAI wire format.

The provider unit tests in ``test_provider_dialects.py`` prove that a single
HTTP round trip is understood. This module closes the loop: a full
``LoopEngine`` run - planner, worker, verifier, ledger - is executed against
a fake gateway that speaks a different dialect, so the whole stack
(prompt building, JSON extraction, machine checks, ledger accounting) has
to survive the wire format of that gateway.

Two shapes are covered:

* an OpenAI-style gateway that answers a chat request with an SSE stream;
* an Anthropic-style gateway (``/v1/messages``, content blocks,
  ``input_tokens`` / ``output_tokens``).

The fake gateway produces role-appropriate answers by looking at the
``<<MAS_STATE>>`` block of the prompt, which is exactly the contract the
real providers rely on.
"""
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
import pathlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.providers import AnthropicProvider, OpenAICompatProvider

ARTIFACT = "关键结论：这是一份通过机检的交付物，长度足够。"


def _state_from_prompt(prompt: str) -> Dict[str, Any]:
    marker = "<<MAS_STATE>>"
    if marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1]
    end = tail.find("<<END>>")
    if end == -1:
        return {}
    try:
        data = json.loads(tail[:end].strip())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _role_answer(prompt: str, system: str = "") -> str:
    """Answer like a well-behaved model, for whatever role is asked.

    The role lives in the *system* prompt; the criteria live in the task
    state. A real endpoint only sees the rendered text, so the fake gateway
    is given exactly the same two strings the provider would send.
    """
    state = _state_from_prompt(prompt)
    criteria = state.get("criteria") or []
    scores = [
        {"id": criterion.get("id"), "pass": True, "note": "criterion satisfied"}
        for criterion in criteria
        if isinstance(criterion, dict)
    ]
    lowered = (system + "\n" + prompt).lower()
    if "independent verifier" in lowered:
        return json.dumps({"verdict": "pass", "scores": scores, "issues": []})
    if "planner" in lowered:
        return json.dumps({"summary": "s", "plan": ["p"], "risks": []})
    if "lead agent" in lowered:
        return json.dumps({"decision": "close", "reason": "ok"})
    if "reflector" in lowered:
        return json.dumps({"summary": "r", "issues": [], "next_action": "revise"})
    return ARTIFACT


class _GatewayServer:
    """Fake gateway that speaks one dialect."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.requests: List[Dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw) if raw else {}
                outer.requests.append(payload)
                body, status, ctype = outer.respond(self.path, payload)
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

    def __enter__(self) -> "_GatewayServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def respond(self, path: str, payload: Dict[str, Any]):
        if self.dialect == "anthropic":
            prompt = ""
            for message in payload.get("messages") or []:
                content = message.get("content")
                if isinstance(content, str):
                    prompt = content
            answer = _role_answer(prompt, str(payload.get("system") or ""))
            return json.dumps({
                "id": "msg_x", "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": answer}],
                "usage": {"input_tokens": 120, "output_tokens": 40},
            }), 200, "application/json"

        prompt = ""
        system = ""
        for message in payload.get("messages") or []:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if message.get("role") == "user":
                prompt = content
            elif message.get("role") == "system":
                system = content
        # A Responses-style bridge reads ``input`` and ignores ``messages``.
        # The system instruction has to be picked up from either shape,
        # otherwise the role contract is lost and the loop closes on a
        # guess instead of on a verdict.
        if isinstance(payload.get("input"), list):
            for item in payload["input"]:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                if item.get("role") == "user" and not prompt:
                    prompt = content
                elif item.get("role") == "system" and not system:
                    system = content
        if not prompt:
            prompt = str(payload.get("input") or "")
        answer = _role_answer(prompt, system)
        chunks = [
            {"choices": [{"delta": {"content": answer[: len(answer) // 2]}}]},
            {"choices": [{"delta": {"content": answer[len(answer) // 2:]}},
                         {"finish_reason": "stop"}],
             "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}},
        ]
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return body, 200, "text/event-stream"


def _make_config(tmp: str) -> Dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"]["max_iterations"] = 3
    return config


def _recorded_tokens(rows: List[Dict[str, Any]]) -> int:
    """Sum every token counter that was written into the ledger."""
    total = 0
    for row in rows:
        def walk(node: Any) -> None:
            nonlocal total
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("prompt_tokens", "completion_tokens", "total_tokens") and isinstance(value, int):
                        total += value
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(row)
    return total


class DialectLoopTest(unittest.TestCase):
    def _run_loop(self, provider):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LoopEngine(_make_config(tmp), provider=provider)
            criteria = [
                {"id": "c1", "desc": "包含关键结论", "check": {"type": "contains", "arg": "关键结论"}},
                {"id": "c2", "desc": "长度达标", "check": {"type": "min_chars", "arg": 10}},
            ]
            return engine.run("写一份交付物", criteria)

    def test_full_loop_over_sse_openai_dialect(self):
        with _GatewayServer("sse") as server:
            provider = OpenAICompatProvider(model="m", base_url=server.base_url)
            result = self._run_loop(provider)
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.iterations, 1)
        self.assertGreater(result.tokens_used, 0)
        self.assertTrue(all(s["pass"] for s in result.scores))

    def test_full_loop_over_anthropic_dialect(self):
        with _GatewayServer("anthropic") as server:
            provider = AnthropicProvider(model="claude-x", base_url=f"http://127.0.0.1:{server.port}")
            result = self._run_loop(provider)
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.iterations, 1)
        self.assertGreater(result.tokens_used, 0)

    def test_ledger_records_tokens_for_non_openai_dialect(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _GatewayServer("anthropic") as server:
                provider = AnthropicProvider(model="claude-x", base_url=f"http://127.0.0.1:{server.port}")
                engine = LoopEngine(_make_config(tmp), provider=provider)
                result = engine.run(
                    "写一份交付物",
                    [{"id": "c1", "desc": "包含关键结论", "check": {"type": "contains", "arg": "关键结论"}}],
                )
            self.assertEqual(result.status, "closed")
            rows = [
                json.loads(line)
                for line in pathlib.Path(engine.ledger_path(engine.config)).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(rows)
            recorded = _recorded_tokens(rows)
            self.assertTrue(recorded > 0, rows)
            # The Anthropic dialect reports input_tokens/output_tokens; the
            # normalised counters must show up in the ledger as well.
            self.assertGreaterEqual(recorded, 160)

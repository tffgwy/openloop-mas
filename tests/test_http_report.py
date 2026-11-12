"""Tests for the HTTP + SSE transport and the v0.2 reporting layer."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from mas.http_server import MASRequestHandler, _QuietThreadingHTTPServer
from mas.report import build_report, ledger_markdown, replay_trace, render_markdown


class TestHTTPTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mas-http-test-")
        os.environ["MAS_STATE_DIR"] = cls.tmp
        cls.server = _QuietThreadingHTTPServer(("127.0.0.1", 0), MASRequestHandler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_health(self):
        status, payload = self._get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "openloop-mas")

    def test_tools_catalogue(self):
        status, payload = self._get("/tools")
        self.assertEqual(status, 200)
        names = [t["name"] for t in payload["tools"]]
        for expected in ("mas_run", "mas_status", "mas_report", "mas_replay", "mas_doctor"):
            self.assertIn(expected, names)

    def test_call_runs_a_real_loop(self):
        body = json.dumps(
            {
                "tool": "mas_run",
                "arguments": {
                    "goal": "输出一份结论",
                    "criteria": [
                        {"id": "c1", "desc": "包含版本号", "check": {"type": "contains", "arg": "v0.1.0"}},
                        {"id": "c2", "desc": "不少于200字", "check": {"type": "min_chars", "arg": 200}},
                    ],
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/call", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=40) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(payload["result"]["status"], "closed")
        self.assertTrue(payload["result"]["closed"])

    def test_unknown_route_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/nope", timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_missing_tool_name_is_400(self):
        body = json.dumps({"arguments": {}}).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/call", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_sse_streams_ledger_events(self):
        request = urllib.request.Request(self.base + "/events?interval=0.3&max_events=2")
        events = []
        start = time.time()
        with urllib.request.urlopen(request, timeout=15) as resp:
            buffer = ""
            while time.time() - start < 12 and len(events) < 2:
                chunk = resp.read(1)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    if "event: ledger" in block and "data: " in block:
                        events.append(json.loads(block.split("data: ", 1)[1]))
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("role", events[0])


class TestReportLayer(unittest.TestCase):
    def _rows(self):
        return [
            {
                "seq": 1, "ts": "2026-01-01T00:00:00+00:00", "run_id": "R-1", "iteration": 1,
                "role": "planner", "plan": {"summary": "拆分目标", "plan": ["第一步"]},
            },
            {
                "seq": 2, "ts": "2026-01-01T00:00:01+00:00", "run_id": "R-1", "iteration": 1,
                "role": "closure", "verdict": "fail", "decision": "revise", "closed": False,
                "artifact_sha": "aaaa", "issues": ["c1: 长度不足"],
                "scores": [{"id": "c1", "pass": False, "note": "长度不足"}],
            },
            {
                "seq": 3, "ts": "2026-01-01T00:00:02+00:00", "run_id": "R-1", "iteration": 2,
                "role": "closure", "verdict": "pass", "decision": "close", "closed": True,
                "artifact_sha": "bbbb", "issues": [],
                "scores": [{"id": "c1", "pass": True, "note": "ok"}],
            },
        ]

    def test_report_counts_closure_rate(self):
        report = build_report(self._rows())
        self.assertEqual(report["totals"]["runs"], 1)
        self.assertEqual(report["totals"]["closed"], 1)
        self.assertEqual(report["totals"]["closure_rate"], 1.0)

    def test_report_status_is_closed_not_the_verdict(self):
        report = build_report(self._rows())
        run = report["runs"][0]
        self.assertEqual(run["status"], "closed")
        self.assertEqual(run["verdict"], "pass")
        self.assertEqual(run["decision"], "close")

    def test_report_does_not_carry_stale_issues(self):
        report = build_report(self._rows())
        self.assertEqual(report["runs"][0]["issues"], [])

    def test_markdown_contains_the_closure_row(self):
        markdown = render_markdown(build_report(self._rows()))
        self.assertIn("R-1", markdown)
        self.assertIn("closed", markdown)
        self.assertIn("PASS", markdown)

    def test_replay_trace_is_ordered(self):
        trace = replay_trace(self._rows())
        self.assertEqual(trace["steps_count"], 3)
        self.assertEqual([s["seq"] for s in trace["steps"]], [1, 2, 3])
        self.assertEqual(trace["artifact_chain"], ["aaaa", "bbbb"])
        self.assertTrue(trace["terminal"]["closed"])

    def test_ledger_markdown_is_a_table(self):
        markdown = ledger_markdown(self._rows())
        self.assertIn("| # | 时间 |", markdown)
        self.assertIn("planner", markdown)


if __name__ == "__main__":
    unittest.main()

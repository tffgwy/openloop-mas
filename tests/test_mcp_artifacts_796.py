# -*- coding: utf-8 -*-
"""The deliverable list has to reach the MCP caller (v0.7.96).

``mas artifacts`` answered "what did I actually get?" - but only on the
terminal. An agent holding the MCP tools could run a loop, read the
report, replay the ledger, audit the decisions and list every file it
wrote, and still could not find the deliverable.

These tests hold the fix in place:

* the tool is advertised and it answers;
* the answer is data, not prose;
* the CLI and the MCP give the *same* answer - one implementation, so
  the two surfaces cannot drift apart;
* a deliverable that was deleted afterwards is still listed and flagged;
* and the gate that watches all of this can itself fail.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import os
import shutil
import tempfile
import unittest

from mas.mcp_server import TOOLS
from mas.report import artifact_rows, format_artifacts
from mas.selftest_mcp_artifacts import (
    check_the_deliverables_reach_the_mcp_caller,
)


def _tool_names():
    return [str(tool.get("name") or "") for tool in TOOLS]


class _FakeLedger:
    def __init__(self, rows):
        self._rows = list(rows)

    def entries(self):
        return list(self._rows)


class _FakeEngine:
    def __init__(self, rows):
        self.ledger = _FakeLedger(rows)


class DeliverablesReachTheMcpCallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mas-test-artifacts-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.deliverable = os.path.join(self.tmp, "out.md")
        with open(self.deliverable, "w", encoding="utf-8") as handle:
            handle.write("# 交付物\n\n这是一份交付物。\n")
        self.rows = [
            {
                "role": "closure",
                "run_id": "R-testart",
                "task_id": "T-testart",
                "iteration": 1,
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": 1,
                "verdict": "pass",
                "decision": "accept",
                "scores": [{"id": "c1", "pass": True}],
            },
            {
                "role": "run_end",
                "run_id": "R-testart",
                "task_id": "T-testart",
                "iteration": 1,
                "ts": "2026-01-01T00:00:01+00:00",
                "seq": 2,
                "status": "closed",
                "decision": "accept",
                "artifact_path": self.deliverable,
                "artifact_chars": 24,
                "workspace": self.tmp,
                "title": "写一份结论",
                "goal": "写一份结论",
                "files": [{"path": "out.md", "bytes": 24}],
            },
        ]

    def _call(self, rows=None):
        import mas.mcp_server as mcp_module

        real_engine = mcp_module._engine
        rows = self.rows if rows is None else rows

        def _fake(_config, team=None):
            return _FakeEngine(rows)

        try:
            mcp_module._engine = _fake
            return mcp_module.handle_tool("mas_artifacts", {})
        finally:
            mcp_module._engine = real_engine

    def test_the_tool_is_advertised(self):
        self.assertIn("mas_artifacts", _tool_names())

    def test_the_tool_returns_the_deliverable(self):
        payload = self._call()
        artifacts = payload["artifacts"]
        self.assertEqual(len(artifacts), 1)
        item = artifacts[0]
        self.assertEqual(item["run_id"], "R-testart")
        self.assertTrue(item["path"].endswith("out.md"))
        self.assertEqual(item["title"], "写一份结论")
        self.assertEqual(item["goal"], "写一份结论")
        self.assertEqual(item["status"], "closed")
        self.assertEqual(item["chars"], 24)
        self.assertTrue(item["exists"])

    def test_the_caller_gets_data_not_markdown(self):
        payload = self._call()
        self.assertIsInstance(payload["artifacts"], list)
        self.assertIsInstance(payload["artifacts"][0], dict)
        self.assertIn("path", payload["artifacts"][0])
        self.assertIn("run_id", payload["artifacts"][0])
        # The prose is there too, for a human reading the answer.
        self.assertIn("out.md", payload["reading"])

    def test_the_mcp_and_the_cli_give_the_same_answer(self):
        payload = self._call()
        self.assertEqual(payload["artifacts"], artifact_rows(self.rows))

    def test_a_deleted_deliverable_is_still_listed_and_flagged(self):
        os.remove(self.deliverable)
        payload = self._call()
        artifacts = payload["artifacts"]
        self.assertEqual(len(artifacts), 1)
        self.assertFalse(artifacts[0]["exists"])
        self.assertEqual(payload["missing"], 1)
        self.assertIn("缺失", format_artifacts(artifacts))

    def test_a_run_without_a_deliverable_is_not_listed(self):
        rows = [row for row in self.rows if row["role"] != "run_end"]
        payload = self._call(rows)
        self.assertEqual(payload["artifacts"], [])

    def test_the_shared_listing_is_the_one_the_cli_uses(self):
        import inspect

        import mas.cli as cli_module

        # The CLI must not have its own copy of the listing any more:
        # it has to call the shared one, so the two surfaces cannot
        # drift apart.
        source = inspect.getsource(cli_module.cmd_artifacts)
        self.assertIn("artifact_rows(rows)", source)
        self.assertIn("format_artifacts(items)", source)
        # And the listing is not assembled a second time inside the
        # command - that is exactly how the two would drift.
        self.assertNotIn("run_summary(group", source)

    def test_the_gate_passes(self):
        result = check_the_deliverables_reach_the_mcp_caller(iterations=1)
        self.assertTrue(result["ok"], result["failures"])

    def test_the_gate_can_fail(self):
        import mas.mcp_server as mcp_module
        import mas.report as report_module

        real_rows = report_module.artifact_rows
        real_imported = mcp_module.artifact_rows
        try:
            report_module.artifact_rows = lambda rows: []
            mcp_module.artifact_rows = lambda rows: []
            result = check_the_deliverables_reach_the_mcp_caller(iterations=1)
        finally:
            report_module.artifact_rows = real_rows
            mcp_module.artifact_rows = real_imported
        self.assertFalse(result["ok"])
        self.assertTrue(result["failures"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

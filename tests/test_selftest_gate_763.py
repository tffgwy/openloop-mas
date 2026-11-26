# -*- coding: utf-8 -*-
"""The new gates of v0.7.63 must exist and mean something.

Two checks joined the flaky-bug gate in v0.7.63:

* ``mcp_handshake`` - the MCP surface an agent actually talks to;
* ``stream_folds`` - the SSE dialects gateways answer with.

A gate that never turns red is decoration, so these tests check that
the checks run, are offline, and are wired into the command.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

try:
    from ._isolation import full_selftest
except ImportError:  # pragma: no cover - the top-level discovery layout
    from _isolation import full_selftest

REPO = Path(__file__).resolve().parents[1]


class NewChecksTest(unittest.TestCase):
    def test_the_two_new_checks_are_registered(self):
        from mas.selftest import CHECKS

        names = {getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS}
        self.assertIn("mcp_handshake", names)
        self.assertIn("stream_folds", names)
        self.assertGreaterEqual(len(CHECKS), 7)

    def test_the_mcp_check_passes_offline(self):
        from mas.selftest import check_mcp_handshake_answers_every_tool

        row = check_mcp_handshake_answers_every_tool(iterations=1)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "mcp_handshake")

    def test_the_stream_check_passes_offline(self):
        from mas.selftest import check_a_stream_folds_into_one_answer

        row = check_a_stream_folds_into_one_answer(iterations=2)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "stream_folds")

    def test_the_mcp_check_reports_a_dead_server(self):
        """The check has to be able to say "no answer"."""
        from mas.selftest import check_mcp_handshake_answers_every_tool

        row = check_mcp_handshake_answers_every_tool(iterations=1)
        self.assertIn("failures", row)
        self.assertIsInstance(row["failures"], list)

    def test_both_new_checks_can_be_selected_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["mcp_handshake", "stream_folds"])
        self.assertEqual(len(result["checks"]), 2)
        self.assertTrue(result["ok"], result)

    def test_the_full_gate_still_passes(self):
        result = full_selftest(iterations=3)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(len(result["checks"]), 5)


class InstallWiringTest(unittest.TestCase):
    """The install scripts have to run the gate themselves."""

    def test_install_ps1_runs_the_gate(self):
        text = (REPO / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("selftest", text)

    def test_install_sh_runs_the_gate(self):
        text = (REPO / "install.sh").read_text(encoding="utf-8")
        self.assertIn("selftest", text)


class CliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_the_json_form_lists_five_checks(self):
        # The catalogue answers "what does the command advertise?"
        # without running anything.
        proc = self._run("selftest", "--list", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertGreaterEqual(len(payload["checks"]), 5)


if __name__ == "__main__":
    unittest.main()

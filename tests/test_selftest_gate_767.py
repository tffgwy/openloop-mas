# -*- coding: utf-8 -*-
"""The handback gate of v0.7.67 must exist and mean something.

v0.7.67 added a ninth check to the flaky-bug gate:

* ``handback_closes`` - a deliverable a human wrote must be able to
  close the loop, judged by the same acceptance criteria the task was
  written with.

Before v0.7.67 the ticket queue had exactly one answer: resume the
loop and let the machine try again. A human who had already written
the answer had nowhere to put it.

A gate that never turns red is decoration. These tests check that the
check is registered, runs offline, and is wired into the command.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class HandbackClosesGateTest(unittest.TestCase):
    def test_the_check_is_registered(self):
        from mas.selftest import CHECKS

        names = {getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS}
        self.assertIn("handback_closes", names)
        self.assertEqual(len(CHECKS), 13)

    def test_the_check_passes_offline(self):
        from mas.selftest import check_a_handback_closes_on_human_work

        row = check_a_handback_closes_on_human_work(iterations=1)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "handback_closes")

    def test_the_check_states_what_it_guarantees(self):
        from mas.selftest import check_a_handback_closes_on_human_work

        row = check_a_handback_closes_on_human_work(iterations=1)
        self.assertTrue(row.get("title"))
        self.assertTrue(row.get("invariant"))
        self.assertIsInstance(row.get("failures"), list)

    def test_the_check_can_be_selected_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["handback_closes"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "handback_closes")
        self.assertTrue(result["ok"], result)

    def test_the_check_is_slow_enough_to_be_capped(self):
        """Five checks run a whole loop per iteration - they are capped."""
        from mas.selftest import run_selftest

        result = run_selftest(iterations=50, only=["handback_closes"])
        row = result["checks"][0]
        self.assertLessEqual(int(row["iterations"]), 20)

    def test_the_full_gate_still_passes(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["checks"]), 13)


class CliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_the_json_form_lists_thirteen_checks(self):
        proc = self._run("selftest", "--iterations", "1", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload["checks"]), 13)
        self.assertTrue(payload["ok"])

    def test_it_runs_without_a_model_endpoint(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        for key in ("MAS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", "selftest", "--iterations", "1"],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # The human-readable form prints the title, not the short name.
        self.assertIn("人工写的交付物也能通过同一套验收", proc.stdout)

# -*- coding: utf-8 -*-
"""The resource-index gate of v0.7.71 must exist and mean something.

v0.7.71 added a fourteenth check to the flaky-bug gate:

* ``resource_index`` - a folder must be describable: every file listed,
  every format named by the file's own header, every fact read from
  the file, and a file that cannot be read listed with its reason.

Before v0.7.71 the answer to "what did this machine produce?" was
scattered across commands, and the answer to "what is in this folder?"
did not exist at all.

A gate that never turns red is decoration. These tests check that the
check is registered, runs offline, is wired into the command, and can
tell that its own observation was broken.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class ResourceIndexGateTest(unittest.TestCase):
    def test_the_check_is_registered(self):
        from mas.selftest import CHECKS

        names = {getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS}
        self.assertIn("resource_index", names)
        self.assertEqual(len(CHECKS), 20)

    def test_the_check_passes_offline(self):
        from mas.selftest import check_the_index_knows_every_file

        row = check_the_index_knows_every_file(iterations=1)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "resource_index")

    def test_the_check_states_what_it_guarantees(self):
        from mas.selftest import check_the_index_knows_every_file

        row = check_the_index_knows_every_file(iterations=1)
        self.assertTrue(row.get("title"))
        self.assertTrue(row.get("invariant"))
        self.assertIsInstance(row.get("failures"), list)

    def test_the_check_can_be_selected_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["resource_index"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "resource_index")
        self.assertTrue(result["ok"], result)

    def test_the_check_is_slow_enough_to_be_capped(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=50, only=["resource_index"])
        row = result["checks"][0]
        self.assertLessEqual(int(row["iterations"]), 20)

    def test_the_observations_are_all_reachable(self):
        from mas.selftest import RESOURCE_INDEX_OBSERVATIONS

        self.assertGreaterEqual(len(RESOURCE_INDEX_OBSERVATIONS), 10)
        for label in RESOURCE_INDEX_OBSERVATIONS:
            self.assertTrue(label)

    def test_the_check_fails_when_the_index_is_broken(self):
        """A gate that cannot fail is not a gate."""
        import mas.content as content
        from mas.selftest import check_the_index_knows_every_file

        real_detect = content.detect_format

        def _blind(path):
            return "binary"

        content.detect_format = _blind
        try:
            row = check_the_index_knows_every_file(iterations=1)
        finally:
            content.detect_format = real_detect
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_full_gate_still_passes(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["checks"]), 20)


class CliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_the_json_form_lists_eighteen_checks(self):
        proc = self._run("selftest", "--iterations", "1", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload["checks"]), 20)
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
        self.assertIn("循环知道自己产出了什么", proc.stdout)


if __name__ == "__main__":
    unittest.main()

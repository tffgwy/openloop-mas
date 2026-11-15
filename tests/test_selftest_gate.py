# -*- coding: utf-8 -*-
"""The flaky-bug gate must exist, run, and mean something (v0.7.62).

A gate that never turns red is decoration. These tests check three
things:

* the command exists and is wired into the CLI;
* it runs offline - no endpoint, no key, no network;
* its result is structured, so a CI runner can read the verdict.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class SelftestModuleTest(unittest.TestCase):
    def test_the_module_exposes_the_three_checks(self):
        from mas.selftest import CHECKS

        names = {getattr(c, "__name__", "") for c in CHECKS}
        self.assertIn("check_relay_refusal_keeps_the_connection_usable", names)
        self.assertIn("check_provider_keeps_the_usage_of_a_failed_call", names)
        self.assertIn("check_a_run_keeps_its_files_with_the_deliverable", names)

    def test_every_check_states_an_invariant_and_a_title(self):
        from mas.selftest import CHECKS

        for check in CHECKS:
            row = check(iterations=2)
            self.assertTrue(row.get("title"), check.__name__)
            self.assertTrue(row.get("invariant"), check.__name__)
            self.assertIn("ok", row)
            self.assertIsInstance(row.get("failures"), list)

    def test_a_short_run_passes(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(result["checks"]), 3)

    def test_the_result_is_json_serialisable(self):
        from mas.selftest import run_selftest

        payload = json.dumps(run_selftest(iterations=2), ensure_ascii=False)
        self.assertIn("checks", payload)

    def test_an_unknown_check_name_is_refused(self):
        from mas.selftest import run_selftest

        with self.assertRaises(ValueError):
            run_selftest(iterations=2, only=["no-such-check"])

    def test_a_single_check_can_be_selected(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=2, only=["provider_usage"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "provider_usage")

    def test_the_report_is_readable(self):
        from mas.selftest import format_selftest, run_selftest

        text = format_selftest(run_selftest(iterations=2))
        self.assertIn("\u7ed3\u8bba", text)
        self.assertIn("\u4fdd\u8bc1", text)


class SelftestCliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        env.pop("MAS_API_KEY", None)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )
        return proc  # noqa: RET504 - the explicit name reads better here

    def test_the_command_is_listed(self):
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("selftest", proc.stdout)

    def test_the_command_runs_and_exits_zero(self):
        proc = self._run("selftest", "--iterations", "3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("\u7ed3\u8bba", proc.stdout)

    def test_the_json_form_is_machine_readable(self):
        proc = self._run("selftest", "--iterations", "2", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["checks"]), 3)

    def test_it_runs_without_a_model_endpoint(self):
        """The gate is offline: no endpoint, no key, no network."""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO),
            "PYTHONUTF8": "1",
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "HOME": os.environ.get("HOME", ""),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
        }
        for key in ("MAS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", "selftest", "--iterations", "2"],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

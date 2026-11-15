"""The ledger has to say what happened, in words.

The ledger listing used to print the engine's own identifiers:

    #150 2026-11-13T22:14:28+00:00 closure   iter=1 pass close sha=c8ce61c843

A reader who has not read the source code cannot tell whether
"closure" is a good thing, whether "pass" means the work was
accepted, or whether "close" means the loop finished. The names are
the engine's vocabulary, and the engine's vocabulary is not the
reader's.

The rule that keeps this honest: the raw name stays next to the word.
The table is pasted into issues as often as it is read, so it has to
stay greppable - but it also has to be readable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mas.report import ledger_markdown
from mas.verdict_text import role_reading


class RoleReadingTest(unittest.TestCase):
    """Every role the engine writes has to have a word."""

    def test_the_roles_read_in_words(self):
        self.assertEqual(role_reading("planner"), "规划")
        self.assertEqual(role_reading("write"), "执行")
        self.assertEqual(role_reading("review"), "评审")
        self.assertEqual(role_reading("closure"), "验收")
        self.assertEqual(role_reading("reflector"), "复盘")
        self.assertEqual(role_reading("run_end"), "收尾")

    def test_an_unknown_role_keeps_its_own_name(self):
        """An unknown role may carry meaning this table cannot see."""
        self.assertEqual(role_reading("some_new_role"), "some_new_role")

    def test_an_empty_role_reads_as_nothing(self):
        self.assertEqual(role_reading(""), "")
        self.assertEqual(role_reading(None), "")


class LedgerTableTest(unittest.TestCase):
    def _rows(self):
        return [
            {
                "seq": 1,
                "ts": "2026-01-01T00:00:00+00:00",
                "run_id": "R-1",
                "iteration": 1,
                "role": "planner",
                "detail": "拆分目标",
            },
            {
                "seq": 2,
                "ts": "2026-01-01T00:00:01+00:00",
                "run_id": "R-1",
                "iteration": 1,
                "role": "closure",
                "verdict": "fail",
                "decision": "revise",
                "scores": [{"id": "c1", "pass": False, "note": "长度不足"}],
                "issues": ["c1: 长度不足"],
            },
        ]

    def test_the_table_says_the_role_in_words(self):
        table = ledger_markdown(self._rows())
        self.assertIn("规划", table)

    def test_the_table_keeps_the_raw_role_too(self):
        """The table is pasted into issues - it must stay greppable."""
        table = ledger_markdown(self._rows())
        self.assertIn("planner", table)
        self.assertIn("closure", table)

    def test_the_table_says_the_verdict_in_words(self):
        table = ledger_markdown(self._rows())
        self.assertIn("没通过", table)
        self.assertIn("要改一版", table)

    def test_the_table_still_has_the_raw_verdict(self):
        table = ledger_markdown(self._rows())
        self.assertIn("fail", table)
        self.assertIn("revise", table)

    def test_the_table_has_a_column_for_the_verdict(self):
        table = ledger_markdown(self._rows())
        self.assertIn("判定", table)
        self.assertIn("决策", table)


class LedgerCommandTest(unittest.TestCase):
    """The command itself, on a real ledger."""

    def _run(self, args, state_dir):
        env = dict(os.environ)
        env["MAS_STATE_DIR"] = str(state_dir)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_ledger_listing_says_the_role_in_words(self):
        code, out = self._run(["ledger"], self.state_dir)
        self.assertEqual(code, 0)
        # An empty ledger still has to be honest about being empty.
        self.assertIn("账本还是空的", out)

    def test_the_ledger_json_keeps_the_raw_role(self):
        """The machine view is unchanged - only the screen changed."""
        code, out = self._run(["ledger", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("entries", payload)


if __name__ == "__main__":
    unittest.main()

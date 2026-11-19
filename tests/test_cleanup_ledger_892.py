# -*- coding: utf-8 -*-
"""v0.7.92 tests: the ledger can be cleaned of the old self-test rows.

The gap it closes: before v0.7.92 every run of ``mas selftest`` wrote
draft decisions into the main ledger of the machine. Those rows point
at temporary folders that are deleted right afterwards, so ``mas
report`` reported audit problems that had no cause. v0.7.92 stops the
leak; this module covers the *repair* for machines that already carry
the old rows.

The tests pin:

* only draft decisions in temporary workspaces are removed;
* a real decision in a real workspace is kept;
* a row that cannot be parsed is kept, never destroyed;
* the ledger is backed up before anything is written;
* a dry run changes nothing;
* the command line reaches the module.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from mas import cleanup

REPO = pathlib.Path(__file__).resolve().parents[1]


def _row(event: str, workspace: str) -> str:
    return json.dumps(
        {
            "role": event,
            "event": event,
            "workspace": workspace,
            "ts": "2026-11-17T07:59:40+00:00",
        },
        ensure_ascii=False,
    ) + "\n"


class ClassificationTest(unittest.TestCase):
    def test_a_temporary_draft_decision_is_dropped(self):
        line = _row("draft_approve", r"C:\Users\X\AppData\Local\Temp\mas-audit-i7m3sizh\ws")
        split = cleanup.classify_rows([line])
        self.assertEqual(split["dropped_count"], 1)
        self.assertEqual(split["kept_count"], 0)

    def test_a_real_draft_decision_is_kept(self):
        line = _row("draft_approve", r"C:\Users\X\work\my-project")
        split = cleanup.classify_rows([line])
        self.assertEqual(split["dropped_count"], 0)
        self.assertEqual(split["kept_count"], 1)

    def test_a_real_run_row_is_kept(self):
        line = _row("write", r"C:\Users\X\AppData\Local\Temp\tmpabc\ws")
        split = cleanup.classify_rows([line])
        self.assertEqual(split["dropped_count"], 0)
        self.assertEqual(split["kept_count"], 1)

    def test_a_row_that_cannot_be_parsed_is_kept(self):
        split = cleanup.classify_rows(["not json at all\n"])
        self.assertEqual(split["dropped_count"], 0)
        self.assertEqual(split["kept_count"], 1)
        self.assertEqual(split["unparsable"], 1)

    def test_a_row_without_a_workspace_is_kept(self):
        split = cleanup.classify_rows([_row("draft_approve", "")])
        self.assertEqual(split["dropped_count"], 0)


class CleanLedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-clean-")
        self.addCleanup(self._tmp.cleanup)
        self.state = pathlib.Path(self._tmp.name)
        self.ledger = self.state / "ledgers" / "runs.jsonl"
        self.ledger.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, lines):
        self.ledger.write_text("".join(lines), encoding="utf-8")

    def test_a_missing_ledger_is_not_an_error(self):
        result = cleanup.clean_selftest_ledger(self.state)
        self.assertTrue(result["exists"] is False)
        self.assertEqual(result["dropped_count"], 0)
        self.assertNotIn("error", result)

    def test_a_dry_run_changes_nothing(self):
        self._write([_row("draft_approve", r"C:\Temp\mas-audit-x\ws")])
        result = cleanup.clean_selftest_ledger(self.state, dry_run=True)
        self.assertEqual(result["dropped_count"], 1)
        self.assertEqual(result["written"], False)
        self.assertEqual(result["backup"], "")
        self.assertEqual(len(self.ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_the_leaked_rows_are_removed_and_the_rest_are_kept(self):
        self._write(
            [
                _row("draft_approve", r"C:\Users\X\AppData\Local\Temp\mas-audit-a\ws"),
                _row("draft_undo", r"C:\Users\X\AppData\Local\Temp\mas-audit-a\ws"),
                _row("draft_approve", r"C:\Users\X\real-project"),
                _row("write", r"C:\Users\X\real-project"),
            ]
        )
        result = cleanup.clean_selftest_ledger(self.state)
        self.assertEqual(result["dropped_count"], 2)
        self.assertEqual(result["kept_count"], 2)
        self.assertTrue(result["written"])
        text = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn("mas-audit-a", text)
        self.assertIn("real-project", text)

    def test_the_ledger_is_backed_up_before_anything_is_written(self):
        original = _row("draft_approve", r"C:\Temp\mas-audit-a\ws")
        self._write([original])
        result = cleanup.clean_selftest_ledger(self.state)
        backup = pathlib.Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), original)

    def test_a_ledger_without_leaks_is_left_alone(self):
        self._write([_row("draft_approve", r"C:\Users\X\real-project")])
        result = cleanup.clean_selftest_ledger(self.state)
        self.assertEqual(result["dropped_count"], 0)
        self.assertEqual(result["written"], False)
        self.assertEqual(result["backup"], "")


class CommandLineTest(unittest.TestCase):
    def test_the_command_reaches_the_module(self):
        script = (
            "import sys, json;"
            "sys.path.insert(0, %r);"
            "from mas.cli import build_parser;"
            "args = build_parser().parse_args(['clean', '--selftest-ledger', '--dry-run']);"
            "print(json.dumps({'func': args.func.__name__, 'dry_run': args.dry_run}))"
        ) % str(REPO)
        out = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        payload = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["func"], "cmd_clean")
        self.assertTrue(payload["dry_run"])


if __name__ == "__main__":
    unittest.main()

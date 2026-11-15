# -*- coding: utf-8 -*-
"""v0.7.53 regression tests: "not found" has to say "not found".

Every command that takes ``--run-id`` filtered the ledger and then
reported "the ledger is empty" when nothing came back. That was a lie
in the common case: the ledger held twenty-two records for a
*different* run, and the reader was told there was no history at all.

A beginner's first mistake is a mistyped run id - or a ticket that
points at a run from another machine. The answer has to name what is
missing *and* show what does exist, otherwise the reader cannot tell
"you typed it wrong" from "the work never happened".

``mas export`` had a second problem: it accepted ``--json`` and then
ignored it, handing a Markdown table to a script that asked for data.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import tempfile
import unittest

from mas.cli import main


def _invoke(argv, state_dir):
    """Run the CLI, capturing stdout and stderr separately."""
    os.environ["MAS_STATE_DIR"] = state_dir
    os.environ["MAS_PROVIDER"] = "mock"
    os.environ.pop("MAS_API_KEY", None)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class MissingRunSaysTheTruthTest(unittest.TestCase):
    """A run id that does not exist must not be reported as an empty ledger."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-missing-run-")
        self._saved_config_path = os.environ.get("MAS_CONFIG_PATH")
        self._saved_state_dir = os.environ.get("MAS_STATE_DIR")

    def tearDown(self):
        if self._saved_config_path is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._saved_config_path
        if self._saved_state_dir is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state_dir

    def _make_a_real_run(self):
        """Fill the ledger so "not found" cannot be confused with "empty"."""
        workspace = tempfile.mkdtemp(prefix="mas-missing-run-ws-")
        _invoke(
            [
                "run",
                "--workspace",
                workspace,
                "--goal",
                "写个东西",
                "--criteria",
                "file_contains:out/x.md::交付物",
            ],
            self.state_dir,
        )
        _code, out, _err = _invoke(["tickets", "--json"], self.state_dir)
        tickets = json.loads(out)["tickets"]
        self.assertTrue(tickets, "the fixture should have produced a ticket")
        _code, out, _err = _invoke(["replay", "--json"], self.state_dir)
        runs = json.loads(out).get("runs") or []
        self.assertTrue(runs, "the fixture should have produced a run")
        return runs[0]["run_id"]

    # -------------------------------------------------- the four commands
    def test_replay_does_not_call_a_missing_run_an_empty_ledger(self):
        run_id = self._make_a_real_run()
        code, _out, err = _invoke(["replay", "--run-id", "R-nope"], self.state_dir)
        self.assertNotEqual(code, 0)
        self.assertIn("R-nope", err)
        self.assertNotIn("账本为空", err)
        self.assertIn(run_id, err, "the message should show what does exist")

    def test_export_does_not_call_a_missing_run_an_empty_ledger(self):
        run_id = self._make_a_real_run()
        code, _out, err = _invoke(["export", "--run-id", "R-nope"], self.state_dir)
        self.assertNotEqual(code, 0)
        self.assertNotIn("账本为空", err)
        self.assertIn(run_id, err)

    def test_report_does_not_call_a_missing_run_an_empty_ledger(self):
        run_id = self._make_a_real_run()
        code, _out, err = _invoke(["report", "--run-id", "R-nope"], self.state_dir)
        self.assertNotEqual(code, 0)
        self.assertNotIn("账本为空", err)
        self.assertIn(run_id, err)

    def test_artifacts_does_not_call_a_missing_run_an_empty_ledger(self):
        run_id = self._make_a_real_run()
        code, _out, err = _invoke(["artifacts", "--run-id", "R-nope"], self.state_dir)
        self.assertNotEqual(code, 0)
        self.assertNotIn("账本为空", err)
        self.assertIn(run_id, err)

    # -------------------------------------------------- the json shape
    def test_the_json_miss_is_machine_readable(self):
        self._make_a_real_run()
        for argv in (
            ["replay", "--run-id", "R-nope", "--json"],
            ["export", "--run-id", "R-nope", "--json"],
            ["report", "--run-id", "R-nope", "--json"],
            ["artifacts", "--run-id", "R-nope", "--json"],
        ):
            with self.subTest(command=argv[0]):
                code, out, _err = _invoke(argv, self.state_dir)
                self.assertNotEqual(code, 0)
                payload = json.loads(out)
                self.assertEqual(payload["error"], "run_not_found")
                self.assertFalse(payload["found"])

    # -------------------------------------------------- the empty ledger
    def test_an_empty_ledger_is_still_an_empty_ledger(self):
        """The fix must not turn "nothing happened" into "not found"."""
        code, _out, err = _invoke(["replay", "--run-id", "R-nope"], self.state_dir)
        self.assertNotEqual(code, 0)
        self.assertIn("还没有任何运行记录", err)
        self.assertNotIn("账本里有这些运行", err)

    def test_an_empty_ledger_says_what_to_do_next(self):
        code, out, _err = _invoke(["export"], self.state_dir)
        self.assertEqual(code, 0)
        self.assertIn("mas run", out)
        self.assertNotIn("| # | 时间 |", out, "an empty table says nothing")

    def test_export_json_on_an_empty_ledger_is_still_json(self):
        code, out, _err = _invoke(["export", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["count"], 0)

    def test_replay_json_on_an_empty_ledger_is_still_json(self):
        code, out, _err = _invoke(["replay", "--json"], self.state_dir)
        self.assertNotEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "empty_ledger")
        self.assertEqual(payload["steps"], [])

    # -------------------------------------------------- export --json
    def test_export_json_actually_returns_json(self):
        """``--json`` was accepted and then ignored."""
        self._make_a_real_run()
        code, out, _err = _invoke(["export", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertGreater(payload["count"], 0)
        self.assertEqual(len(payload["rows"]), payload["count"])
        self.assertIn("| # | 时间 |", payload["markdown"])

    def test_export_json_and_export_text_agree(self):
        self._make_a_real_run()
        _code, text, _err = _invoke(["export"], self.state_dir)
        _code, out, _err = _invoke(["export", "--json"], self.state_dir)
        payload = json.loads(out)
        self.assertEqual(payload["markdown"].strip(), text.strip())

    # -------------------------------------------------- the happy path
    def test_an_existing_run_id_still_works(self):
        run_id = self._make_a_real_run()
        code, out, _err = _invoke(["replay", "--run-id", run_id], self.state_dir)
        self.assertEqual(code, 0)
        self.assertIn(run_id, out)

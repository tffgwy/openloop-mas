# -*- coding: utf-8 -*-
"""v0.8.8: a decision hangs on the run that proposed it.

The regression this file locks down is the one v0.8.8 fixed: a human
decision about a draft (an approval, or an approval taken back) used
to be filed **without the thread** that proposed the draft. The main
ledger hard-coded ``run_id`` and ``task_id`` to empty strings, so the
report's "提议运行" column printed a dash for every row and the
question "which of my runs once had a wrong approval?" could not be
asked of the ledger at all.

What has to hold after this release:

* the draft remembers the run and the task that proposed it;
* the ledger's approve row and undo row carry that thread;
* the audit chain agrees with the ledger (the thread is one of the
  copied fields, so a one-sided edit is caught);
* the report's draft table shows the run;
* the draft list shows the run;
* ``mas undos --run-id`` returns only that run's decisions;
* the MCP surface can be asked about one run as well.
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.drafts import (
    approve_draft,
    get_draft,
    ledger_row_for,
    stage_draft,
    undo_draft,
)


class _Env:
    def __init__(self, state_dir, config_path):
        self._values = {"MAS_STATE_DIR": state_dir, "MAS_CONFIG_PATH": config_path}
        self._saved = {}

    def __enter__(self):
        for name, value in self._values.items():
            self._saved[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *exc):
        for name, saved in self._saved.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


class _TwoRuns(unittest.TestCase):
    """Two threads, each with a decision about its own draft."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-test-run-")
        root = pathlib.Path(self._tmp.name)
        self.state = root / "state"
        self.artifacts = self.state / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.config_path = root / "config.json"
        self.made = {}
        with _Env(str(self.state), str(self.config_path)):
            for run_id in ("R-mine", "R-other"):
                workspace = self.artifacts / run_id
                workspace.mkdir(parents=True, exist_ok=True)
                (workspace / "notes.txt").write_text("alpha\n", encoding="utf-8")
                staged = stage_draft(
                    workspace,
                    "notes.txt",
                    ("alpha\nfrom %s\n" % run_id).encode("utf-8"),
                    reason="the loop proposes a change",
                    source="test",
                    run_id=run_id,
                    task_id="T-" + run_id,
                )
                draft_id = str(staged.get("id") or "")
                approve_draft(workspace, draft_id, note="looks fine")
                undo_draft(workspace, draft_id, note="approved the wrong thing")
                self.made[run_id] = {
                    "workspace": str(workspace),
                    "draft_id": draft_id,
                }

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, args):
        from mas.cli import main

        buffer = io.StringIO()
        with _Env(str(self.state), str(self.config_path)):
            try:
                with contextlib.redirect_stdout(buffer):
                    main(args)
            except SystemExit:
                pass
        return buffer.getvalue()

    def _json(self, args):
        text = self._run(args)
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - a broken page is the subject
            return {}


class TheDraftRemembersItsRunTest(_TwoRuns):
    def test_the_draft_carries_the_run_and_the_task(self):
        mine = self.made["R-mine"]
        with _Env(str(self.state), str(self.config_path)):
            draft = get_draft(mine["workspace"], mine["draft_id"])
        self.assertEqual(draft.get("run_id"), "R-mine")
        self.assertEqual(draft.get("task_id"), "T-R-mine")

    def test_the_ledger_row_is_not_hard_coded_to_empty(self):
        # The bug itself: the row's thread was written as "".
        row = ledger_row_for(
            {
                "event": "draft_approve",
                "draft_id": "D-1",
                "path": "notes.txt",
                "run_id": "R-mine",
                "task_id": "T-R-mine",
            },
            self.made["R-mine"]["workspace"],
        )
        self.assertEqual(row.get("run_id"), "R-mine")
        self.assertEqual(row.get("task_id"), "T-R-mine")

    def test_a_row_without_a_thread_still_says_empty(self):
        row = ledger_row_for(
            {"event": "draft_approve", "draft_id": "D-1", "path": "notes.txt"},
            self.made["R-mine"]["workspace"],
        )
        self.assertEqual(row.get("run_id"), "")
        self.assertEqual(row.get("task_id"), "")


class TheLedgerCarriesTheThreadTest(_TwoRuns):
    def _ledger_rows(self):
        from mas.ledger import Ledger

        path = self.state / "ledgers" / "runs.jsonl"
        if not path.exists():
            return []
        return list(Ledger(path).entries())

    def test_the_approve_row_carries_the_run(self):
        rows = self._ledger_rows()
        mine = self.made["R-mine"]
        approve = [
            row
            for row in rows
            if str(row.get("role") or "") == "draft_approve"
            and str(row.get("draft_id") or "") == mine["draft_id"]
        ]
        self.assertTrue(approve, "the ledger must hold the approval")
        for row in approve:
            self.assertEqual(row.get("run_id"), "R-mine")
            self.assertEqual(row.get("task_id"), "T-R-mine")

    def test_the_undo_row_carries_the_run(self):
        rows = self._ledger_rows()
        mine = self.made["R-mine"]
        undo = [
            row
            for row in rows
            if str(row.get("role") or "") == "draft_undo"
            and str(row.get("draft_id") or "") == mine["draft_id"]
        ]
        self.assertTrue(undo, "the ledger must hold the undo")
        for row in undo:
            self.assertEqual(row.get("run_id"), "R-mine")

    def test_the_chain_and_the_ledger_agree(self):
        from mas.audit import verify_against_ledger
        from mas.drafts import audit_chain

        mine = self.made["R-mine"]
        chain = audit_chain(mine["workspace"])
        result = verify_against_ledger(chain, self._ledger_rows())
        self.assertTrue(result.get("ok"), result.get("problems"))

    def test_the_cross_check_catches_a_one_sided_thread(self):
        # The thread is one of the copied fields, so a ledger row whose
        # thread was edited on its own is caught.
        from mas.audit import verify_against_ledger
        from mas.drafts import audit_chain

        mine = self.made["R-mine"]
        chain = audit_chain(mine["workspace"])
        rows = self._ledger_rows()
        tampered = []
        for row in rows:
            copy = dict(row)
            if (
                str(copy.get("role") or "") == "draft_approve"
                and str(copy.get("draft_id") or "") == mine["draft_id"]
            ):
                copy["run_id"] = "R-somewhere-else"
            tampered.append(copy)
        result = verify_against_ledger(chain, tampered)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("problems"))


class TheReportShowsTheRunTest(_TwoRuns):
    def test_the_report_table_shows_the_run(self):
        mine = self.made["R-mine"]
        markdown = self._run(["report"])
        self.assertIn(mine["draft_id"], markdown)
        self.assertIn("R-mine", markdown)

    def test_the_report_json_carries_the_run(self):
        mine = self.made["R-mine"]
        payload = self._json(["report", "--json"])
        rows = (payload.get("drafts") or {}).get("drafts") or []
        found = [
            row
            for row in rows
            if str(row.get("draft_id") or "") == mine["draft_id"]
        ]
        self.assertTrue(found)
        self.assertEqual(found[0].get("run_id"), "R-mine")

    def test_the_draft_list_shows_the_run(self):
        mine = self.made["R-mine"]
        text = self._run(["drafts", "list", "--workspace", str(self.artifacts)])
        self.assertIn(mine["draft_id"], text)
        self.assertIn("R-mine", text)


class TheUndoFilterTest(_TwoRuns):
    def test_the_filter_returns_only_that_run(self):
        payload = self._json(
            ["undos", "--workspace", str(self.artifacts), "--run-id", "R-mine", "--json"]
        )
        records = payload.get("records") or []
        self.assertTrue(records)
        for row in records:
            self.assertEqual(row.get("run_id"), "R-mine")
        other_id = self.made["R-other"]["draft_id"]
        for row in records:
            self.assertNotEqual(row.get("draft_id"), other_id)

    def test_the_filter_says_which_run_it_looked_at(self):
        payload = self._json(
            ["undos", "--workspace", str(self.artifacts), "--run-id", "R-mine", "--json"]
        )
        self.assertEqual(payload.get("run_id"), "R-mine")

    def test_the_totals_are_the_filtered_ones(self):
        payload = self._json(
            ["undos", "--workspace", str(self.artifacts), "--run-id", "R-mine", "--json"]
        )
        totals = payload.get("totals") or {}
        self.assertEqual(totals.get("undone"), 1)

    def test_a_run_with_no_decisions_returns_nothing(self):
        payload = self._json(
            ["undos", "--workspace", str(self.artifacts), "--run-id", "R-nobody", "--json"]
        )
        self.assertEqual(payload.get("records"), [])
        self.assertEqual((payload.get("totals") or {}).get("undone"), 0)

    def test_the_unfiltered_view_still_shows_both(self):
        payload = self._json(["undos", "--workspace", str(self.artifacts), "--json"])
        records = payload.get("records") or []
        self.assertEqual(len(records), 2)


class TheMcpSurfaceTest(_TwoRuns):
    def _call(self, arguments):
        from mas.mcp_server import handle_tool

        return handle_tool("mas_undos", arguments)

    def test_the_tool_takes_a_run_id(self):
        from mas.mcp_server import TOOLS

        found = [tool for tool in TOOLS if tool.get("name") == "mas_undos"]
        self.assertTrue(found)
        properties = (found[0].get("inputSchema") or {}).get("properties") or {}
        self.assertIn("run_id", properties)

    def test_the_tool_can_be_asked_about_one_run(self):
        payload = self._call({"workspace": str(self.artifacts), "run_id": "R-mine"})
        records = payload.get("records") or []
        self.assertTrue(records)
        for row in records:
            self.assertEqual(row.get("run_id"), "R-mine")
        other_id = self.made["R-other"]["draft_id"]
        for row in records:
            self.assertNotEqual(row.get("draft_id"), other_id)

    def test_the_tool_without_a_run_id_shows_everything(self):
        payload = self._call({"workspace": str(self.artifacts)})
        self.assertEqual(len(payload.get("records") or []), 2)


class TheGateTest(unittest.TestCase):
    def test_the_gate_is_registered(self):
        from mas.selftest import CHECKS

        names = [getattr(check, "CHECK_NAME", "") for check in CHECKS]
        self.assertIn("a_decision_hangs_on_its_run", names)

    def test_the_gate_passes(self):
        from mas.selftest_draft_decision_run import (
            check_a_decision_hangs_on_its_run,
        )

        result = check_a_decision_hangs_on_its_run(iterations=1)
        self.assertTrue(result.get("ok"), result.get("failures"))
        self.assertEqual(result.get("failures"), [])


if __name__ == "__main__":
    unittest.main()

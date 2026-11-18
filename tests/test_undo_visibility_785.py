# -*- coding: utf-8 -*-
"""v0.7.85 tests: an undo is visible in the audit.

v0.7.77 made the undo put the file back. v0.7.78 made it leave a
tamper-evident trace. Neither made it *visible* in the way a reader
needs: "which of the things I approved had to be taken back" is a
list, not a line you happen to scroll past. And a draft that was
approved, taken back and approved again is ``approved`` - its own
state cannot say that it was once wrong.

This file covers:

* each undo is paired with the approval it cancels;
* an undo with nothing standing to cancel is reported as unmatched;
* the size of the change is recorded on both ends;
* the draft's history survives a second approval;
* the command line can ask the question and get an answer;
* the gate is registered and can fail.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from mas.drafts import approve_draft, stage_draft, undo_draft
from mas.selftest import CHECKS, EXPECTED_CHECKS
from mas.undos import (
    draft_history,
    pair_decisions,
    undo_report,
    workspace_rows,
)


class _StateCase(unittest.TestCase):
    """A test case with its own state directory, so nothing leaks out."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-p85-test-"))
        self.state = self.root / "state"
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\n", encoding="utf-8")
        self._saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.state)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved
        shutil.rmtree(self.root, ignore_errors=True)

    def _cycle(self):
        """Approve, take back, approve again."""
        staged = stage_draft(
            self.workspace,
            "notes.txt",
            b"alpha\nbeta-agent\n",
            reason="the agent proposes this",
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(self.workspace, draft_id, note="looks fine")
        undo_draft(self.workspace, draft_id, note="approved the wrong thing")
        approve_draft(self.workspace, draft_id, note="corrected")
        return draft_id


class PairingTest(_StateCase):
    """Each undo cancels the most recent approval that is still standing."""

    def test_the_undo_is_paired_with_the_approval_it_cancels(self):
        draft_id = self._cycle()
        records, unmatched = pair_decisions(workspace_rows(self.workspace))
        self.assertEqual(len(records), 1)
        self.assertEqual(unmatched, [])
        record = records[0]
        self.assertEqual(record["draft_id"], draft_id)
        self.assertLess(int(record["approve_seq"]), int(record["undo_seq"]))

    def test_the_second_approval_is_not_paired(self):
        """Only the approval that was actually taken back is listed."""
        self._cycle()
        records, _ = pair_decisions(workspace_rows(self.workspace))
        self.assertEqual(len(records), 1)

    def test_an_undo_with_nothing_to_cancel_is_not_paired(self):
        """An undo with no standing approval is reported, not invented."""
        draft_id = self._cycle()
        rows = [
            row
            for row in workspace_rows(self.workspace)
            if not (
                str(row.get("event") or "") == "draft_approve"
                and str(row.get("draft_id") or "") == draft_id
            )
        ]
        records, unmatched = pair_decisions(rows)
        self.assertEqual(records, [])
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(str(unmatched[0].get("draft_id")), draft_id)


class SizeTest(_StateCase):
    """The change has a size, seen from both ends."""

    def test_the_approval_records_how_many_lines_it_changed(self):
        staged = stage_draft(
            self.workspace, "notes.txt", b"alpha\nbeta-agent\n"
        )
        approve_draft(self.workspace, str(staged.get("id") or ""))
        rows = workspace_rows(self.workspace)
        approvals = [
            row for row in rows if str(row.get("event") or "") == "draft_approve"
        ]
        self.assertTrue(approvals)
        # One line removed, one line added.
        self.assertEqual(int(approvals[0].get("changed_line_count") or 0), 2)

    def test_the_undo_records_how_many_lines_it_took_back(self):
        draft_id = self._cycle()
        rows = workspace_rows(self.workspace)
        undos = [
            row
            for row in rows
            if str(row.get("event") or "") == "draft_undo"
            and str(row.get("draft_id") or "") == draft_id
        ]
        self.assertTrue(undos)
        self.assertEqual(int(undos[0].get("reverted_line_count") or 0), 2)

    def test_the_two_ends_agree(self):
        self._cycle()
        report = undo_report(workspace_rows(self.workspace))
        self.assertEqual(int(report["totals"]["reverted_lines"]), 2)
        self.assertEqual(int(report["totals"]["undone"]), 1)
        self.assertEqual(int(report["totals"]["records_without_line_count"]), 0)


class HistoryTest(_StateCase):
    """The draft's own state cannot hold this; the history can."""

    def test_the_draft_state_cannot_say_it(self):
        from mas.drafts import get_draft

        draft_id = self._cycle()
        draft = get_draft(self.workspace, draft_id)
        # The file says "approved" - the wrongness is gone from it.
        self.assertEqual(draft.get("state"), "approved")

    def test_the_history_can_say_it(self):
        draft_id = self._cycle()
        history = draft_history(workspace_rows(self.workspace), draft_id)
        self.assertTrue(history["ever_undone"])
        self.assertEqual(history["approvals"], 2)
        self.assertEqual(history["undos"], 1)

    def test_a_draft_that_was_never_taken_back_says_so(self):
        staged = stage_draft(
            self.workspace, "notes.txt", b"alpha\nbeta-agent\n"
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(self.workspace, draft_id)
        history = draft_history(workspace_rows(self.workspace), draft_id)
        self.assertFalse(history["ever_undone"])
        self.assertEqual(history["undos"], 0)


class CommandLineTest(_StateCase):
    """The question can be asked from the command line."""

    def _run(self, args):
        env = dict(os.environ)
        env["MAS_STATE_DIR"] = str(self.state)
        env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1])
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def test_the_undos_command_lists_the_taken_back_approvals(self):
        draft_id = self._cycle()
        code, out = self._run(["undos", "--workspace", str(self.workspace)])
        self.assertEqual(code, 0)
        self.assertIn("被撤销的批准", out)
        self.assertIn(draft_id, out)
        self.assertIn("2 行改动退回原样", out)

    def test_the_undos_command_json_keeps_the_raw_fields(self):
        draft_id = self._cycle()
        code, out = self._run(
            ["undos", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["records"]), 1)
        self.assertEqual(payload["records"][0]["draft_id"], draft_id)
        self.assertEqual(payload["records"][0]["reverted_line_count"], 2)

    def test_the_undos_command_says_when_nothing_was_taken_back(self):
        code, out = self._run(["undos", "--workspace", str(self.workspace)])
        self.assertEqual(code, 0)
        self.assertIn("还没有任何被撤销的批准", out)

    def test_the_drafts_list_shows_that_the_draft_was_once_wrong(self):
        draft_id = self._cycle()
        code, out = self._run(
            ["drafts", "list", "--workspace", str(self.workspace)]
        )
        self.assertEqual(code, 0)
        self.assertIn("批准被撤销过", out)
        self.assertIn(draft_id, out)

    def test_the_ledger_can_be_filtered_to_the_undos(self):
        draft_id = self._cycle()
        code, out = self._run(["ledger", "--undone"])
        self.assertEqual(code, 0)
        self.assertIn("撤销改动", out)
        self.assertIn(draft_id, out)

    def test_the_ledger_filter_by_draft(self):
        draft_id = self._cycle()
        code, out = self._run(["ledger", "--draft", draft_id])
        self.assertEqual(code, 0)
        self.assertIn(draft_id, out)


class McpUndoTests(_StateCase):
    """An agent can ask the same question over MCP."""

    def test_the_tool_is_advertised(self):
        from mas.mcp_server import TOOLS

        names = [tool.get("name") for tool in TOOLS]
        self.assertIn("mas_undos", names)

    def test_the_tool_lists_the_taken_back_approvals(self):
        from mas.mcp_server import handle_tool

        draft_id = self._cycle()
        result = handle_tool(
            "mas_undos", {"workspace": str(self.workspace)}
        )
        self.assertNotIn("error", result)
        records = list(result.get("records") or [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].get("draft_id"), draft_id)
        self.assertEqual(int(records[0].get("reverted_line_count") or 0), 2)
        self.assertEqual(int(result.get("totals", {}).get("undone") or 0), 1)

    def test_the_tool_can_be_asked_about_one_draft(self):
        from mas.mcp_server import handle_tool

        draft_id = self._cycle()
        result = handle_tool(
            "mas_undos",
            {"workspace": str(self.workspace), "draft_id": draft_id},
        )
        self.assertNotIn("error", result)
        self.assertEqual(result.get("draft_id"), draft_id)
        self.assertTrue(result.get("ever_undone"))
        self.assertEqual(int(result.get("approvals") or 0), 2)
        self.assertEqual(int(result.get("undos") or 0), 1)

    def test_the_tool_says_when_nothing_was_taken_back(self):
        from mas.mcp_server import handle_tool

        result = handle_tool(
            "mas_undos", {"workspace": str(self.workspace)}
        )
        self.assertNotIn("error", result)
        self.assertEqual(list(result.get("records") or []), [])
        self.assertEqual(list(result.get("unmatched") or []), [])

    def test_the_tool_reads_the_chain_and_says_where_it_read_from(self):
        """The answer names its source, so a reader knows what it saw."""
        from mas.mcp_server import handle_tool

        self._cycle()
        result = handle_tool(
            "mas_undos", {"workspace": str(self.workspace)}
        )
        records = list(result.get("records") or [])
        self.assertEqual(
            str(records[0].get("approve_record_set") or ""), "chain"
        )
        self.assertEqual(
            str(records[0].get("undo_record_set") or ""), "chain"
        )


class GateTest(unittest.TestCase):
    """The twenty-fifth gate is registered and can fail."""

    def test_the_gate_is_registered(self):
        names = [getattr(check, "CHECK_NAME", "") for check in CHECKS]
        self.assertIn("undo_visibility", names)
        self.assertEqual(EXPECTED_CHECKS, len(CHECKS))

    def test_the_gate_holds(self):
        from mas.selftest_undo_visibility import (
            check_an_undo_is_visible_in_the_audit,
        )

        result = check_an_undo_is_visible_in_the_audit(iterations=2)
        self.assertEqual(result["failures"], [])
        self.assertTrue(result["ok"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

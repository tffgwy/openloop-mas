# -*- coding: utf-8 -*-
"""v0.8.6 tests: a taken-back approval is visible on every surface.

A draft file holds only the **newest** state of a draft. Approve a
change, take the approval back, approve it again - and the draft says
``approved``, as if nothing ever went wrong. The wrongness of the first
approval lives in the audit chain.

The command line showed that. The **MCP** surface did not: an agent
saw ``approved`` where the person at the keyboard saw "taken back
once" - and the agent is the one that acts on it. Two more holes in
the same family: the report's draft table said only "已批准", and the
status page had no bucket at all for a draft whose approval was taken
back.

This file covers:

* the shared reading (``mas.undos.draft_histories``) is one reading;
* the MCP surface says the draft was taken back - list *and* show;
* the command line and the MCP surface say the **same** thing;
* the report's draft table names the draft and how often;
* the status page counts the drafts whose approval was taken back;
* the gate is registered and can fail.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest

from mas.drafts import approve_draft, stage_draft, undo_draft
from mas.selftest import CHECKS, EXPECTED_CHECKS
from mas.undos import draft_histories


class _Env:
    """The environment this case borrows, and gives back."""

    def __init__(self, state_dir, config_path):
        self._values = {
            "MAS_STATE_DIR": str(state_dir),
            "MAS_CONFIG_PATH": str(config_path),
        }
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


class _StateCase(unittest.TestCase):
    """A case with its own state directory, in the real layout.

    A run writes into ``<state_dir>/artifacts/<run_id>/``, so the
    workspace lives one folder below the artifacts root - and the
    report page and the status page both scan that root.
    """

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-p86-test-"))
        self.state = self.root / "state"
        self.artifacts = self.state / "artifacts"
        self.workspace = self.artifacts / "R-1"
        self.workspace.mkdir(parents=True)
        self.config_path = self.root / "config.json"
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\n", encoding="utf-8")
        self.addCleanup(self._restore)

    def _restore(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _cycle(self):
        """Approve, take back, approve again.

        The draft's own state is ``approved`` afterwards - the very
        state that hides the wrongness.
        """
        with _Env(self.state, self.config_path):
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

    def _cli(self, args):
        """Run one command of the command line and keep what it printed."""
        from mas.cli import main

        buffer = io.StringIO()
        with _Env(self.state, self.config_path):
            try:
                with contextlib.redirect_stdout(buffer):
                    main(args)
            except SystemExit:
                pass
        return buffer.getvalue()

    def _cli_json(self, args):
        text = self._cli(args)
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - a broken page is the subject
            return {}


class SharedReadingTest(_StateCase):
    """One reading, in one place."""

    def test_the_shared_reading_says_the_draft_was_taken_back(self):
        draft_id = self._cycle()
        histories = draft_histories([self.workspace], [draft_id])
        history = histories.get(draft_id) or {}
        self.assertEqual(int(history.get("approvals") or 0), 2)
        self.assertEqual(int(history.get("undos") or 0), 1)
        self.assertTrue(history.get("ever_undone"))
        self.assertEqual(int(history.get("reverted_line_count") or 0), 2)

    def test_a_draft_with_no_record_has_no_history(self):
        histories = draft_histories([self.workspace], ["D-does-not-exist"])
        self.assertEqual(histories, {})

    def test_an_empty_list_asks_nothing(self):
        self.assertEqual(draft_histories([self.workspace], []), {})

    def test_a_missing_chain_is_not_an_error(self):
        self.assertEqual(draft_histories([self.root / "nowhere"], ["D-x"]), {})


class McpSurfaceTest(_StateCase):
    """The agent sees what the person at the keyboard sees."""

    def _mcp(self, arguments):
        from mas.mcp_server import handle_tool

        with _Env(self.state, self.config_path):
            return handle_tool("mas_drafts", arguments)

    def test_the_list_says_the_draft_was_taken_back(self):
        draft_id = self._cycle()
        result = self._mcp({"workspace": str(self.artifacts), "action": "list"})
        self.assertNotIn("error", result)
        rows = [r for r in result.get("drafts") or [] if r.get("id") == draft_id]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row.get("ever_undone"))
        self.assertEqual(int(row.get("undos") or 0), 1)
        self.assertEqual(int(row.get("approvals") or 0), 2)
        self.assertEqual(int(row.get("reverted_line_count") or 0), 2)

    def test_the_show_action_says_it_too(self):
        draft_id = self._cycle()
        result = self._mcp(
            {
                "workspace": str(self.artifacts),
                "action": "show",
                "draft_id": draft_id,
            }
        )
        self.assertNotIn("error", result)
        draft = result.get("draft") or {}
        self.assertTrue(draft.get("ever_undone"))
        self.assertEqual(int(draft.get("undos") or 0), 1)

    def test_the_two_surfaces_say_the_same_thing(self):
        draft_id = self._cycle()
        cli = self._cli_json(
            ["drafts", "--workspace", str(self.artifacts), "--json"]
        )
        mcp = self._mcp({"workspace": str(self.artifacts), "action": "list"})
        cli_row = [r for r in cli.get("drafts") or [] if r.get("id") == draft_id][0]
        mcp_row = [r for r in mcp.get("drafts") or [] if r.get("id") == draft_id][0]
        for field in ("ever_undone", "undos", "approvals", "reverted_line_count"):
            self.assertEqual(
                cli_row.get(field),
                mcp_row.get(field),
                "the two surfaces disagree about %s" % field,
            )


class ReportSurfaceTest(_StateCase):
    """The report's draft table names the draft that was wrong once."""

    def test_the_report_row_says_how_often(self):
        draft_id = self._cycle()
        report = self._cli_json(["report", "--json"])
        rows = [
            r
            for r in ((report.get("drafts") or {}).get("drafts") or [])
            if r.get("draft_id") == draft_id
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0].get("taken_back") or 0), 1)
        self.assertTrue(rows[0].get("ever_undone"))

    def test_the_report_table_has_a_column_for_it(self):
        draft_id = self._cycle()
        markdown = self._cli(["report"])
        self.assertIn("被撤销", markdown)
        self.assertIn(draft_id, markdown)

    def test_a_draft_that_was_never_taken_back_shows_a_dash(self):
        with _Env(self.state, self.config_path):
            staged = stage_draft(
                self.workspace,
                "notes.txt",
                b"alpha\nbeta-agent\n",
                reason="the agent proposes this",
            )
        markdown = self._cli(["report"])
        self.assertIn("| 草稿 | 状态 | 被撤销 |", markdown)
        # The untouched draft has no undo to show.
        self.assertIn(str(staged.get("id") or ""), markdown)


class StatusSurfaceTest(_StateCase):
    """The status page counts the drafts whose approval was taken back."""

    def test_the_status_page_counts_them(self):
        draft_id = self._cycle()
        status = self._cli_json(["status", "--json"])
        drafts = status.get("drafts") or {}
        self.assertIn(draft_id, drafts.get("undone") or [])
        self.assertIn(draft_id, drafts.get("with_history") or [])

    def test_the_status_page_says_it_in_words(self):
        draft_id = self._cycle()
        text = self._cli(["status"])
        self.assertIn("批准被撤销过", text)
        self.assertIn(draft_id, text)


class WorkspaceOutsideArtifactsTest(_StateCase):
    """A draft outside the artifacts folder is counted too.

    The drafts of a machine live in three places: the artifacts
    folder, the workspace the user named, and the workspaces the
    ledger names. A history lives in the chain of the workspace the
    decision was made in - so a draft that was approved, taken back
    and approved again in a workspace *of its own* has to be counted
    by the status page as well.

    Before this case the status page read the histories from the
    artifacts folder only: the draft was listed, and the taken-back
    approval was not counted. The page showed the change and hid the
    mistake.
    """

    def test_a_draft_outside_the_artifacts_folder_is_still_counted(self):
        elsewhere = self.root / "somewhere-else"
        elsewhere.mkdir(parents=True)
        target = elsewhere / "notes.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        # The user named this workspace, so it belongs to the machine.
        self.config_path.write_text(
            json.dumps({"workspace": str(elsewhere)}),
            encoding="utf-8",
        )

        with _Env(self.state, self.config_path):
            staged = stage_draft(
                elsewhere,
                "notes.txt",
                b"alpha\nbeta-agent\n",
                reason="the agent proposes this",
            )
            draft_id = str(staged.get("id") or "")
            approve_draft(elsewhere, draft_id, note="looks fine")
            undo_draft(elsewhere, draft_id, note="approved the wrong thing")
            approve_draft(elsewhere, draft_id, note="corrected")

        status = self._cli_json(["status", "--json"])
        drafts = status.get("drafts") or {}
        self.assertIn(draft_id, drafts.get("undone") or [])

        text = self._cli(["status"])
        self.assertIn("批准被撤销过", text)
        self.assertIn(draft_id, text)


class GateTest(unittest.TestCase):
    """The forty-seventh gate is registered and can fail."""

    def test_the_gate_is_registered(self):
        names = [getattr(check, "CHECK_NAME", "") for check in CHECKS]
        self.assertIn("a_taken_back_approval_is_visible_everywhere", names)
        self.assertEqual(EXPECTED_CHECKS, len(CHECKS))

    def test_the_gate_holds(self):
        from mas.selftest_draft_undo_visibility import (
            check_a_taken_back_approval_is_visible_everywhere,
        )

        result = check_a_taken_back_approval_is_visible_everywhere(iterations=1)
        self.assertEqual(result["failures"], [])
        self.assertTrue(result["ok"])

    def test_the_gate_notices_a_broken_wiring(self):
        """The gate must be able to fail.

        The break is on the **wiring**: the MCP surface stops reading
        the audit chain. Breaking the shared function instead would
        make every surface wrong in the same way, and the two surfaces
        would agree - the gate would see nothing.
        """
        import mas.mcp_server as mcp_module
        from mas.selftest_draft_undo_visibility import (
            check_a_taken_back_approval_is_visible_everywhere,
        )

        real = mcp_module.draft_histories

        def _no_history(roots, draft_ids, _real=real):
            return {}

        mcp_module.draft_histories = _no_history
        try:
            result = check_a_taken_back_approval_is_visible_everywhere(iterations=1)
        finally:
            mcp_module.draft_histories = real

        self.assertFalse(result["ok"])
        self.assertTrue(result["failures"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

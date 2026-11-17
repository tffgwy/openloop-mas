# -*- coding: utf-8 -*-
"""v0.7.77 tests: an approved change can be taken back.

v0.7.72 made the approval the only way a change reaches a file, and
v0.7.76 kept the baseline the draft was made against. But the
approval was still a one-way door: once the human approved the wrong
thing, the only way back was to edit the file by hand - and the
baseline that would have made the undo exact was sitting there
unused.

v0.7.77 closes that loop:

* an undo puts the file back *exactly* - byte for byte;
* the draft survives the undo, so the same change can be approved
  again once the reason it was undone is understood;
* an undo of a draft that was never approved is refused;
* an undo after the file moved is refused, because undoing then would
  throw the newer work away - and the refusal names what it would
  cost;
* the command line and the MCP tool can both do it.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import argparse
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.drafts import (
    DRAFT_APPROVED,
    DRAFT_PENDING,
    DRAFT_UNDONE,
    DraftUndoConflict,
    approve_draft,
    draft_undo_plan,
    stage_draft,
    undo_draft,
)
from mas.mcp_server import TOOLS, handle_tool
from mas.selftest import CHECKS


def _tool_names():
    return [str(t.get("name") or "") for t in TOOLS if t.get("name")]


class UndoBasicsTests(unittest.TestCase):
    """The undo puts the file back, and the draft survives it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.original = "alpha\nbeta\ngamma\n"
        self.target.write_text(self.original, encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def _stage(self, content=b"alpha\nbeta-agent\ngamma\n"):
        return stage_draft(self.workspace, "notes.txt", content)

    def test_the_undo_puts_the_file_back_byte_for_byte(self):
        staged = self._stage()
        approve_draft(self.workspace, staged["id"])
        self.assertEqual(
            self.target.read_text(encoding="utf-8"), "alpha\nbeta-agent\ngamma\n"
        )
        undo_draft(self.workspace, staged["id"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)

    def test_the_undo_is_exact_for_bytes_that_are_not_text(self):
        blob = self.workspace / "blob.bin"
        payload = bytes(range(256))
        blob.write_bytes(payload)
        staged = stage_draft(self.workspace, "blob.bin", b"other bytes")
        approve_draft(self.workspace, staged["id"])
        undo_draft(self.workspace, staged["id"])
        self.assertEqual(blob.read_bytes(), payload)

    def test_the_draft_survives_the_undo(self):
        staged = self._stage()
        approve_draft(self.workspace, staged["id"])
        undone = undo_draft(self.workspace, staged["id"], note="wrong call")
        self.assertEqual(undone.get("state"), DRAFT_UNDONE)
        self.assertEqual(undone.get("undo_note"), "wrong call")
        self.assertIsNotNone(undone.get("undone_at"))

    def test_the_same_change_can_be_approved_again(self):
        staged = self._stage()
        approve_draft(self.workspace, staged["id"])
        undo_draft(self.workspace, staged["id"])
        again = approve_draft(self.workspace, staged["id"])
        self.assertEqual(again.get("state"), DRAFT_APPROVED)
        self.assertEqual(
            self.target.read_text(encoding="utf-8"), "alpha\nbeta-agent\ngamma\n"
        )

    def test_an_unapproved_draft_cannot_be_undone(self):
        staged = self._stage()
        with self.assertRaises(ValueError):
            undo_draft(self.workspace, staged["id"])
        # Nothing was written.
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)

    def test_an_unknown_draft_is_refused(self):
        with self.assertRaises(KeyError):
            undo_draft(self.workspace, "D-nope")

    def test_the_draft_stays_pending_when_it_was_never_approved(self):
        staged = self._stage()
        with self.assertRaises(ValueError):
            undo_draft(self.workspace, staged["id"])
        from mas.drafts import get_draft

        self.assertEqual(get_draft(self.workspace, staged["id"]).get("state"), DRAFT_PENDING)

    def test_a_draft_without_a_baseline_cannot_be_undone(self):
        # A draft made before v0.7.76 kept no baseline, so there is
        # nothing exact to go back to.
        staged = self._stage()
        approve_draft(self.workspace, staged["id"])
        base = self.workspace / ".mas-drafts" / ("%s.base" % staged["id"])
        base.unlink()
        with self.assertRaises(ValueError):
            undo_draft(self.workspace, staged["id"])

    def test_the_undo_refuses_to_leave_the_workspace(self):
        staged = self._stage()
        approve_draft(self.workspace, staged["id"])
        draft_file = self.workspace / ".mas-drafts" / ("%s.json" % staged["id"])
        data = json.loads(draft_file.read_text(encoding="utf-8"))
        data["absolute"] = str(self.root / "outside.txt")
        draft_file.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            undo_draft(self.workspace, staged["id"])


class UndoConflictTests(unittest.TestCase):
    """An undo after the file moved is refused, and says what it costs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_undo_is_refused_after_the_file_moved(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        self.target.write_text(
            "alpha\nbeta-agent\nhuman-after-approval\n", encoding="utf-8"
        )
        with self.assertRaises(DraftUndoConflict):
            undo_draft(self.workspace, staged["id"])
        # The newer work survives the refusal.
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "alpha\nbeta-agent\nhuman-after-approval\n",
        )

    def test_the_plan_names_the_lines_the_undo_would_cost(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        self.target.write_text(
            "alpha\nbeta-agent\nhuman-after-approval\n", encoding="utf-8"
        )
        plan = draft_undo_plan(self.workspace, staged)
        self.assertEqual(plan.get("reason"), "file_changed")
        self.assertFalse(plan.get("ready"))
        self.assertEqual(plan.get("would_lose"), ["human-after-approval"])
        self.assertEqual(plan.get("would_lose_count"), 1)

    def test_a_forced_undo_goes_through(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        self.target.write_text(
            "alpha\nbeta-agent\nhuman-after-approval\n", encoding="utf-8"
        )
        undo_draft(self.workspace, staged["id"], force=True)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "alpha\nbeta\n")

    def test_the_plan_of_a_ready_undo_is_ready(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        plan = draft_undo_plan(self.workspace, staged)
        self.assertTrue(plan.get("ready"))
        self.assertEqual(plan.get("reason"), "ready")
        self.assertEqual(plan.get("would_lose"), [])

    def test_the_plan_reads_the_state_from_the_disk(self):
        # The caller may hold a copy of the draft from before it was
        # approved. The plan has to use the truth on the disk.
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        stale = dict(staged)
        approve_draft(self.workspace, staged["id"])
        plan = draft_undo_plan(self.workspace, stale)
        self.assertEqual(plan.get("state"), DRAFT_APPROVED)
        self.assertTrue(plan.get("ready"))

    def test_the_plan_of_a_missing_file_says_so(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        self.target.unlink()
        plan = draft_undo_plan(self.workspace, staged)
        self.assertEqual(plan.get("reason"), "file_missing")
        self.assertFalse(plan.get("ready"))

    def test_the_plan_of_a_pending_draft_says_not_approved(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        plan = draft_undo_plan(self.workspace, staged)
        self.assertEqual(plan.get("reason"), "not_approved")
        self.assertFalse(plan.get("ready"))


class UndoCommandLineTests(unittest.TestCase):
    """The command line can undo, and it says so in plain words."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def _run(self, **kwargs):
        from mas.cli import cmd_drafts

        args = argparse.Namespace(
            action="undo",
            draft_id="",
            workspace=str(self.workspace),
            state=None,
            note="",
            token="",
            ticket_action="approve",
            force=False,
            json=False,
        )
        for key, value in kwargs.items():
            setattr(args, key, value)
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_drafts(args)
        return code, out.getvalue(), err.getvalue()

    def test_the_command_line_undoes(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        code, out, _err = self._run(draft_id=staged["id"])
        self.assertEqual(code, 0)
        self.assertIn("已撤销", out)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "alpha\nbeta\n")

    def test_the_command_line_refuses_after_the_file_moved(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        self.target.write_text(
            "alpha\nbeta-agent\nhuman-after-approval\n", encoding="utf-8"
        )
        code, _out, err = self._run(draft_id=staged["id"])
        self.assertEqual(code, 2)
        self.assertIn("撤销被拒", err)
        self.assertIn("human-after-approval", err)
        # The file keeps the newer work.
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "alpha\nbeta-agent\nhuman-after-approval\n",
        )

    def test_the_command_line_refuses_an_unapproved_draft(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        code, _out, err = self._run(draft_id=staged["id"])
        self.assertEqual(code, 2)
        self.assertIn("没什么可撤销的", err)

    def test_the_command_line_reports_an_unknown_draft(self):
        code, _out, err = self._run(draft_id="D-nope")
        self.assertEqual(code, 2)
        self.assertIn("找不到这个草稿", err)

    def test_the_command_line_answers_in_json(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        code, out, _err = self._run(draft_id=staged["id"], json=True)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload.get("draft", {}).get("state"), DRAFT_UNDONE)
        self.assertIn("已撤销", str(payload.get("message") or ""))


class UndoMcpTests(unittest.TestCase):
    """An agent can undo too, and it is held to the same rule."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_tool_is_advertised(self):
        self.assertIn("mas_draft_undo", _tool_names())

    def test_an_agent_without_a_token_cannot_undo(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        result = handle_tool(
            "mas_draft_undo",
            {"draft_id": staged["id"], "workspace": str(self.workspace)},
        )
        self.assertEqual(result.get("error"), "human_token_required")
        # The file was not touched.
        self.assertEqual(
            self.target.read_text(encoding="utf-8"), "alpha\nbeta-agent\n"
        )

    def test_an_agent_with_the_token_can_undo(self):
        from mas.drafts import ensure_human_token

        token = ensure_human_token(self.root / "state")
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        result = handle_tool(
            "mas_draft_undo",
            {
                "draft_id": staged["id"],
                "workspace": str(self.workspace),
                "token": token,
            },
        )
        self.assertNotIn("error", result)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "alpha\nbeta\n")
        self.assertEqual(result.get("draft", {}).get("state"), DRAFT_UNDONE)

    def test_an_agent_is_told_what_the_undo_would_cost(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        self.target.write_text(
            "alpha\nbeta-agent\nhuman-after-approval\n", encoding="utf-8"
        )
        result = handle_tool(
            "mas_draft_undo",
            {"draft_id": staged["id"], "workspace": str(self.workspace)},
        )
        self.assertEqual(result.get("error"), "undo_conflict")
        self.assertEqual(result.get("would_lose"), ["human-after-approval"])
        self.assertEqual(result.get("would_lose_count"), 1)

    def test_the_status_sees_the_undone_drafts(self):
        from mas.drafts import ensure_human_token

        token = ensure_human_token(self.root / "state")
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\n")
        approve_draft(self.workspace, staged["id"])
        handle_tool(
            "mas_draft_undo",
            {
                "draft_id": staged["id"],
                "workspace": str(self.workspace),
                "token": token,
            },
        )
        status = handle_tool("mas_status", {"workspace": str(self.workspace)})
        self.assertEqual(status.get("undone_draft_count"), 1)
        self.assertEqual(
            status.get("undone_drafts", [])[0].get("draft_id"), staged["id"]
        )


class UndoGateTests(unittest.TestCase):
    """The twentieth gate is registered, and it is green."""

    def test_the_gate_is_registered(self):
        names = [
            str(getattr(check, "CHECK_NAME", "") or getattr(check, "__name__", ""))
            for check in CHECKS
        ]
        self.assertIn("undo_guard", names)
        self.assertEqual(len(CHECKS), 20)

    def test_the_gate_is_green(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=2, only=["undo_guard"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["checks"]), 1)


if __name__ == "__main__":
    unittest.main()

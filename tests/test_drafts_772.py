# -*- coding: utf-8 -*-
"""v0.7.72 tests for the draft layer.

The MOKI rule - "the change goes into a draft first, and only a
human confirmation writes it back to the original file" - became an
engine rule in v0.7.72. These tests pin the three load-bearing
properties down:

* a write over an *existing* file becomes a draft and leaves the
  original byte-identical;
* a write that *creates* a file is still performed right away;
* approving writes the change back, rejecting changes nothing at all.

The CLI is tested too, because the approval is a human action and the
human reaches it through the command line.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from mas.drafts import (
    DRAFT_APPROVED,
    DRAFT_PENDING,
    DRAFT_REJECTED,
    approve_draft,
    draft_overlay,
    get_draft,
    list_drafts,
    reject_draft,
    stage_draft,
)
from mas.engine import LoopEngine


class _StubProvider:
    """A provider that never answers - the draft layer needs no model."""

    name = "stub"

    def complete(self, *, system="", prompt="", role="worker",
                 expect_json=False, max_tokens=4000):
        return {"text": "", "usage": {"total_tokens": 0}}


def _engine(workspace: pathlib.Path, policy: str = "draft") -> LoopEngine:
    engine = LoopEngine(
        {
            "state_dir": str(workspace / "state"),
            "edit_policy": policy,
            "workspace": str(workspace),
        },
        provider=_StubProvider(),
    )
    engine._run_id = "R-test-drafts"
    engine._write_workspace()
    return engine


def _block(path: str, body: str) -> str:
    return '<<<MAS:FILE path="%s">>>>\n%s\n<<<MAS:END FILE>>>\n' % (path, body)


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self._tmp.name)
        self.existing = self.workspace / "notes.txt"
        self.existing.write_bytes(b"the human's own words")

    def tearDown(self):
        self._tmp.cleanup()

    def test_stage_draft_leaves_the_original_alone(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        self.assertEqual(draft["state"], DRAFT_PENDING)
        self.assertEqual(self.existing.read_bytes(), b"the human's own words")

    def test_stage_draft_refuses_a_file_that_does_not_exist(self):
        # A draft for a missing file is not an edit, it is a creation -
        # and a creation is what the normal write path is for.
        with self.assertRaises(ValueError):
            stage_draft(self.workspace, "not-there.txt", b"content")

    def test_stage_draft_refuses_a_path_outside_the_workspace(self):
        with self.assertRaises(ValueError):
            stage_draft(self.workspace, "../escape.txt", b"content")

    def test_the_draft_carries_a_diff(self):
        draft = stage_draft(self.workspace, "notes.txt", b"a different line")
        self.assertTrue(draft["diff"])
        self.assertTrue(draft["changed"])
        self.assertEqual(draft["path"], "notes.txt")
        self.assertEqual(draft["before_bytes"], len(b"the human's own words"))

    def test_approve_writes_the_change_back(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        approved = approve_draft(self.workspace, draft["id"])
        self.assertEqual(approved["state"], DRAFT_APPROVED)
        self.assertEqual(self.existing.read_bytes(), b"the loop wants this")

    def test_reject_changes_nothing(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        rejected = reject_draft(self.workspace, draft["id"])
        self.assertEqual(rejected["state"], DRAFT_REJECTED)
        self.assertEqual(self.existing.read_bytes(), b"the human's own words")

    def test_a_draft_cannot_be_approved_twice(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        approve_draft(self.workspace, draft["id"])
        with self.assertRaises(ValueError):
            approve_draft(self.workspace, draft["id"])

    def test_list_drafts_filters_by_state(self):
        first = stage_draft(self.workspace, "notes.txt", b"one")
        second = stage_draft(self.workspace, "notes.txt", b"two")
        approve_draft(self.workspace, first["id"])
        pending = [d["id"] for d in list_drafts(self.workspace, DRAFT_PENDING)]
        self.assertEqual(pending, [second["id"]])
        self.assertEqual(len(list_drafts(self.workspace, DRAFT_APPROVED)), 1)

    def test_get_draft_of_an_unknown_id_is_none(self):
        self.assertIsNone(get_draft(self.workspace, "D-nope"))

    def test_the_overlay_carries_the_drafted_bytes(self):
        stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        overlay = draft_overlay(self.workspace)
        found = [data for data in overlay.values() if b"the loop wants this" in data]
        self.assertTrue(found)

    def test_the_overlay_ignores_a_resolved_draft(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        approve_draft(self.workspace, draft["id"])
        self.assertEqual(draft_overlay(self.workspace), {})


class EnginePolicyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self._tmp.name)
        self.existing = self.workspace / "notes.txt"
        self.existing.write_bytes(b"the human's own words")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_default_policy_drafts_an_edit(self):
        engine = _engine(self.workspace)
        applied = engine._apply_writes(_block("notes.txt", "the loop wants this"))
        self.assertTrue(applied["drafts"])
        self.assertFalse(applied["written"])
        self.assertEqual(self.existing.read_bytes(), b"the human's own words")

    def test_a_new_file_is_still_written_right_away(self):
        engine = _engine(self.workspace)
        applied = engine._apply_writes(_block("fresh.md", "a brand new file"))
        self.assertTrue(applied["written"])
        self.assertFalse(applied["drafts"])
        self.assertEqual((self.workspace / "fresh.md").read_bytes(), b"a brand new file")

    def test_the_overwrite_policy_keeps_the_old_behaviour(self):
        engine = _engine(self.workspace, policy="overwrite")
        applied = engine._apply_writes(_block("notes.txt", "the loop wants this"))
        self.assertFalse(applied["drafts"])
        self.assertTrue(applied["written"])
        self.assertEqual(self.existing.read_bytes(), b"the loop wants this")

    def test_an_unknown_policy_falls_back_to_draft(self):
        engine = _engine(self.workspace, policy="nonsense")
        self.assertEqual(engine._edit_policy(), "draft")

    def test_the_engine_refuses_to_write_outside_the_workspace(self):
        engine = _engine(self.workspace)
        applied = engine._apply_writes(_block("../escape.txt", "content"))
        self.assertTrue(applied["errors"])
        self.assertFalse((self.workspace.parent / "escape.txt").exists())


class DraftCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self._tmp.name)
        self.existing = self.workspace / "notes.txt"
        self.existing.write_bytes(b"the human's own words")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, argv):
        from mas import cli

        buffer = io.StringIO()
        args = cli.build_parser().parse_args(argv)
        with redirect_stdout(buffer):
            code = args.func(args)
        return code, buffer.getvalue()

    def test_list_shows_the_pending_drafts(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        code, out = self._run(
            ["drafts", "list", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["drafts"][0]["id"], draft["id"])

    def test_show_prints_the_diff(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        code, out = self._run(
            ["drafts", "show", draft["id"], "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["draft"]["diff"])

    def test_approve_writes_the_file_back(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        code, _ = self._run(
            ["drafts", "approve", draft["id"], "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.existing.read_bytes(), b"the loop wants this")

    def test_reject_leaves_the_file_alone(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        code, _ = self._run(
            ["drafts", "reject", draft["id"], "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.existing.read_bytes(), b"the human's own words")

    def test_an_unknown_workspace_is_reported(self):
        code, out = self._run(
            ["drafts", "list", "--workspace", str(self.workspace / "nope"), "--json"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"], "workspace_not_found")

    def test_a_missing_default_workspace_is_not_an_error(self):
        # The default workspace does not exist yet on a machine where no
        # run has produced anything. That is not a failure - a first
        # time reader must not be met with an error code.
        import os

        from mas import cli

        saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.workspace / "empty-state")
        try:
            buffer = io.StringIO()
            args = cli.build_parser().parse_args(["drafts", "--json"])
            with redirect_stdout(buffer):
                code = args.func(args)
        finally:
            if saved is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = saved
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["drafts"], [])

    def test_an_unknown_draft_is_reported(self):
        code, out = self._run(
            ["drafts", "show", "D-nope", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"], "draft_not_found")

    def test_the_drafts_of_a_sub_folder_are_found(self):
        # A run writes into "<artifacts>/<run_id>/", so the drafts live
        # one folder deeper than the default workspace.
        run_folder = self.workspace / "R-run-1"
        run_folder.mkdir()
        (run_folder / "notes.txt").write_bytes(b"the human's own words")
        draft = stage_draft(run_folder, "notes.txt", b"the loop wants this")
        code, out = self._run(
            ["drafts", "list", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 1)
        code, _ = self._run(
            ["drafts", "approve", draft["id"], "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual((run_folder / "notes.txt").read_bytes(), b"the loop wants this")


if __name__ == "__main__":
    unittest.main()

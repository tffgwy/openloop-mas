# -*- coding: utf-8 -*-
"""v0.8.7: a taken-back draft is still a change that waits for a human.

The regression this file locks down is the one v0.8.7 fixed: a draft
whose approval was taken back (``state == "undone"``) is a **live
proposal**. The undo wrote the baseline back to the disk and the
draft can be approved again, so the change is waiting for a person -
and every surface has to say so.

Before v0.8.7 the queue read only ``pending``, so an undone draft
fell out of every count, the closure verdict called the loop closed,
and the machine-check overlay judged the *baseline* the undo had just
written back instead of the bytes the loop proposes.
"""

import json
import pathlib
import tempfile
import unittest

from mas.drafts import (
    DRAFT_UNDONE,
    approve_draft,
    draft_overlay,
    get_draft,
    list_drafts,
    stage_draft,
    undo_draft,
)
from mas.drafts_reading import draft_reading, is_waiting_for_human


class _Workspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self._tmp.name)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _undone_draft(self):
        """A draft that was approved and then taken back.

        The draft is **read back** from its file at the end: the
        decisions write the new state to the disk, and the dict
        ``stage_draft`` returned is the snapshot of the moment it was
        proposed. A reader of the queue reads the file, so the test
        has to read the file too.
        """
        draft = stage_draft(
            self.workspace,
            "notes.txt",
            "alpha\nbeta-agent\n".encode("utf-8"),
            reason="the agent proposes this",
        )
        approve_draft(self.workspace, draft["id"], note="looks fine")
        undo_draft(self.workspace, draft["id"], note="approved the wrong thing")
        return get_draft(self.workspace, draft["id"]) or draft


class TheWaitingReadingTest(unittest.TestCase):
    """One reading of "does this draft still wait for a person"."""

    def test_pending_waits_and_approved_does_not(self):
        self.assertTrue(is_waiting_for_human("pending"))
        self.assertTrue(is_waiting_for_human("undone"))
        self.assertFalse(is_waiting_for_human("approved"))
        self.assertFalse(is_waiting_for_human("rejected"))
        self.assertFalse(is_waiting_for_human(""))
        self.assertFalse(is_waiting_for_human(None))

    def test_an_undone_draft_can_be_approved_again(self):
        # The premise of the whole reading: an undone draft is not a
        # dead end. If this stops being true, the queue may stop
        # waiting for it.
        workspace = pathlib.Path(tempfile.mkdtemp())
        try:
            target = workspace / "notes.txt"
            target.write_text("alpha\n", encoding="utf-8")
            draft = stage_draft(workspace, "notes.txt", b"beta\n")
            approve_draft(workspace, draft["id"])
            undo_draft(workspace, draft["id"])
            self.assertEqual(DRAFT_UNDONE, list_drafts(workspace)[0]["state"])
            approve_draft(workspace, draft["id"])
            self.assertEqual(DRAFT_APPROVED_STATE, list_drafts(workspace)[0]["state"])
        finally:
            import shutil

            shutil.rmtree(workspace, ignore_errors=True)


DRAFT_APPROVED_STATE = "approved"


class TheOverlayTest(_Workspace):
    """The machine checks must judge the bytes the loop proposes."""

    def test_the_overlay_carries_a_pending_draft(self):
        stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        overlay = draft_overlay(self.workspace)
        found = [data for data in overlay.values() if b"the loop wants this" in data]
        self.assertTrue(found)

    def test_the_overlay_carries_an_undone_draft(self):
        draft = self._undone_draft()
        overlay = draft_overlay(self.workspace)
        found = [
            data
            for data in overlay.values()
            if b"beta-agent" in data
        ]
        self.assertTrue(
            found,
            "the machine checks must see the proposed bytes of a "
            "taken-back draft, not the baseline the undo wrote back",
        )
        self.assertTrue(str(draft["id"]))

    def test_the_overlay_does_not_carry_the_baseline_of_an_undone_draft(self):
        self._undone_draft()
        overlay = draft_overlay(self.workspace)
        for data in overlay.values():
            self.assertNotIn(b"alpha\nbeta\n", data)

    def test_an_approved_draft_is_not_in_the_overlay(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        approve_draft(self.workspace, draft["id"])
        self.assertEqual(draft_overlay(self.workspace), {})


class TheDraftReadingTest(_Workspace):
    """The queue must count a taken-back draft as waiting."""

    def _reading(self, drafts):
        return draft_reading([], drafts)

    def test_an_undone_draft_is_counted_as_waiting(self):
        draft = self._undone_draft()
        reading = self._reading([draft])
        self.assertIn(draft["id"], reading["pending"])
        self.assertEqual(1, reading["pending_count"])
        self.assertIn(draft["id"], reading["undone_waiting"])
        self.assertEqual(1, reading["undone_waiting_count"])

    def test_an_undone_draft_is_not_in_the_other_bucket(self):
        draft = self._undone_draft()
        reading = self._reading([draft])
        self.assertNotIn(draft["id"], reading["other"])

    def test_an_approved_draft_is_not_waiting(self):
        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        approve_draft(self.workspace, draft["id"])
        reading = self._reading([get_draft(self.workspace, draft["id"])])
        self.assertEqual(0, reading["pending_count"])
        self.assertEqual(1, reading["approved_count"])

    def test_a_rejected_draft_is_not_waiting(self):
        from mas.drafts import reject_draft

        draft = stage_draft(self.workspace, "notes.txt", b"the loop wants this")
        reject_draft(self.workspace, draft["id"])
        reading = self._reading([get_draft(self.workspace, draft["id"])])
        self.assertEqual(0, reading["pending_count"])

    def test_a_closed_run_with_an_undone_draft_is_found(self):
        from mas.drafts_reading import ledger_drafts

        draft = self._undone_draft()
        rows = [
            {
                "role": "draft",
                "run_id": "R-unit",
                "task_id": "T-unit",
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": 1,
                "drafts": [
                    {
                        "id": draft["id"],
                        "path": draft["path"],
                        "absolute": draft["absolute"],
                        "bytes": 10,
                        "changed": True,
                    }
                ],
            },
            {
                "role": "run_end",
                "run_id": "R-unit",
                "closed": True,
                "status": "closed",
                "goal": "change a file",
                "ts": "2026-01-01T00:01:00+00:00",
            },
        ]
        self.assertEqual(1, len(ledger_drafts(rows)))
        reading = draft_reading(rows, [draft])
        self.assertEqual(1, reading["closed_with_pending_count"])
        self.assertFalse(reading["ok"])


class TheSurfaceTest(_Workspace):
    """The pages have to say that the change still waits."""

    def setUp(self):
        super().setUp()
        import os

        self._root = pathlib.Path(tempfile.mkdtemp())
        self._saved = {
            name: os.environ.get(name)
            for name in ("MAS_STATE_DIR", "MAS_CONFIG_PATH")
        }
        os.environ["MAS_STATE_DIR"] = str(self._root / "state")
        os.environ["MAS_CONFIG_PATH"] = str(self._root / "config.json")

    def tearDown(self):
        import os
        import shutil

        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self._root, ignore_errors=True)
        super().tearDown()

    def _json_of(self, args):
        import contextlib
        import io

        from mas.cli import main

        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                main(args)
        except SystemExit:
            pass
        try:
            return json.loads(buffer.getvalue())
        except Exception:  # noqa: BLE001 - a broken page is the subject
            return {}

    def test_the_mcp_surface_says_the_draft_is_waiting(self):
        from mas.mcp_server import handle_tool

        draft = self._undone_draft()
        payload = handle_tool(
            "mas_drafts", {"workspace": str(self.workspace), "action": "list"}
        )
        rows = payload.get("drafts") or []
        found = [row for row in rows if row.get("id") == draft["id"]]
        self.assertTrue(found)
        self.assertTrue(found[0].get("waiting_for_human"))
        self.assertIn(draft["id"], payload.get("waiting_draft_ids") or [])
        self.assertGreaterEqual(int(payload.get("undone_waiting_count") or 0), 1)

    def test_the_mcp_surface_shows_one_draft(self):
        from mas.mcp_server import handle_tool

        draft = self._undone_draft()
        payload = handle_tool(
            "mas_drafts", {"workspace": str(self.workspace), "action": "show", "draft_id": draft["id"]}
        )
        self.assertTrue((payload.get("draft") or {}).get("waiting_for_human"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

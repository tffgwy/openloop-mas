"""The lost-update guard: two people, one file.

The gate in ``mas/selftest.py`` proves the rule holds. These tests
prove the rule is what the code says it is - each one names the way
the guard could be broken and checks that the break is caught.
"""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mas.drafts import (  # noqa: E402
    DraftConflict,
    approve_draft,
    draft_conflict,
    file_digest,
    stage_draft,
)
from mas.mcp_server import handle_tool  # noqa: E402


class ConflictTests(unittest.TestCase):
    """The clash between a waiting draft and a file that moved."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-conflict-"))
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("v1", encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _stage(self, content=b"v2-agent"):
        return stage_draft(self.workspace, "notes.txt", content, reason="agent")

    def test_a_draft_remembers_the_hash_it_was_made_against(self):
        draft = self._stage()
        self.assertEqual(len(draft.get("before_sha256") or ""), 64)

    def test_a_file_that_did_not_move_is_not_a_clash(self):
        draft = self._stage()
        clash = draft_conflict(self.workspace, draft)
        self.assertFalse(clash.get("conflict"))
        self.assertEqual(clash.get("reason"), "unchanged")

    def test_a_file_that_moved_is_a_clash(self):
        draft = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        clash = draft_conflict(self.workspace, draft)
        self.assertTrue(clash.get("conflict"))
        self.assertEqual(clash.get("reason"), "file_changed")
        self.assertNotEqual(
            clash.get("expected_sha256"), clash.get("current_sha256")
        )

    def test_a_missing_file_hashes_to_nothing(self):
        self.assertEqual(file_digest(self.root / "nope.txt"), "")

    def test_approving_over_a_moved_file_is_refused(self):
        draft = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        with self.assertRaises(DraftConflict):
            approve_draft(self.workspace, draft["id"])

    def test_the_refusal_does_not_touch_the_disk(self):
        draft = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        with self.assertRaises(DraftConflict):
            approve_draft(self.workspace, draft["id"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v3-human")

    def test_the_refusal_keeps_the_draft_waiting(self):
        draft = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        with self.assertRaises(DraftConflict):
            approve_draft(self.workspace, draft["id"])
        from mas.drafts import get_draft

        after = get_draft(self.workspace, draft["id"])
        self.assertEqual(after.get("state"), "pending")

    def test_force_writes_over_the_newer_work(self):
        draft = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        approve_draft(self.workspace, draft["id"], force=True)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v2-agent")

    def test_an_untouched_draft_is_approved_as_before(self):
        draft = self._stage()
        approve_draft(self.workspace, draft["id"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v2-agent")


class McpConflictTests(unittest.TestCase):
    """The same rule, spoken by the MCP server."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-mcp-conflict-"))
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("v1", encoding="utf-8")
        self._previous = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        import shutil

        if self._previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._previous
        shutil.rmtree(self.root, ignore_errors=True)

    def _stage(self):
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "v2-agent",
                "reason": "agent",
                "workspace": str(self.workspace),
            },
        )
        return str((staged.get("draft") or {}).get("id") or "")

    def _token(self):
        """The human token of this machine - the door opens for it."""
        from mas.drafts import ensure_human_token

        return ensure_human_token(pathlib.Path(os.environ["MAS_STATE_DIR"]))

    def test_the_server_refuses_a_moved_file(self):
        draft_id = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "token": self._token(),
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(result.get("error"), "draft_conflict")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v3-human")

    def test_the_server_reports_both_hashes(self):
        draft_id = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "token": self._token(),
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(len(result.get("expected_sha256") or ""), 64)
        self.assertEqual(len(result.get("current_sha256") or ""), 64)
        self.assertNotEqual(result.get("expected_sha256"), result.get("current_sha256"))

    def test_the_server_writes_when_told_to_force(self):
        draft_id = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "token": self._token(),
                "force": True,
                "workspace": str(self.workspace),
            },
        )
        self.assertNotEqual(result.get("error"), "draft_conflict")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v2-agent")


class TicketSurvivesAClashTests(unittest.TestCase):
    """A one-shot ticket must not be spent on a refused approval."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-ticket-clash-"))
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("v1", encoding="utf-8")
        self._previous = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        import shutil

        if self._previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._previous
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_clash_does_not_burn_the_ticket(self):
        import json

        from mas.drafts import ensure_human_token, issue_ticket, ticket_store_path

        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "v2-agent",
                "reason": "agent",
                "workspace": str(self.workspace),
            },
        )
        draft_id = str((staged.get("draft") or {}).get("id") or "")
        state = pathlib.Path(os.environ["MAS_STATE_DIR"])
        token = ensure_human_token(state)
        signed = issue_ticket(state, draft_id, action="approve", human_token=token)
        self.target.write_text("v3-human", encoding="utf-8")

        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "ticket": str(signed.get("secret") or ""),
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(result.get("error"), "draft_conflict")

        store = json.loads(ticket_store_path(state).read_text(encoding="utf-8"))
        record = store.get(str(signed.get("id") or ""))
        self.assertFalse(record.get("used"))

    def test_the_same_ticket_still_works_after_the_clash(self):
        from mas.drafts import ensure_human_token, issue_ticket

        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "v2-agent",
                "reason": "agent",
                "workspace": str(self.workspace),
            },
        )
        draft_id = str((staged.get("draft") or {}).get("id") or "")
        state = pathlib.Path(os.environ["MAS_STATE_DIR"])
        token = ensure_human_token(state)
        signed = issue_ticket(state, draft_id, action="approve", human_token=token)
        self.target.write_text("v3-human", encoding="utf-8")

        handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "ticket": str(signed.get("secret") or ""),
                "workspace": str(self.workspace),
            },
        )
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "ticket": str(signed.get("secret") or ""),
                "force": True,
                "workspace": str(self.workspace),
            },
        )
        self.assertNotEqual(result.get("error"), "draft_conflict")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v2-agent")


class CliConflictTests(unittest.TestCase):
    """The command line says the same thing the server says."""

    def setUp(self):
        import argparse

        from mas.cli import cmd_drafts

        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-cli-conflict-"))
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("v1", encoding="utf-8")
        self.cmd_drafts = cmd_drafts
        self.argparse = argparse

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _args(self, action, draft_id="", **extra):
        values = {
            "action": action,
            "draft_id": draft_id,
            "workspace": str(self.workspace),
            "state": None,
            "note": "",
            "token": "",
            "ticket_action": "approve",
            "force": False,
            "json": True,
        }
        values.update(extra)
        return self.argparse.Namespace(**values)

    def _stage(self):
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "v2-agent",
                "reason": "agent",
                "workspace": str(self.workspace),
            },
        )
        return str((staged.get("draft") or {}).get("id") or "")

    def test_the_command_line_refuses_a_moved_file(self):
        draft_id = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        code = self.cmd_drafts(self._args("approve", draft_id))
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v3-human")

    def test_the_command_line_writes_when_told_to_force(self):
        draft_id = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        code = self.cmd_drafts(self._args("approve", draft_id, force=True))
        self.assertEqual(code, 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "v2-agent")

    def test_the_conflict_action_lists_the_clash(self):
        draft_id = self._stage()
        self.target.write_text("v3-human", encoding="utf-8")
        code = self.cmd_drafts(self._args("conflict"))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

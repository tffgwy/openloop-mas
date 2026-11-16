# -*- coding: utf-8 -*-
"""v0.7.73 tests for the MCP draft layer and the human token.

v0.7.72 made "the change goes into a draft first" an engine rule.
That rule only governed the engine: an agent holding the MCP tools
could still reach straight into the files. v0.7.73 splits the draft
layer in two halves - *proposing* and *approving* - and only the second
one needs the human token.

These tests pin down:

* an agent can stage a change over MCP and the original keeps every
  byte;
* approving needs the human token - no token and a wrong token are
  both refused, and the file does not move;
* the right token writes the change back, a rejection changes nothing;
* the MCP server can index a workspace and preview a file, so the agent
  can read before it proposes;
* the CLI hands out the token, and the token is stable.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import io
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from mas.drafts import (
    ensure_human_token,
    read_human_token,
    verify_human_token,
)
from mas.mcp_server import TOOLS, handle_tool


def _tool_names():
    return [str(t.get("name") or "") for t in TOOLS if t.get("name")]


class McpDraftToolTests(unittest.TestCase):
    """The MCP surface of the draft layer."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.existing = self.workspace / "notes.txt"
        self.existing.write_text("the human's own words", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_draft_tools_are_advertised(self):
        names = _tool_names()
        for name in ("mas_drafts", "mas_draft_stage", "mas_draft_resolve"):
            self.assertIn(name, names)

    def test_the_resource_tools_are_advertised(self):
        names = _tool_names()
        self.assertIn("mas_files", names)
        self.assertIn("mas_preview", names)

    def test_an_agent_can_stage_a_change(self):
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "reason": "adding a conclusion",
                "workspace": str(self.workspace),
            },
        )
        draft = staged.get("draft") or {}
        self.assertTrue(draft.get("id"))
        self.assertTrue(draft.get("diff"))
        self.assertEqual(draft.get("reason"), "adding a conclusion")

    def test_a_staged_change_does_not_touch_the_original(self):
        handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(
            self.existing.read_text(encoding="utf-8"), "the human's own words"
        )

    def test_staging_a_missing_file_is_refused(self):
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "does-not-exist.txt",
                "content": "whatever",
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(staged.get("error"), "draft_refused")
        self.assertFalse((self.workspace / "does-not-exist.txt").exists())

    def test_the_agent_cannot_approve_its_own_proposal(self):
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "workspace": str(self.workspace),
            },
        )
        draft_id = (staged.get("draft") or {}).get("id")
        resolved = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(resolved.get("error"), "human_token_required")
        self.assertEqual(
            self.existing.read_text(encoding="utf-8"), "the human's own words"
        )

    def test_a_wrong_token_is_refused(self):
        token = ensure_human_token(self.root / "state")
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "workspace": str(self.workspace),
            },
        )
        draft_id = (staged.get("draft") or {}).get("id")
        resolved = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "token": token + "x",
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(resolved.get("error"), "human_token_required")
        self.assertEqual(
            self.existing.read_text(encoding="utf-8"), "the human's own words"
        )

    def test_the_right_token_writes_the_change_back(self):
        token = ensure_human_token(self.root / "state")
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "workspace": str(self.workspace),
            },
        )
        draft_id = (staged.get("draft") or {}).get("id")
        resolved = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "token": token,
                "workspace": str(self.workspace),
            },
        )
        self.assertIn("written back", str(resolved.get("message") or ""))
        self.assertEqual(
            self.existing.read_text(encoding="utf-8"), "the agent proposes this"
        )

    def test_rejecting_through_mcp_changes_nothing(self):
        token = ensure_human_token(self.root / "state")
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "nobody wanted this",
                "workspace": str(self.workspace),
            },
        )
        draft_id = (staged.get("draft") or {}).get("id")
        handle_tool(
            "mas_draft_resolve",
            {
                "action": "reject",
                "draft_id": draft_id,
                "token": token,
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(
            self.existing.read_text(encoding="utf-8"), "the human's own words"
        )

    def test_an_unknown_action_is_refused(self):
        resolved = handle_tool(
            "mas_draft_resolve",
            {
                "action": "obliterate",
                "draft_id": "D-whatever",
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(resolved.get("error"), "unknown_action")

    def test_listing_the_drafts_shows_the_pending_one(self):
        handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "workspace": str(self.workspace),
            },
        )
        listed = handle_tool("mas_drafts", {"workspace": str(self.workspace)})
        self.assertEqual(listed.get("count"), 1)

    def test_showing_one_draft_prints_its_diff(self):
        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": "the agent proposes this",
                "workspace": str(self.workspace),
            },
        )
        draft_id = (staged.get("draft") or {}).get("id")
        shown = handle_tool(
            "mas_drafts",
            {"action": "show", "draft_id": draft_id, "workspace": str(self.workspace)},
        )
        self.assertTrue((shown.get("draft") or {}).get("diff"))

    def test_the_mcp_server_can_index_a_workspace(self):
        indexed = handle_tool("mas_files", {"path": str(self.workspace)})
        self.assertGreater(int(indexed.get("files") or 0), 0)

    def test_the_mcp_server_can_preview_a_file(self):
        previewed = handle_tool("mas_preview", {"path": str(self.existing)})
        self.assertIn("the human's own words", str(previewed.get("text") or ""))

    def test_previewing_a_missing_file_is_refused(self):
        previewed = handle_tool("mas_preview", {"path": str(self.root / "nope.txt")})
        self.assertEqual(previewed.get("error"), "file_not_found")


class HumanTokenTests(unittest.TestCase):
    """The token that opens the approval door."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self._tmp.name) / "state"

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_token_is_created_on_demand(self):
        self.assertEqual(read_human_token(self.state), "")
        token = ensure_human_token(self.state)
        self.assertTrue(token)
        self.assertEqual(read_human_token(self.state), token)

    def test_the_token_is_stable(self):
        first = ensure_human_token(self.state)
        second = ensure_human_token(self.state)
        self.assertEqual(first, second)

    def test_the_right_token_verifies(self):
        token = ensure_human_token(self.state)
        self.assertTrue(verify_human_token(self.state, token))

    def test_a_wrong_token_does_not_verify(self):
        token = ensure_human_token(self.state)
        self.assertFalse(verify_human_token(self.state, token + "x"))
        self.assertFalse(verify_human_token(self.state, ""))
        self.assertFalse(verify_human_token(self.state, None))

    def test_no_token_means_nothing_verifies(self):
        self.assertFalse(verify_human_token(self.state, "anything"))


class DraftCliTokenTests(unittest.TestCase):
    """The CLI hands the token out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def _run(self, argv):
        from mas.cli import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_the_token_command_prints_a_token(self):
        code, output = self._run(["drafts", "token"])
        self.assertEqual(code, 0)
        self.assertTrue(output.strip())

    def test_the_token_command_works_without_a_workspace(self):
        # The token belongs to the machine, not to a workspace: a
        # machine that has not run anything yet can still hand one out.
        code, output = self._run(["drafts", "token"])
        self.assertEqual(code, 0)
        self.assertTrue(output.strip())

    def test_the_token_command_prints_the_same_token_twice(self):
        _, first = self._run(["drafts", "token"])
        _, second = self._run(["drafts", "token"])
        self.assertEqual(first.strip(), second.strip())


class McpWorkspacePathTests(unittest.TestCase):
    """The index and the preview must read the folder the caller named.

    An agent that says "index this workspace" and gets back an empty
    list has been told a lie about its own files. These tests pin the
    path resolution down.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        (self.workspace / "notes.txt").write_text("hello", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_index_reads_the_named_workspace(self):
        result = handle_tool("mas_files", {"workspace": str(self.workspace)})
        self.assertNotIn("error", result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["files"], 1)

    def test_the_index_still_works_without_a_workspace(self):
        # With no argument the tool keeps indexing every run workspace.
        result = handle_tool("mas_files", {})
        self.assertNotIn("error", result)
        self.assertIn("folders", result)

    def test_the_preview_reads_a_relative_path_in_the_named_workspace(self):
        result = handle_tool(
            "mas_preview", {"workspace": str(self.workspace), "path": "notes.txt"}
        )
        self.assertNotIn("error", result)
        self.assertIn("hello", str(result.get("text") or ""))

    def test_the_preview_still_reads_an_absolute_path(self):
        result = handle_tool("mas_preview", {"path": str(self.workspace / "notes.txt")})
        self.assertNotIn("error", result)
        self.assertIn("hello", str(result.get("text") or ""))

    def test_the_preview_says_so_when_the_file_is_not_there(self):
        result = handle_tool(
            "mas_preview", {"workspace": str(self.workspace), "path": "missing.txt"}
        )
        self.assertEqual(result.get("error"), "file_not_found")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# -*- coding: utf-8 -*-
"""v0.7.76 tests: what a forced approval would cost.

v0.7.75 gave the draft layer an optimistic lock: the draft remembers
the hash of the file it was made against, and a plain approval is
refused when the file moved. That is the right answer - but "the file
moved" is a fact, not a decision. A human cannot tell whether the
change that moved is worth keeping without seeing *what* it was.

v0.7.76 keeps the baseline with the draft and names the lines a forced
approval would take away:

* the draft keeps the bytes the file held when it was made;
* the comparison names the lines the human wrote that the draft does
  not carry - the ones a forced approval would throw away;
* lines nobody touched are not called a loss;
* the command line and the MCP tool answer the same question in plain
  words;
* a binary file is not mistaken for a changed file;
* the digest basis is recorded, so an old draft is compared honestly
  instead of raising a false alarm forever.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from mas.drafts import (
    DIGEST_BASIS_BYTES,
    DraftConflict,
    approve_draft,
    draft_compare,
    draft_conflict,
    stage_draft,
)
from mas.mcp_server import TOOLS, handle_tool

REPO = pathlib.Path(__file__).resolve().parents[1]


def _tool_names():
    return [str(t.get("name") or "") for t in TOOLS if t.get("name")]


class CompareBaselineTests(unittest.TestCase):
    """The draft keeps the baseline it was made against."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        # The bytes are written exactly, with no newline translation.
        # ``write_text`` would rewrite "\n" as "\r\n" on Windows and
        # keep it as "\n" on Linux and macOS - and a digest taken over
        # the text is a *different number* on each platform. That makes
        # a test about digests pass on one platform and fail on another
        # for a reason that has nothing to do with the code under test.
        self.target.write_bytes(b"alpha\nbeta\ngamma\n")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_draft_keeps_the_baseline(self):
        staged = stage_draft(
            self.workspace,
            "notes.txt",
            b"alpha\nbeta-agent\ngamma\n",
            reason="the agent proposes this",
        )
        baseline = self.workspace / ".mas-drafts" / ("%s.base" % staged["id"])
        self.assertTrue(baseline.exists(), "the baseline must be kept")
        self.assertEqual(baseline.read_bytes(), self.target.read_bytes())

    def test_the_digest_basis_is_recorded(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\n")
        self.assertEqual(staged.get("digest_basis"), DIGEST_BASIS_BYTES)

    def test_the_comparison_names_the_lost_line(self):
        staged = stage_draft(
            self.workspace, "notes.txt", b"alpha\nbeta-agent\ngamma\n"
        )
        # The human writes a line of their own while the draft waits.
        self.target.write_text("alpha\nbeta\ngamma\nhuman-added\n", encoding="utf-8")
        comparison = draft_compare(self.workspace, staged)
        self.assertTrue(comparison["conflict"])
        self.assertEqual(comparison["would_lose"], ["human-added"])
        self.assertEqual(comparison["would_lose_count"], 1)

    def test_untouched_lines_are_not_a_loss(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\ngamma\n")
        comparison = draft_compare(self.workspace, staged)
        self.assertEqual(comparison["would_lose"], [])

    def test_a_line_the_draft_also_carries_is_not_a_loss(self):
        staged = stage_draft(
            self.workspace, "notes.txt", b"alpha\nbeta\ngamma\nhuman-added\n"
        )
        self.target.write_text("alpha\nbeta\ngamma\nhuman-added\n", encoding="utf-8")
        comparison = draft_compare(self.workspace, staged)
        self.assertEqual(comparison["would_lose"], [])

    def test_a_binary_file_is_not_mistaken_for_a_changed_file(self):
        blob = self.workspace / "blob.bin"
        blob.write_bytes(bytes(range(256)))
        staged = stage_draft(self.workspace, "blob.bin", bytes(reversed(range(256))))
        comparison = draft_compare(self.workspace, staged)
        self.assertFalse(comparison["readable"])
        self.assertFalse(comparison["conflict"])
        self.assertEqual(comparison["would_lose"], [])

    def test_a_missing_file_is_reported_not_guessed(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\n")
        self.target.unlink()
        comparison = draft_compare(self.workspace, staged)
        self.assertTrue(comparison["file_missing"])
        self.assertFalse(comparison["readable"])

    def test_an_old_draft_without_a_basis_is_not_a_false_alarm(self):
        """A draft made before v0.7.76 recorded the hash of the *text*.

        A text digest and a byte digest are the same number for every
        file that decodes as UTF-8 (decoding and re-encoding gives the
        same bytes back), and different numbers only for a file that
        does not decode. So a text-basis number *cannot* be compared
        with the byte hash of the file: for most files the comparison
        would silently succeed, and for a binary file it would always
        cry wolf.

        Such an old draft kept no baseline either, so nothing can be
        said with confidence - and a false alarm is worse than no
        alarm, because it teaches the human to ignore the real ones.
        """
        import mas.drafts as drafts_module

        staged = stage_draft(self.workspace, "notes.txt", b"alpha\n")
        staged.pop("digest_basis", None)
        staged["before_sha256"] = drafts_module._digest_text(
            b"alpha\nbeta\ngamma\n"
        )
        # An old draft kept no baseline.
        baseline = self.workspace / ".mas-drafts" / ("%s.base" % staged["id"])
        baseline.unlink()
        clash = draft_conflict(self.workspace, staged)
        self.assertFalse(clash["conflict"], clash)
        self.assertEqual(clash["reason"], "unknown_basis")
        self.assertEqual(clash["expected_sha256"], staged["before_sha256"])
        self.assertTrue(clash["current_sha256"])

    def test_an_old_draft_is_judged_by_its_baseline_not_by_its_number(self):
        """The recorded number of an old draft is not comparable.

        For a file that decodes as UTF-8 the text digest and the byte
        digest are the *same* number, so comparing the recorded number
        with the byte hash would "succeed" for the wrong reason: it
        would be comparing a number with itself, not with the file.
        The only honest comparison is the baseline the draft kept.
        """
        import mas.drafts as drafts_module

        staged = stage_draft(self.workspace, "notes.txt", b"alpha\n")
        staged.pop("digest_basis", None)
        staged["before_sha256"] = drafts_module._digest_text(
            b"alpha\nbeta\ngamma\n"
        )
        # The file moved away from the baseline.
        self.target.write_bytes(b"alpha\nbeta\ngamma\nhuman-added\n")
        clash = draft_conflict(self.workspace, staged)
        self.assertTrue(clash["conflict"], clash)
        self.assertEqual(clash["reason"], "file_changed")

    def test_an_old_draft_with_a_baseline_is_compared_honestly(self):
        """The baseline is the only honest comparison for an old draft."""
        import mas.drafts as drafts_module

        staged = stage_draft(self.workspace, "notes.txt", b"alpha\n")
        staged.pop("digest_basis", None)
        staged["before_sha256"] = drafts_module._digest_text(
            b"alpha\nbeta\ngamma\n"
        )
        clash = draft_conflict(self.workspace, staged)
        self.assertFalse(clash["conflict"], clash)
        self.assertEqual(clash["reason"], "unchanged")


class ConflictStillHoldsTests(unittest.TestCase):
    """The comparison does not weaken the lock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_a_plain_approval_is_still_refused(self):
        staged = stage_draft(self.workspace, "notes.txt", b"agent\n")
        self.target.write_text("human\n", encoding="utf-8")
        with self.assertRaises(DraftConflict):
            approve_draft(self.workspace, staged["id"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "human\n")

    def test_the_refusal_does_not_burn_the_draft(self):
        staged = stage_draft(self.workspace, "notes.txt", b"agent\n")
        self.target.write_text("human\n", encoding="utf-8")
        with self.assertRaises(DraftConflict):
            approve_draft(self.workspace, staged["id"])
        from mas.drafts import get_draft

        self.assertEqual(get_draft(self.workspace, staged["id"])["state"], "pending")


class CliCompareTests(unittest.TestCase):
    """The command line answers the same question in plain words."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def _run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_the_action_is_registered(self):
        proc = self._run_cli("drafts", "--help")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("compare", proc.stdout)

    def test_it_names_what_would_be_lost(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\ngamma\n")
        self.target.write_text("alpha\nbeta\ngamma\nhuman-added\n", encoding="utf-8")
        proc = self._run_cli(
            "drafts", "compare", staged["id"], "--workspace", str(self.workspace)
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("human-added", proc.stdout)
        self.assertIn("会丢掉", proc.stdout)

    def test_the_json_form_carries_the_lost_lines(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\ngamma\n")
        self.target.write_text("alpha\nbeta\ngamma\nhuman-added\n", encoding="utf-8")
        proc = self._run_cli(
            "drafts",
            "compare",
            staged["id"],
            "--workspace",
            str(self.workspace),
            "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        comparison = payload["comparison"]
        self.assertEqual(comparison["would_lose"], ["human-added"])

    def test_an_unknown_draft_is_refused(self):
        proc = self._run_cli(
            "drafts", "compare", "no-such-draft", "--workspace", str(self.workspace)
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("找不到", proc.stdout + proc.stderr)


class McpCompareTests(unittest.TestCase):
    """An agent can ask the same question over MCP."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_tool_is_advertised(self):
        self.assertIn("mas_draft_compare", _tool_names())

    def test_the_tool_answers_the_question(self):
        staged = stage_draft(self.workspace, "notes.txt", b"alpha\nbeta-agent\ngamma\n")
        self.target.write_text("alpha\nbeta\ngamma\nhuman-added\n", encoding="utf-8")
        answer = handle_tool(
            "mas_draft_compare",
            {"draft_id": staged["id"], "workspace": str(self.workspace)},
        )
        self.assertNotIn("error", answer, answer)
        comparison = answer["comparison"]
        self.assertEqual(comparison["would_lose"], ["human-added"])
        self.assertTrue(comparison["conflict"])

    def test_an_unknown_draft_is_reported(self):
        answer = handle_tool(
            "mas_draft_compare",
            {"draft_id": "no-such-draft", "workspace": str(self.workspace)},
        )
        self.assertEqual(answer.get("error"), "draft_not_found")

    def test_a_missing_draft_id_is_reported(self):
        answer = handle_tool("mas_draft_compare", {"workspace": str(self.workspace)})
        self.assertEqual(answer.get("error"), "draft_id_required")


class StatusSeesTheStuckDraftsTests(unittest.TestCase):
    """A draft waiting on a conflict is a decision the human still owes.

    The ticket queue is for the *loop* to ask a question. A draft that
    sits on a conflict is invisible there, so an agent reading the
    status would never learn that the work is stuck.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.target = self.workspace / "notes.txt"
        self.target.write_text("alpha\n", encoding="utf-8")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        self._tmp.cleanup()

    def test_the_status_reports_the_conflicting_drafts(self):
        staged = stage_draft(self.workspace, "notes.txt", b"agent\n")
        self.target.write_text("human\n", encoding="utf-8")
        answer = handle_tool("mas_status", {"workspace": str(self.workspace)})
        self.assertEqual(answer.get("conflicting_draft_count"), 1, answer)
        stuck = answer.get("conflicting_drafts") or []
        self.assertEqual(stuck[0]["draft_id"], staged["id"])

    def test_the_status_is_quiet_when_nothing_is_stuck(self):
        stage_draft(self.workspace, "notes.txt", b"agent\n")
        answer = handle_tool("mas_status", {"workspace": str(self.workspace)})
        self.assertEqual(answer.get("conflicting_draft_count"), 0, answer)


class ForceCostGateTest(unittest.TestCase):
    """The nineteenth gate must exist and mean something."""

    def test_the_check_is_registered(self):
        from mas.selftest import CHECKS, EXPECTED_CHECKS

        names = {getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS}
        self.assertIn("force_cost", names)
        self.assertEqual(len(CHECKS), EXPECTED_CHECKS)

    def test_the_check_passes_offline(self):
        from mas.selftest import check_a_forced_approval_says_what_it_would_cost

        row = check_a_forced_approval_says_what_it_would_cost(iterations=1)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "force_cost")

    def test_the_check_states_what_it_guarantees(self):
        from mas.selftest import check_a_forced_approval_says_what_it_would_cost

        row = check_a_forced_approval_says_what_it_would_cost(iterations=1)
        self.assertTrue(row.get("title"))
        self.assertTrue(row.get("invariant"))
        self.assertIsInstance(row.get("failures"), list)

    def test_the_check_can_be_selected_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["force_cost"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "force_cost")
        self.assertTrue(result["ok"], result)

    def test_the_full_gate_still_passes(self):
        from mas.selftest import EXPECTED_CHECKS, run_selftest

        result = run_selftest(iterations=1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["checks"]), EXPECTED_CHECKS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

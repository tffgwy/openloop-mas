# -*- coding: utf-8 -*-
"""A human must be able to edit the files in place and hand the folder back.

v0.7.69 added the folder handoff: the human opens the files the run
produced, in the run's own workspace, changes them, and hands the
folder back. The acceptance criteria are the ticket's own, and the
file checks have to see the *edited* files - that is the whole point.

The properties that make this a closure and not a rubber stamp:

* the file checks read the human's edits, not the ones the loop left
  behind (the overlay);
* nothing is written before the verdict - a refused handoff leaves
  the disk exactly as the human left it;
* the folder is the unit: every file in it is part of the offer;
* the ticket names the folder, so a human can find it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.handoff import collect_workspace_files, read_workspace_overlay  # noqa: E402
from mas.providers import Provider  # noqa: E402


class _AlwaysFails(Provider):
    """The verifier always says no - the model cannot fake a closure."""

    name = "handoff-test"

    def __init__(self, model: str = "test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        self.calls += 1
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
        elif role == "verifier":
            text = json.dumps(
                {"verdict": "fail", "scores": [{"id": "c1", "pass": False, "note": "no"}]}
            )
        elif role == "reflector":
            text = json.dumps({"summary": "s", "issues": [], "next_action": "revise"})
        elif role == "worker":
            text = "the answer is 41."
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


#: The criteria judge a *file*, not a blob of text. This is the shape
#: of task this handoff exists for.
FILE_CRITERIA = [
    {
        "id": "c1",
        "desc": "finding.txt says 42",
        "check": {"type": "file_contains", "arg": "finding.txt::42"},
    }
]


def _config(tmp):
    return {
        "provider": "mock",
        "model": "test-model",
        "state_dir": os.path.join(tmp, "state"),
        "workspace": "",
        "budget": {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
        "stagnation_limit": 2,
    }


class HandoffModuleTest(unittest.TestCase):
    def test_the_files_of_a_folder_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.txt").write_text("beta", encoding="utf-8")
            rows = collect_workspace_files(root)
            paths = {row["path"] for row in rows}
            self.assertIn("a.txt", paths)
            self.assertIn("sub/b.txt", paths)

    def test_the_old_deliverable_is_not_offered_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            (root / "deliverable.md").write_text("old", encoding="utf-8")
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            paths = {row["path"] for row in collect_workspace_files(root)}
            self.assertNotIn("deliverable.md", paths)
            self.assertIn("a.txt", paths)

    def test_the_overlay_holds_the_bytes_on_the_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            (root / "a.txt").write_bytes("hello".encode("utf-8"))
            overlay = read_workspace_overlay(root)
            self.assertEqual(len(overlay), 1)
            self.assertEqual(list(overlay.values())[0], b"hello")


class FolderHandoffTest(unittest.TestCase):
    def _escalated(self, tmp):
        engine = LoopEngine(
            _config(tmp), provider=_AlwaysFails(model="test-model")
        )
        result = engine.run(goal="write the finding", criteria=FILE_CRITERIA)
        self.assertFalse(result.closed)
        return engine, str(result.ticket_id)

    def _workspace_of(self, engine, ticket_id):
        ticket = engine.tickets.get(ticket_id)
        return Path(str(ticket["payload"].get("workspace") or ""))

    def test_the_ticket_names_the_folder_to_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            self.assertTrue(str(folder))
            self.assertTrue(folder.exists())

    def test_a_folder_edited_by_hand_can_close_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            result = engine.handback_workspace(ticket_id, note="by hand")
            self.assertTrue(result.closed, result.error)
            self.assertEqual(result.status, "closed")

    def test_the_file_check_reads_the_human_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            result = engine.handback_workspace(ticket_id)
            by_id = {str(s.get("id")): s for s in result.scores}
            self.assertEqual(by_id["c1"]["source"], "file")
            self.assertTrue(by_id["c1"]["pass"])

    def test_an_unedited_folder_does_not_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 41.", encoding="utf-8")
            result = engine.handback_workspace(ticket_id)
            self.assertFalse(result.closed)
            self.assertTrue(result.issues)

    def test_a_refused_handoff_leaves_the_files_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            target = folder / "finding.txt"
            target.write_text("the answer is 41.", encoding="utf-8")
            before = target.read_bytes()
            result = engine.handback_workspace(ticket_id)
            self.assertFalse(result.closed)
            # The human's edit is still there, byte for byte. A
            # rejected draft is still their work.
            self.assertEqual(target.read_bytes(), before)

    def test_a_refused_handoff_writes_no_deliverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 41.", encoding="utf-8")
            # The failed run left a deliverable of its own. A refused
            # handoff must not overwrite it: the folder has to look
            # exactly as it did before the offer.
            deliverable = folder / "deliverable.md"
            before = deliverable.read_bytes() if deliverable.exists() else None
            result = engine.handback_workspace(ticket_id)
            self.assertFalse(result.closed)
            self.assertEqual(result.artifact_path, "")
            after = deliverable.read_bytes() if deliverable.exists() else None
            self.assertEqual(before, after)

    def test_an_accepted_handoff_writes_the_deliverable_next_to_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            result = engine.handback_workspace(ticket_id)
            self.assertTrue(result.closed)
            self.assertTrue(result.artifact_path)
            written = Path(result.artifact_path)
            self.assertTrue(written.exists())
            self.assertEqual(written.parent, folder)
            self.assertIn("42", written.read_text(encoding="utf-8"))

    def test_every_file_of_the_folder_is_part_of_the_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            (folder / "notes.txt").write_text("some notes", encoding="utf-8")
            result = engine.handback_workspace(ticket_id)
            self.assertTrue(result.closed, result.error)
            paths = {row["path"] for row in result.files}
            self.assertIn("finding.txt", paths)
            self.assertIn("notes.txt", paths)

    def test_the_ticket_closes_with_the_handback_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            engine.handback_workspace(ticket_id)
            ticket = engine.tickets.get(ticket_id)
            self.assertEqual(ticket["state"], "resolved")
            self.assertEqual(ticket["decision"], "handback")

    def test_a_refused_handoff_stays_in_the_human_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 41.", encoding="utf-8")
            engine.handback_workspace(ticket_id)
            ticket = engine.tickets.get(ticket_id)
            self.assertEqual(ticket["state"], "waiting_human")

    def test_the_handoff_is_marked_as_a_handback_in_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            engine.handback_workspace(ticket_id)
            entries = engine.ledger.entries()
            ends = [e for e in entries if e.get("role") == "run_end"]
            self.assertTrue(ends)
            self.assertTrue(ends[-1].get("handback"))
            self.assertTrue(ends[-1].get("closed"))

    def test_an_unknown_ticket_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="test-model"))
            with self.assertRaises(KeyError):
                engine.handback_workspace("T-nope")

    def test_a_missing_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            with self.assertRaises(ValueError):
                engine.handback_workspace(
                    ticket_id, workspace=str(Path(tmp) / "does-not-exist")
                )

    def test_an_empty_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._workspace_of(engine, ticket_id)
            empty = Path(tmp) / "empty"
            empty.mkdir()
            with self.assertRaises(ValueError):
                engine.handback_workspace(ticket_id, workspace=str(empty))


class TheFolderIsTheUnitTest(unittest.TestCase):
    """The point of the folder handoff, stated as a difference.

    The old text handback asked a human to paste the content of the
    file they had edited into a command line. That is a second copy of
    the work, and it is a copy that can be wrong: a human who fixes
    the file and then forgets a line of it hands back something that
    fails - and the failure looks like the human's answer being bad,
    when it is really the copying that was bad.

    The folder handoff removes the copying: the human edits the file,
    the deliverable is built from the file, and the file checks read
    the file. So the deliverable that passes is the deliverable that
    is on the disk.
    """

    CRITERIA: ClassVar = [
        {
            "id": "c1",
            "desc": "finding.txt says 42",
            "check": {"type": "file_contains", "arg": "finding.txt::42"},
        },
        {
            "id": "c2",
            "desc": "the deliverable carries the conclusion",
            "check": {"type": "contains", "arg": "\u7ed3\u8bba"},
        },
    ]

    def _escalated(self, tmp):
        engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="test-model"))
        result = engine.run(goal="write the finding", criteria=self.CRITERIA)
        self.assertFalse(result.closed)
        ticket = engine.tickets.get(str(result.ticket_id))
        folder = Path(str(ticket["payload"]["workspace"]))
        return engine, str(result.ticket_id), folder

    def test_the_old_text_handback_cannot_carry_the_files_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id, folder = self._escalated(tmp)
            (folder / "finding.txt").write_text(
                "the answer is 42.\n\u7ed3\u8bba\uff1a\u5df2\u5b8c\u6210\n",
                encoding="utf-8",
            )
            # The human hands back a summary and forgets the last line.
            result = engine.handback(ticket_id, "the answer is 42.")
            self.assertFalse(result.closed)
            by_id = {str(s.get("id")): s for s in result.scores}
            self.assertTrue(by_id["c1"]["pass"])
            self.assertFalse(by_id["c2"]["pass"])

    def test_the_folder_handoff_carries_the_files_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id, folder = self._escalated(tmp)
            (folder / "finding.txt").write_text(
                "the answer is 42.\n\u7ed3\u8bba\uff1a\u5df2\u5b8c\u6210\n",
                encoding="utf-8",
            )
            result = engine.handback_workspace(ticket_id)
            self.assertTrue(result.closed, result.error)
            by_id = {str(s.get("id")): s for s in result.scores}
            self.assertTrue(by_id["c1"]["pass"])
            self.assertTrue(by_id["c2"]["pass"])

    def test_the_accepted_deliverable_is_the_file_on_the_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id, folder = self._escalated(tmp)
            (folder / "finding.txt").write_text(
                "the answer is 42.\n\u7ed3\u8bba\uff1a\u5df2\u5b8c\u6210\n",
                encoding="utf-8",
            )
            result = engine.handback_workspace(ticket_id)
            self.assertTrue(result.closed, result.error)
            written = Path(result.artifact_path).read_text(encoding="utf-8")
            self.assertIn("\u7ed3\u8bba", written)


class CliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_the_command_offers_the_folder(self):
        proc = self._run("handback", "--help")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("--workspace", proc.stdout)

    def test_the_ticket_queue_names_the_folder_to_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LoopEngine(
                _config(tmp), provider=_AlwaysFails(model="test-model")
            )
            result = engine.run(goal="write the finding", criteria=FILE_CRITERIA)
            self.assertFalse(result.closed)
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO)
            env["PYTHONUTF8"] = "1"
            env["MAS_STATE_DIR"] = os.path.join(tmp, "state")
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "mas", "tickets"],
                cwd=str(REPO), env=env, capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("在这里改", proc.stdout)
            self.assertIn("--workspace", proc.stdout)
            self.assertIn(str(result.ticket_id), proc.stdout)


if __name__ == "__main__":
    unittest.main()

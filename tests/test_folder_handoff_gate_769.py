# -*- coding: utf-8 -*-
"""v0.7.69 regression tests for the eleventh gate: ``folder_handoff``.

The tenth gate (``handback_report``) watches the *report*. Nothing
watched the *human path* - the folder handoff, where a human edits the
files where they are and hands the folder back. Mutation testing
proved the gap: six mutations of the folder handoff (read the wrong
folder, drop the file content from the deliverable, write a
deliverable even when the handoff is refused, offer only the first
file, drop the deliverable into a second folder, forget the folder on
the ticket) were caught by **zero** gates.

These tests pin the eleventh gate down: it has to exist, it has to be
listed among the checks, it has to pass, and it has to fail when the
human path breaks.
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
import tempfile
import unittest

from mas import selftest as st
from mas.engine import LoopEngine
from mas.providers import Provider


class _AlwaysFails(Provider):
    """The verifier always says no, so the human has to close the loop."""

    name = "gate-test-folder-handoff"

    def __init__(self, model: str = "m", **options) -> None:
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker",
                 expect_json=False, max_tokens=4000):
        self.calls += 1
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
        elif role == "verifier":
            text = json.dumps(
                {"verdict": "fail",
                 "scores": [{"id": "c1", "pass": False, "note": "no"}]}
            )
        elif role == "reflector":
            text = json.dumps(
                {"summary": "s", "issues": [], "next_action": "revise"}
            )
        elif role == "worker":
            text = "the answer is 41."
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


def _config(tmp):
    return {
        "provider": "mock",
        "model": "m",
        "state_dir": os.path.join(tmp, "state"),
        "workspace": "",
        "budget": {"max_iterations": 1, "max_tokens": 1000000,
                   "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1,
                          "review_mode": "all"},
        "team": {"language": "zh"},
        "stagnation_limit": 2,
    }


CRITERIA = [
    {"id": "c1", "desc": "finding.txt says 42",
     "check": {"type": "file_contains", "arg": "finding.txt::42"}},
    {"id": "c2", "desc": "the deliverable carries the conclusion",
     "check": {"type": "contains", "arg": "\u7ed3\u8bba"}},
]


def _engine_with_a_ticket(tmp):
    """A run that cannot close, with the folder the human is meant to edit."""
    engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="m"))
    run = engine.run(goal="write the finding", criteria=CRITERIA)
    assert not run.closed, "the loop must not close on its own"
    ticket_id = str(run.ticket_id or "")
    ticket = engine.tickets.get(ticket_id)
    workspace = str((ticket or {}).get("payload", {}).get("workspace") or "")
    assert workspace, "the ticket has to name the folder"
    folder = pathlib.Path(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "finding.txt").write_text("the answer is 41.\n", encoding="utf-8")
    return engine, ticket_id, folder


class TestFolderHandoffGateExists(unittest.TestCase):
    """The gate has to be there, and it has to be reachable."""

    def test_the_eleventh_gate_is_registered(self):
        names = [st._check_name(check) for check in st.CHECKS]
        self.assertIn("folder_handoff", names)

    def test_the_gate_has_its_own_short_name(self):
        check = st.check_a_folder_handoff_closes_on_human_edits
        self.assertEqual(st._check_name(check), "folder_handoff")

    def test_the_gate_passes_on_a_clean_build(self):
        row = st.check_a_folder_handoff_closes_on_human_edits(iterations=1)
        self.assertTrue(row["ok"], row.get("failures"))
        self.assertEqual(row["failures"], [])

    def test_the_gate_states_its_invariant(self):
        row = st.check_a_folder_handoff_closes_on_human_edits(iterations=1)
        self.assertTrue(row.get("detail"))
        self.assertEqual(row["name"], "folder_handoff")

    def test_run_selftest_can_select_it_by_name(self):
        result = st.run_selftest(iterations=1, only=["folder_handoff"])
        self.assertTrue(result["ok"], result.get("failed"))
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "folder_handoff")

    def test_the_gate_is_one_of_eleven(self):
        names = [st._check_name(check) for check in st.CHECKS]
        self.assertGreaterEqual(len(names), 11)


class TestFolderHandoffGateCatchesBreakage(unittest.TestCase):
    """The gate has to fail when the human path breaks."""

    def test_the_gate_fails_when_the_machine_reads_stale_files(self):
        """The file check has to read the human's edit, not the old one."""
        import mas.engine as engine_mod

        real_overlay = engine_mod.read_workspace_overlay

        def _stale_overlay(root):
            overlay = dict(real_overlay(root))
            for key in list(overlay):
                if str(key).endswith("finding.txt"):
                    overlay[key] = b"the answer is 41.\n"
            return overlay

        engine_mod.read_workspace_overlay = _stale_overlay
        try:
            row = st.check_a_folder_handoff_closes_on_human_edits(iterations=1)
        finally:
            engine_mod.read_workspace_overlay = real_overlay
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_a_refusal_leaves_a_deliverable(self):
        """A refused handoff must not look like an accepted answer."""
        real_handback = LoopEngine.handback_workspace

        def _write_on_refusal(self, ticket_id, note="", workspace=None):
            result = real_handback(self, ticket_id, note=note,
                                   workspace=workspace)
            if not result.closed:
                self._save_artifact("forced", "# forced\n")
                result.artifact_path = "forced"
            return result

        LoopEngine.handback_workspace = _write_on_refusal
        try:
            row = st.check_a_folder_handoff_closes_on_human_edits(iterations=1)
        finally:
            LoopEngine.handback_workspace = real_handback
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_the_offer_drops_files(self):
        """Only the first file of the folder is not the folder."""
        import mas.engine as engine_mod

        real_collect = engine_mod.collect_workspace_files

        def _one_file(root):
            return real_collect(root)[:1]

        engine_mod.collect_workspace_files = _one_file
        try:
            row = st.check_a_folder_handoff_closes_on_human_edits(iterations=1)
        finally:
            engine_mod.collect_workspace_files = real_collect
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_the_deliverable_drops_the_content(self):
        """The text check has to see the content of the files."""
        import mas.engine as engine_mod

        real_build = engine_mod.build_deliverable

        def _empty(files):
            return "# empty\n"

        engine_mod.build_deliverable = _empty
        try:
            row = st.check_a_folder_handoff_closes_on_human_edits(iterations=1)
        finally:
            engine_mod.build_deliverable = real_build
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])


class TestFolderHandoffInvariants(unittest.TestCase):
    """The invariants the eleventh gate guards, checked one by one."""

    def test_a_refused_handoff_leaves_the_disk_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id, folder = _engine_with_a_ticket(tmp)
            snapshot = (folder / "finding.txt").read_bytes()
            result = engine.handback_workspace(ticket_id)
            self.assertFalse(result.closed)
            self.assertEqual((folder / "finding.txt").read_bytes(), snapshot)
            self.assertEqual(result.artifact_path, "")

    def test_an_accepted_handoff_writes_into_the_edited_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id, folder = _engine_with_a_ticket(tmp)
            (folder / "finding.txt").write_text("the answer is 42.\n",
                                                encoding="utf-8")
            (folder / "notes.txt").write_text("\u7ed3\u8bba\uff1aok\n",
                                              encoding="utf-8")
            result = engine.handback_workspace(ticket_id)
            self.assertTrue(result.closed, result.error)
            self.assertTrue(result.artifact_path)
            self.assertEqual(pathlib.Path(result.artifact_path).parent, folder)
            names = {pathlib.Path(str(r.get("path"))).name
                     for r in result.files}
            self.assertIn("finding.txt", names)
            self.assertIn("notes.txt", names)

    def test_the_ticket_names_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id, folder = _engine_with_a_ticket(tmp)
            ticket = engine.tickets.get(ticket_id)
            self.assertTrue(ticket)
            self.assertEqual(
                pathlib.Path(str(ticket["payload"]["workspace"])).resolve(),
                folder.resolve(),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

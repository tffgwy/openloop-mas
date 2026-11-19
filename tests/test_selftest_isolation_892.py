# -*- coding: utf-8 -*-
"""The self-test must not touch the machine - and the gate can fail.

These are the unit tests behind the thirty-second gate. The gate
itself is in ``mas/selftest_isolation.py``; this file checks the
parts of it that are easier to state one at a time, plus the small
isolation helpers it is built on.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import unittest


class StateIsolationTest(unittest.TestCase):
    """The isolation helper points the machine somewhere else, and back."""

    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-isolation-"))
        self.previous = os.environ.get("MAS_STATE_DIR")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self.previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self.previous
        shutil.rmtree(self.root, ignore_errors=True)

    def test_it_points_the_state_directory_at_a_directory_of_its_own(self):
        from mas.isolation import state_isolation

        with state_isolation(self.root) as state:
            self.assertEqual(os.environ.get("MAS_STATE_DIR"), str(state))
            self.assertTrue(state.exists())

    def test_it_puts_the_environment_back(self):
        from mas.isolation import state_isolation

        with state_isolation(self.root):
            pass
        self.assertEqual(os.environ.get("MAS_STATE_DIR"), self.previous)

    def test_a_variable_that_was_never_set_is_removed_again(self):
        from mas.isolation import state_isolation

        os.environ.pop("MAS_STATE_DIR", None)
        with state_isolation(self.root):
            pass
        self.assertNotIn("MAS_STATE_DIR", os.environ)

    def test_the_environment_is_put_back_even_when_the_block_raises(self):
        from mas.isolation import state_isolation

        with self.assertRaises(ValueError), state_isolation(self.root):
            raise ValueError("boom")
        self.assertEqual(os.environ.get("MAS_STATE_DIR"), self.previous)

    def test_a_decision_made_inside_the_isolation_stays_inside_it(self):
        from mas.drafts import approve_draft, stage_draft
        from mas.isolation import ledger_path, state_isolation

        workspace = self.root / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "notes.txt").write_text("alpha\\n", encoding="utf-8")

        with state_isolation(self.root):
            staged = stage_draft(
                workspace,
                "notes.txt",
                b"beta\\n",
                reason="the agent proposes this",
            )
            approve_draft(workspace, str(staged.get("id") or ""))

        ledger = ledger_path(self.root / "state")
        self.assertTrue(ledger.exists())
        rows = [
            line
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(rows)


class SnapshotTest(unittest.TestCase):
    """A picture of a state directory, and what changed between two."""

    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-snapshot-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_an_untouched_directory_has_no_problems(self):
        from mas.isolation import diff_snapshots, snapshot_state

        before = snapshot_state(self.root)
        after = snapshot_state(self.root)
        self.assertEqual(diff_snapshots(before, after), [])

    def test_a_growing_ledger_is_named(self):
        from mas.isolation import diff_snapshots, snapshot_state

        before = snapshot_state(self.root)
        ledger = self.root / "ledgers" / "runs.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"event": "draft_approve"}\\n', encoding="utf-8")
        after = snapshot_state(self.root)
        problems = diff_snapshots(before, after)
        self.assertTrue(any("ledger" in problem for problem in problems))
        self.assertTrue(any("runs.jsonl" in problem for problem in problems))

    def test_a_changed_file_is_named(self):
        from mas.isolation import diff_snapshots, snapshot_state

        target = self.root / "note.txt"
        target.write_text("one", encoding="utf-8")
        before = snapshot_state(self.root)
        target.write_text("two", encoding="utf-8")
        after = snapshot_state(self.root)
        problems = diff_snapshots(before, after)
        self.assertTrue(any("note.txt" in problem for problem in problems))


class SelftestIsolationGateTest(unittest.TestCase):
    """The thirty-second gate, and the fact that it can fail."""

    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-gate-iso-"))
        self.previous = os.environ.get("MAS_STATE_DIR")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self.previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self.previous
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_gate_passes(self):
        from mas.selftest_isolation import check_the_selftest_leaves_the_machine_alone

        result = check_the_selftest_leaves_the_machine_alone(iterations=1)
        self.assertTrue(result["ok"], result.get("failures"))
        self.assertEqual(result["name"], "selftest_isolation")

    def test_the_gate_can_be_run_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["selftest_isolation"])
        self.assertTrue(result["ok"], result.get("failed"))

    def test_a_leak_is_detected(self):
        from mas.selftest_isolation import (
            _isolation_observations,
            _isolation_verdicts,
        )

        machine = self.root / "machine"
        machine.mkdir(parents=True, exist_ok=True)
        os.environ["MAS_STATE_DIR"] = str(machine)

        import mas.isolation as isolation_module

        real = isolation_module.state_isolation

        root = self.root

        class _NoIsolation:
            def __enter__(self):
                return pathlib.Path(root)

            def __exit__(self, *exc_info):
                return False

        isolation_module.state_isolation = lambda _root: _NoIsolation()
        try:
            facts = _isolation_observations(self.root)
        finally:
            isolation_module.state_isolation = real

        verdicts = _isolation_verdicts(facts)
        self.assertFalse(verdicts["the machine's ledger is exactly as it was found"])
        self.assertTrue(
            any("ledger" in problem for problem in facts["machine_problems"])
        )

    def test_the_real_gates_do_not_leak_into_the_machine(self):
        """The undo gate approves drafts - and must not write them down here."""
        from mas.isolation import snapshot_state
        from mas.selftest import check_an_approved_change_can_be_taken_back

        machine = self.root / "machine"
        machine.mkdir(parents=True, exist_ok=True)
        os.environ["MAS_STATE_DIR"] = str(machine)
        before = snapshot_state(machine)
        check_an_approved_change_can_be_taken_back(iterations=1)
        after = snapshot_state(machine)
        self.assertEqual(before["ledger_rows"], after["ledger_rows"])

    def test_the_draft_gate_does_not_leak_into_the_machine(self):
        from mas.isolation import snapshot_state
        from mas.selftest import check_a_draft_is_approved_before_it_is_written_back

        machine = self.root / "machine"
        machine.mkdir(parents=True, exist_ok=True)
        os.environ["MAS_STATE_DIR"] = str(machine)
        before = snapshot_state(machine)
        check_a_draft_is_approved_before_it_is_written_back(iterations=1)
        after = snapshot_state(machine)
        self.assertEqual(before["ledger_rows"], after["ledger_rows"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

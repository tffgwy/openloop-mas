# -*- coding: utf-8 -*-
"""v0.11.2 tests: an approved change is a take-over, not a drift.

The seal compares the bytes on the disk with the bytes the closure
was decided on. When they differ, the seal had one word for it:
*drifted* - and a drifted conclusion is an accusation. It says "the
run lied about what it decided on".

But a human who reviews a deliverable and approves a change is doing
the loop's job, not attacking it. So the seal now tells the two
apart, and this file pins the difference down:

* an approved change is reported as ``taken-over`` - the conclusion
  still holds, and the report says so in those words;
* an unapproved change is still reported as ``drifted`` - the lock is
  not weakened by any of this;
* an approval that was taken back excuses nothing;
* an approval of another folder excuses nothing, even when the bytes
  are identical;
* an approval whose chain is broken excuses nothing either - the
  chain is the authority, and an approval nobody can verify is not
  evidence;
* every surface says the same thing about the same closure.

The bytes are written with ``write_bytes`` on purpose. A file written
with ``write_text`` can hold ``\\r\\n`` where the text holds ``\\n``,
and a comparison across two bases is not a comparison at all - that
trap cost v0.7.77 a CI failure that only showed up on Windows.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import pathlib
import shutil
import tempfile
import unittest

from mas.drafts import approve_draft, stage_draft, undo_draft
from mas.engine import LoopEngine
from mas.ledger import Ledger
from mas.providers import Provider
from mas.seal import SEAL_STATE_TAKEN_OVER, seal_report


class _Writer(Provider):
    """A provider that writes a file and then passes the review."""

    name = "test-writer"

    def complete(self, *, system: str = "", prompt: str = "", role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000):
        usage = {"total_tokens": 10}
        if role == "worker":
            text = (
                "交付物正文\n"
                '<<<MAS:FILE path="notes/finding.txt">>>\n'
                "结论：SELFTEST\n"
                "<<<MAS:END FILE>>>\n"
            )
            return {"text": text, "usage": usage, "raw": {}}
        if role == "verifier":
            return {
                "text": json.dumps(
                    {
                        "verdict": "pass",
                        "scores": [
                            {"id": "c1", "pass": True, "note": "ok"},
                            {"id": "c2", "pass": True, "note": "file there"},
                        ],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        return {"text": "{}", "usage": usage, "raw": {}}


class TakeOverCase(unittest.TestCase):
    """A test case with its own state directory."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-p903-test-"))
        self.state = self.root / "state"
        self.state.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.workspace = None
        self.rows = []
        self._run()

    def _run(self):
        config = {
            "provider": "mock",
            "model": "test-model",
            "state_dir": str(self.state),
            "workspace": "",
            "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
        }
        engine = LoopEngine(config, provider=_Writer(model="test-model"))
        result = engine.run(
            goal="写一份结论",
            criteria=[
                {"id": "c1", "desc": "含正文", "check": {"type": "contains", "arg": "正文"}},
                {"id": "c2", "desc": "文件写出来了",
                 "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
            ],
        )
        self.workspace = pathlib.Path(str(result.workspace or ""))
        self.rows = engine.ledger.entries()
        self.target = self.workspace / "notes" / "finding.txt"
        return result

    def _report(self):
        """The seal report of the ledger, the way ``mas seal`` reads it."""
        return seal_report(Ledger(self.state / "ledgers" / "runs.jsonl").entries())

    def _state(self):
        """The state of the closure."""
        results = self._report().get("results") or []
        return str(results[0].get("state") or "") if results else ""

    def _approve_an_edit(self, extra=b"REVIEWED\n", note="a person reviewed it"):
        original = self.target.read_bytes()
        draft = stage_draft(
            self.workspace,
            "notes/finding.txt",
            original + extra,
            reason="test",
        )
        approve_draft(self.workspace, str(draft.get("id") or ""), note=note)
        return draft


class TheTakeOverTest(TakeOverCase):
    """What an approved change is reported as."""

    def test_a_fresh_closure_still_holds(self):
        self.assertEqual(self._state(), "holds")

    def test_an_approved_change_is_a_take_over(self):
        self._approve_an_edit()
        self.assertEqual(self._state(), SEAL_STATE_TAKEN_OVER)

    def test_an_approved_change_is_not_a_drift(self):
        self._approve_an_edit()
        report = self._report()
        self.assertEqual(report.get("drifted"), 0)
        self.assertEqual(report.get("taken_over"), 1)
        self.assertTrue(report.get("ok"))
        results = report.get("results") or []
        self.assertEqual(results[0].get("drifted_files"), [])
        self.assertEqual(results[0].get("problems"), [])

    def test_the_take_over_names_the_file_and_the_approval(self):
        self._approve_an_edit()
        results = self._report().get("results") or []
        taken = results[0].get("taken_over_files") or []
        self.assertEqual(len(taken), 1)
        self.assertEqual(
            str(taken[0].get("path") or "").replace("\\", "/"),
            "notes/finding.txt",
        )
        self.assertTrue(taken[0].get("draft_id"))

    def test_an_unapproved_change_is_still_a_drift(self):
        self.target.write_bytes(self.target.read_bytes() + b"SNEAKY\n")
        self.assertEqual(self._state(), "drifted")
        report = self._report()
        self.assertEqual(report.get("drifted"), 1)
        self.assertEqual(report.get("taken_over"), 0)
        self.assertFalse(report.get("ok"))

    def test_an_approval_that_was_taken_back_excuses_nothing(self):
        original = self.target.read_bytes()
        draft = self._approve_an_edit()
        self.assertEqual(self._state(), SEAL_STATE_TAKEN_OVER)
        undo_draft(self.workspace, str(draft.get("id") or ""), note="took it back")
        # The undo put the baseline back. Now the approved bytes are
        # written again - by hand, with no approval standing.
        self.target.write_bytes(original + b"REVIEWED\n")
        self.assertEqual(self._state(), "drifted")

    def test_an_approval_of_another_folder_excuses_nothing(self):
        other = self.root / "other-workspace"
        (other / "notes").mkdir(parents=True)
        other_target = other / "notes" / "finding.txt"
        other_target.write_bytes(self.target.read_bytes())
        draft = stage_draft(
            other,
            "notes/finding.txt",
            other_target.read_bytes() + b"REVIEWED\n",
            reason="test",
        )
        approve_draft(other, str(draft.get("id") or ""), note="approved elsewhere")
        # The very same bytes are approved here too - but not here.
        self.target.write_bytes(self.target.read_bytes() + b"REVIEWED\n")
        self.assertEqual(self._state(), "drifted")

    def test_an_approval_whose_chain_is_broken_excuses_nothing(self):
        self._approve_an_edit()
        self.assertEqual(self._state(), SEAL_STATE_TAKEN_OVER)
        # The chain is the authority. A chain that does not verify
        # cannot excuse anything.
        chain_path = self.workspace / ".mas-drafts" / "audit.jsonl"
        self.assertTrue(chain_path.exists())
        tampered = []
        for line in chain_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                tampered.append(line)
                continue
            if str(entry.get("event") or "") == "draft_approve":
                entry["approved_sha256"] = "0" * 64
            tampered.append(json.dumps(entry, ensure_ascii=False))
        chain_path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
        self.assertEqual(self._state(), "drifted")


class EveryFaceAgreesTest(TakeOverCase):
    """The report, the command line and the JSON say the same thing."""

    def test_the_report_counts_the_take_over_and_no_drift(self):
        from mas.report import build_report

        self._approve_an_edit()
        payload = build_report(self.rows)
        totals = payload.get("totals") or {}
        self.assertGreaterEqual(int(totals.get("seal_taken_over") or 0), 1)
        self.assertEqual(int(totals.get("seal_drifted") or 0), 0)
        seal = payload.get("seal") or {}
        self.assertGreaterEqual(int(seal.get("taken_over") or 0), 1)
        self.assertEqual(int(seal.get("drifted") or 0), 0)
        self.assertTrue(seal.get("ok"))

    def test_the_report_does_not_call_it_a_drift(self):
        from mas.report import build_report, render_markdown

        self._approve_an_edit()
        markdown = render_markdown(build_report(self.rows))
        self.assertNotIn("结论已漂移", markdown)
        self.assertIn("人工接手", markdown)

    def test_the_run_keeps_the_take_over_visible(self):
        from mas.report import run_summary

        self._approve_an_edit()
        summary = run_summary(
            [r for r in self.rows if str(r.get("run_id") or "")][0]["run_id"],
            [r for r in self.rows if str(r.get("run_id") or "")][0] and self.rows,
        )
        self.assertEqual(summary.get("seal_state"), SEAL_STATE_TAKEN_OVER)
        self.assertTrue(summary.get("seal_taken_over_files"))


class TheGateTest(unittest.TestCase):
    """The gate is registered, and it can fail."""

    def test_the_gate_is_part_of_the_suite(self):
        from mas.selftest import CHECKS, EXPECTED_CHECKS

        names = [getattr(check, "CHECK_NAME", "") for check in CHECKS]
        self.assertIn("seal_takeover", names)
        self.assertEqual(EXPECTED_CHECKS, len(CHECKS))

    def test_the_gate_holds(self):
        from mas.selftest_seal_takeover import (
            check_an_approved_change_is_not_a_drift,
        )

        outcome = check_an_approved_change_is_not_a_drift(iterations=1)
        self.assertTrue(outcome.get("ok"), outcome.get("failures"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


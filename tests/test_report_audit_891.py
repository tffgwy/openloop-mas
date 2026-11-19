# -*- coding: utf-8 -*-
"""The report reads the decision chains - and says so when they are hurt.

These are the unit tests behind the thirty-first gate. The gate
itself is in ``mas/selftest_report_audit.py``; this file checks the
parts of it that are easier to state one at a time.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest


def _make_workspace(root: pathlib.Path, name: str) -> pathlib.Path:
    """A workspace with one proposed change, approved and taken back."""
    from mas.drafts import approve_draft, stage_draft, undo_draft

    workspace = pathlib.Path(root) / name
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    staged = stage_draft(
        workspace,
        "notes.txt",
        "alpha\nbeta-agent\n".encode("utf-8"),
        reason="the agent proposes this",
    )
    draft_id = str(staged.get("id") or "")
    approve_draft(workspace, draft_id)
    undo_draft(workspace, draft_id, note="approved the wrong thing")
    return workspace


class ReportAuditTest(unittest.TestCase):
    """The report has to read the chains of the human decisions."""

    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-test-report-audit-"))
        self.state = self.root / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.previous = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.state)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self.previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self.previous
        shutil.rmtree(self.root, ignore_errors=True)

    def _rows(self) -> list:
        from mas.ledger import Ledger

        return Ledger(self.state / "ledgers" / "runs.jsonl").entries()

    def test_a_healthy_chain_is_reported_as_intact(self):
        from mas.report import build_report, render_markdown

        _make_workspace(self.root, "healthy")
        report = build_report(self._rows())
        markdown = render_markdown(report)
        self.assertIn("\u5ba1\u8ba1\u94fe\u590d\u6838\uff1a", markdown)
        self.assertIn("\u5168\u90e8\u5b8c\u597d", markdown)
        self.assertTrue(report["audit"]["ok"])
        self.assertEqual(report["totals"]["audit_problems"], 0)

    def test_a_changed_chain_is_named(self):
        from mas.report import build_report, render_markdown

        workspace = _make_workspace(self.root, "changed")
        chain_file = workspace / ".mas-drafts" / "audit.jsonl"
        lines = chain_file.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["note"] = "changed behind the back of the reader"
        lines[1] = json.dumps(entry, ensure_ascii=False)
        chain_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        report = build_report(self._rows())
        markdown = render_markdown(report)
        self.assertIn("\u5185\u5bb9\u88ab\u6539\u8fc7", markdown)
        self.assertFalse(report["audit"]["ok"])
        self.assertGreater(report["totals"]["audit_problems"], 0)

    def test_a_missing_chain_is_named(self):
        from mas.report import build_report, render_markdown

        workspace = _make_workspace(self.root, "missing")
        (workspace / ".mas-drafts" / "audit.jsonl").unlink()

        report = build_report(self._rows())
        markdown = render_markdown(report)
        self.assertIn("\u5ba1\u8ba1\u94fe\u4e0d\u89c1\u4e86", markdown)
        self.assertFalse(report["audit"]["ok"])

    def test_the_payload_carries_the_numbers(self):
        from mas.report import build_report

        _make_workspace(self.root, "healthy")
        report = build_report(self._rows())
        totals = report["totals"]
        self.assertEqual(totals["audit_chains"], 1)
        self.assertGreater(totals["audit_entries"], 0)
        self.assertEqual(totals["audit_problems"], 0)
        self.assertIn("chains", report["audit"])
        self.assertIn("problems", report["audit"])

    def test_a_ledger_without_decisions_has_no_chains_to_report(self):
        from mas.report import build_report, render_markdown

        report = build_report([])
        markdown = render_markdown(report)
        self.assertEqual(report["totals"]["audit_chains"], 0)
        self.assertNotIn("## \u5ba1\u8ba1\u94fe\u590d\u6838", markdown)
        self.assertIn("\u8d26\u672c\u4e3a\u7a7a", markdown)

    def test_the_decision_rows_do_not_become_a_run(self):
        from mas.report import build_report

        _make_workspace(self.root, "healthy")
        report = build_report(self._rows())
        self.assertEqual(len(report["runs"]), 0)


if __name__ == "__main__":
    unittest.main()

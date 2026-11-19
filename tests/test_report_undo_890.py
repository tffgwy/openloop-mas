# -*- coding: utf-8 -*-
"""v0.7.90 tests: the report says which approvals were wrong.

v0.7.78 made an undo leave a trace. v0.7.85 made ``mas undos``
list the taken-back approvals. Neither made the **report** say it:
``run_summary`` never looked at the ``draft_approve`` / ``draft_undo``
rows, because a human decision about a draft is not a run.

So a wrong approval was invisible in the main view - the report
could read "3 closed" while the reader's own bad approvals from
three weeks ago sat in the ledger, unmentioned.

This file covers:

* the headline counts the taken-back approvals;
* the report has a section of its own, naming the draft and the file;
* the JSON payload carries the numbers, so a script can ask without
  parsing Markdown;
* a ledger that holds only decisions is **not** reported as empty -
  saying so would be a false statement about the record;
* the decision rows do not turn into a phantom run;
* an undo with no standing approval is counted as unmatched rather
  than hidden or paired with a neighbour;
* the payload survives a JSON round-trip.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import unittest

from mas.report import build_report, render_markdown


def _decision_rows(draft_id="D-testundo", path="notes.txt"):
    """One approval and one undo, as the ledger holds them."""
    return [
        {
            "role": "draft_approve",
            "event": "draft_approve",
            "run_id": "",
            "draft_id": draft_id,
            "path": path,
            "workspace": "",
            "seq": 1,
            "ts": "2026-11-19T00:00:00Z",
            "note": "looks right",
            "approved_sha256": "a" * 64,
            "current_sha256": "a" * 64,
            "would_lose_count": 0,
            "forced": False,
        },
        {
            "role": "draft_undo",
            "event": "draft_undo",
            "run_id": "",
            "draft_id": draft_id,
            "path": path,
            "workspace": "",
            "seq": 2,
            "ts": "2026-11-19T00:01:00Z",
            "note": "actually wrong",
            "approved_sha256": "a" * 64,
            "current_sha256": "a" * 64,
            "would_lose_count": 0,
            "forced": False,
            "reverted_line_count": 3,
        },
    ]


class _ReportUndoCase(unittest.TestCase):
    def setUp(self):
        self.rows = _decision_rows()
        self.report = build_report(self.rows)
        self.markdown = render_markdown(self.report)

    def test_headline_counts_the_taken_back_approval(self):
        self.assertIn("\u64a4\u9500\u56de\u6536\uff1a1 \u6b21\u6279\u51c6\u88ab\u64a4\u9500", self.markdown)
        self.assertIn("\u9000\u56de 3 \u884c", self.markdown)

    def test_the_report_has_a_section_naming_the_draft(self):
        self.assertIn("## \u88ab\u64a4\u9500\u7684\u6279\u51c6", self.markdown)
        self.assertIn("D-testundo", self.markdown)
        self.assertIn("notes.txt", self.markdown)

    def test_json_carries_the_numbers(self):
        totals = self.report["totals"]
        self.assertEqual(1, totals["approvals_taken_back"])
        self.assertEqual(1, totals["drafts_taken_back"])
        self.assertEqual(3, totals["lines_reverted"])
        self.assertEqual(0, totals["undos_unmatched"])
        drafts = self.report["undos"]["drafts"]
        self.assertEqual(1, len(drafts))
        self.assertEqual("D-testundo", drafts[0]["draft_id"])
        self.assertEqual(1, drafts[0]["taken_back"])
        self.assertEqual(3, drafts[0]["reverted_lines"])

    def test_a_ledger_of_only_decisions_is_not_called_empty(self):
        self.assertNotIn("\u8d26\u672c\u4e3a\u7a7a", self.markdown)
        self.assertIn("\u8fd8\u6ca1\u6709\u8dd1\u8fc7\u4efb\u4f55\u4e00\u6b21\u95ed\u73af", self.markdown)
        self.assertIn("mas undos", self.markdown)

    def test_the_decision_rows_do_not_become_a_run(self):
        self.assertEqual([], self.report["runs"])
        mixed = list(self.rows)
        mixed.append(
            {
                "role": "run_end",
                "run_id": "R-mixed",
                "status": "closed",
                "decision": "pass",
                "goal": "mixed",
                "iterations": 1,
                "tokens": 5,
            }
        )
        mixed_report = build_report(mixed)
        self.assertEqual(1, len(mixed_report["runs"]))
        self.assertEqual(1, mixed_report["totals"]["approvals_taken_back"])
        self.assertIn("## \u88ab\u64a4\u9500\u7684\u6279\u51c6", render_markdown(mixed_report))

    def test_an_unmatched_undo_is_counted_not_hidden(self):
        # The approve row is gone - the undo has nothing standing to
        # cancel. It must show up as unmatched, and must not be
        # counted as a taken-back approval.
        rows = [r for r in self.rows if r["role"] != "draft_approve"]
        report = build_report(rows)
        self.assertEqual(0, report["totals"]["approvals_taken_back"])
        self.assertEqual(1, report["totals"]["undos_unmatched"])
        self.assertEqual(1, len(report["undos"]["unmatched"]))
        markdown = render_markdown(report)
        self.assertIn("1 \u6b21\u64a4\u9500\u627e\u4e0d\u5230\u5bf9\u5e94\u7684\u6279\u51c6", markdown)

    def test_the_payload_survives_a_json_round_trip(self):
        text = json.dumps(self.report, ensure_ascii=False, sort_keys=True)
        again = json.loads(text)
        self.assertEqual(
            self.report["totals"]["approvals_taken_back"],
            again["totals"]["approvals_taken_back"],
        )
        self.assertEqual(
            self.report["undos"]["drafts"][0]["draft_id"],
            again["undos"]["drafts"][0]["draft_id"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

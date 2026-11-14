# -*- coding: utf-8 -*-
"""v0.7.42 regression tests: an unjudged criterion is not a failed one.

The engine keeps three states per criterion - passed, failed, unjudged -
and deliberately never counts an unjudged one as a pass. The report
flattened the last two together, so a criterion nobody could judge was
listed under "failed" and rendered as ``[FAIL]``. The reader was told a
criterion that was never checked had been checked and rejected.
"""

try:  # pragma: no cover
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import unittest

from mas.report import build_report, render_markdown, run_summary


def _rows(scores):
    return [
        {"seq": 1, "ts": "t1", "run_id": "R-u", "task_id": "R-u-1",
         "iteration": 1, "role": "candidates", "count": 1,
         "selected_angle": "direct",
         "candidates": [{"angle": "direct", "selected": True, "passed": 1,
                         "chars": 10, "sha": "aa"}],
         "tokens": 100},
        {"seq": 2, "ts": "t2", "run_id": "R-u", "task_id": "R-u-1",
         "iteration": 1, "role": "closure", "verdict": "fail",
         "decision": "revise", "scores": scores,
         "issues": [f"{s['id']}: {s.get('note','')}" for s in scores
                    if s.get("pass") is False],
         "stagnation": 0, "closed": False, "artifact_sha": "sha1111",
         "tokens_total": 100},
        {"seq": 3, "ts": "t3", "run_id": "R-u", "task_id": "R-u-1",
         "iteration": 1, "role": "run_end", "status": "escalated",
         "decision": "escalate",
         "error": "stagnation: the deliverable did not change for 3 consecutive attempts",
         "iterations": 1, "closed": False, "deliverable_ok": False,
         "ticket_id": "T-1", "artifact_sha": "sha1111",
         "issues": [], "tokens_total": 100},
    ]


class UnjudgedIsNotFailedTest(unittest.TestCase):
    """The three states stay three states."""

    def test_an_unjudged_criterion_is_not_in_the_failed_list(self):
        summary = run_summary("R-u", _rows([
            {"id": "c1", "pass": True, "note": "ok", "source": "rule"},
            {"id": "c2", "pass": None, "note": "no machine check",
             "source": "model-missing"},
        ]))
        failed_ids = [s["id"] for s in summary["failed"]]
        self.assertNotIn("c2", failed_ids)
        self.assertEqual([s["id"] for s in summary["unjudged"]], ["c2"])

    def test_the_unjudged_list_carries_the_criterion(self):
        summary = run_summary("R-u", _rows([
            {"id": "c2", "pass": None, "note": "no machine check",
             "source": "model-missing"},
        ]))
        self.assertEqual(len(summary["unjudged"]), 1)
        self.assertEqual(summary["unjudged"][0]["id"], "c2")
        self.assertEqual(summary["unjudged"][0]["source"], "model-missing")

    def test_a_really_failed_criterion_still_fails(self):
        summary = run_summary("R-u", _rows([
            {"id": "c1", "pass": False, "note": "regex not found", "source": "rule"},
        ]))
        self.assertEqual([s["id"] for s in summary["failed"]], ["c1"])
        self.assertEqual(summary["unjudged"], [])

    def test_an_optional_failure_stays_optional_and_out_of_failed(self):
        summary = run_summary("R-u", _rows([
            {"id": "c1", "pass": False, "note": "nope", "source": "rule",
             "blocking": False},
        ]))
        self.assertEqual(summary["failed"], [])
        self.assertEqual([s["id"] for s in summary["optional_failed"]], ["c1"])

    def test_the_markdown_does_not_say_fail_for_an_unjudged_criterion(self):
        markdown = render_markdown(build_report(_rows([
            {"id": "c1", "pass": True, "note": "ok", "source": "rule"},
            {"id": "c2", "pass": None, "note": "no machine check",
             "source": "model-missing"},
        ])))
        self.assertIn("[UNDECIDED]", markdown)
        self.assertNotIn("[FAIL] `c2`", markdown)
        self.assertIn("[PASS] `c1`", markdown)

    def test_the_markdown_names_the_unjudged_criteria(self):
        markdown = render_markdown(build_report(_rows([
            {"id": "c2", "pass": None, "note": "no machine check",
             "source": "model-missing"},
        ])))
        self.assertIn("无人判定", markdown)
        self.assertIn("`c2`", markdown)
        self.assertIn("model-missing", markdown)

    def test_the_deliverable_line_does_not_claim_a_clean_rejection(self):
        markdown = render_markdown(build_report(_rows([
            {"id": "c2", "pass": None, "note": "no machine check",
             "source": "model-missing"},
        ])))
        # "未通过验收标准" alone would read as "checked and rejected".
        self.assertIn("从未被判定", markdown)
        self.assertNotIn("交付物：❌", markdown)

    def test_a_pure_failure_still_reports_the_hard_rejection(self):
        markdown = render_markdown(build_report(_rows([
            {"id": "c1", "pass": False, "note": "regex not found", "source": "rule"},
        ])))
        self.assertIn("交付物：❌ 未通过验收标准", markdown)
        self.assertNotIn("从未被判定", markdown)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()

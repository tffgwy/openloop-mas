# -*- coding: utf-8 -*-
"""v0.7.43 regression tests: the report has to show the loop.

The engine writes one closure row per iteration and one reflector row
per failed iteration. The report read only the *last* closure row, so
a run that revised itself three times showed a single verdict - the
last one - and the reader could not see the failure that drove the
work. The reflection was not mentioned at all, and the replay's
reflector row was blank.
"""

try:  # pragma: no cover
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import unittest

from mas.report import (
    _short_detail,
    build_report,
    render_markdown,
    replay_trace,
    run_summary,
)


def _rows():
    """A run that failed once, reflected, and then closed."""
    return [
        {"seq": 1, "ts": "t1", "run_id": "R-r", "task_id": "R-r-1",
         "iteration": 1, "role": "candidates", "count": 1,
         "selected_angle": "direct",
         "candidates": [{"angle": "direct", "selected": True, "passed": 0,
                         "chars": 10, "sha": "aa"}],
         "tokens": 100},
        {"seq": 2, "ts": "t2", "run_id": "R-r", "task_id": "R-r-1",
         "iteration": 1, "role": "reflector",
         "reflection": {"summary": "缺少标题行", "issues": ["c1: no title"],
                        "next_action": "revise"},
         "tokens": 30},
        {"seq": 3, "ts": "t3", "run_id": "R-r", "task_id": "R-r-1",
         "iteration": 1, "role": "closure", "verdict": "fail",
         "decision": "revise",
         "scores": [{"id": "c1", "pass": False, "note": "no title"}],
         "issues": ["c1: no title"], "stagnation": 0, "closed": False,
         "artifact_sha": "sha1111111111111111", "tokens_total": 130},
        {"seq": 4, "ts": "t4", "run_id": "R-r", "task_id": "R-r-1",
         "iteration": 2, "role": "candidates", "count": 1,
         "selected_angle": "direct",
         "candidates": [{"angle": "direct", "selected": True, "passed": 1,
                         "chars": 20, "sha": "bb"}],
         "tokens": 100},
        {"seq": 5, "ts": "t5", "run_id": "R-r", "task_id": "R-r-1",
         "iteration": 2, "role": "closure", "verdict": "pass",
         "decision": "close",
         "scores": [{"id": "c1", "pass": True, "note": "ok"}],
         "issues": [], "stagnation": 0, "closed": True,
         "artifact_sha": "sha2222222222222222", "tokens_total": 230},
        {"seq": 6, "ts": "t6", "run_id": "R-r", "task_id": "R-r-1",
         "iteration": 2, "role": "run_end", "status": "closed",
         "decision": "close", "error": "", "iterations": 2, "closed": True,
         "deliverable_ok": True, "ticket_id": "",
         "artifact_sha": "sha2222222222222222", "issues": [],
         "tokens_total": 230},
    ]


class TheReportShowsTheLoopTest(unittest.TestCase):
    """A report of a loop must show the loop."""

    def test_the_summary_carries_every_round(self):
        summary = run_summary("R-r", _rows())
        self.assertEqual(summary["rounds"], 2)
        self.assertEqual(len(summary["timeline"]), 2)

    def test_the_timeline_keeps_the_failed_round(self):
        summary = run_summary("R-r", _rows())
        first = summary["timeline"][0]
        self.assertEqual(first["verdict"], "fail")
        self.assertEqual(first["passed"], 0)
        self.assertEqual(first["total"], 1)
        self.assertEqual(first["issues"], ["c1: no title"])
        self.assertFalse(first["closed"])

    def test_the_timeline_keeps_the_passing_round(self):
        summary = run_summary("R-r", _rows())
        second = summary["timeline"][1]
        self.assertEqual(second["verdict"], "pass")
        self.assertEqual(second["passed"], 1)
        self.assertTrue(second["closed"])

    def test_the_reflection_is_attached_to_the_round_it_produced(self):
        summary = run_summary("R-r", _rows())
        self.assertIn("缺少标题行", summary["timeline"][0]["reflection"])
        self.assertEqual(summary["timeline"][1]["reflection"], "")

    def test_the_summary_lists_the_reflections(self):
        summary = run_summary("R-r", _rows())
        self.assertEqual(len(summary["reflections"]), 1)
        self.assertEqual(summary["reflections"][0]["summary"], "缺少标题行")
        self.assertEqual(summary["reflections"][0]["next_action"], "revise")

    def test_the_markdown_shows_every_round(self):
        markdown = render_markdown(build_report(_rows()))
        self.assertIn("循环过程（共 2 轮）", markdown)
        self.assertIn("第 1 轮 [FAIL]", markdown)
        self.assertIn("第 2 轮 [PASS]", markdown)

    def test_the_markdown_shows_the_failure_of_the_first_round(self):
        markdown = render_markdown(build_report(_rows()))
        self.assertIn("c1: no title", markdown)

    def test_the_markdown_shows_the_reflection(self):
        markdown = render_markdown(build_report(_rows()))
        self.assertIn("反思", markdown)
        self.assertIn("缺少标题行", markdown)

    def test_a_single_round_says_so(self):
        rows = [r for r in _rows() if r.get("iteration") == 2]
        markdown = render_markdown(build_report(rows))
        self.assertIn("1 轮即定", markdown)

    def test_the_replay_reflector_row_is_not_blank(self):
        detail = _short_detail({
            "role": "reflector",
            "reflection": {"summary": "缺少标题行", "issues": ["c1: no title"],
                           "next_action": "revise"},
        })
        self.assertNotEqual(detail.strip(), "")
        self.assertIn("缺少标题行", detail)
        self.assertIn("revise", detail)

    def test_the_replay_trace_carries_the_reflection(self):
        trace = replay_trace(_rows())
        reflector_steps = [s for s in trace["steps"] if s["role"] == "reflector"]
        self.assertEqual(len(reflector_steps), 1)
        self.assertIn("缺少标题行", reflector_steps[0]["detail"])

    def test_a_run_without_reflections_has_no_reflection_section(self):
        rows = [r for r in _rows() if r["role"] != "reflector"]
        summary = run_summary("R-r", rows)
        self.assertEqual(summary["reflections"], [])
        self.assertEqual(summary["timeline"][0]["reflection"], "")


if __name__ == "__main__":
    unittest.main()

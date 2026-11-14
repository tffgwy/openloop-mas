"""Regression tests for the v0.7.33 replay summary.

The replay trace is what a reviewer walks through to see what the loop
did. The closure row - the line that says what was judged and how it
came out - showed an empty summary for every closed run, because its
summary was built from the issues and a closed run has none. The most
important line of the replay was a blank.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import json
import tempfile
import unittest

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.report import _short_detail, replay_trace


class _Provider:
    """A provider that closes the loop on the first attempt."""

    name = "provider"
    model = "provider-1"

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            return {"text": "交付物 正文", "usage": {"total_tokens": 100}, "raw": {}}
        if role == "planner":
            return {"text": json.dumps({"summary": "先写结论", "plan": ["写"],
                                        "risks": []}),
                    "usage": {"total_tokens": 10}, "raw": {}}
        if role == "reflector":
            return {"text": json.dumps({"summary": "r", "issues": [],
                                        "next_action": "revise"}),
                    "usage": {"total_tokens": 20}, "raw": {}}
        if role == "verifier":
            return {"text": json.dumps({"verdict": "pass", "scores": []}),
                    "usage": {"total_tokens": 30}, "raw": {}}
        return {"text": "{}", "usage": {"total_tokens": 5}, "raw": {}}

    def describe(self):
        return {"provider": self.name, "model": self.model}


class ReplaySummaryTest(unittest.TestCase):
    """Every role of a replay has to say something readable."""

    def test_a_closed_closure_row_is_not_blank(self):
        detail = _short_detail({
            "role": "closure",
            "verdict": "pass",
            "decision": "close",
            "issues": [],
            "scores": [{"id": "c1", "pass": True, "note": "内容达标"}],
        })
        self.assertTrue(detail.strip(),
                        "a closed run must not replay as a blank line")
        self.assertIn("判定=pass", detail)
        self.assertIn("决策=close", detail)

    def test_the_closure_row_names_the_criteria(self):
        detail = _short_detail({
            "role": "closure",
            "verdict": "fail",
            "decision": "revise",
            "issues": ["c2: 缺少结论"],
            "scores": [
                {"id": "c1", "pass": True},
                {"id": "c2", "pass": False, "note": "缺少结论"},
            ],
        })
        self.assertIn("通过 1/2", detail)
        self.assertIn("未过 c2", detail)
        self.assertIn("缺少结论", detail)

    def test_the_closure_row_names_a_failed_criterion(self):
        detail = _short_detail({
            "role": "closure",
            "verdict": "fail",
            "decision": "revise",
            "issues": [],
            "scores": [{"id": "c1", "pass": False, "note": "跑题"}],
        })
        self.assertIn("未过 c1", detail)
        self.assertIn("跑题", detail)

    def test_the_run_end_row_names_the_status(self):
        detail = _short_detail({
            "role": "run_end",
            "status": "budget_exhausted",
            "decision": "escalate",
            "error": "budget: tokens 100 >= 100",
        })
        self.assertIn("status=budget_exhausted", detail)
        self.assertIn("budget: tokens", detail)

    def test_every_role_of_a_real_run_has_a_summary(self):
        # A whole replay: no row may be a blank cell.
        rows = [
            {"seq": 1, "run_id": "R-1", "role": "planner",
             "plan": {"summary": "先界定概念"}},
            {"seq": 2, "run_id": "R-1", "role": "candidates",
             "candidates": [{"angle": "direct", "selected": True},
                            {"angle": "thorough", "selected": False}]},
            {"seq": 3, "run_id": "R-1", "role": "review",
             "mode": "all", "usable": 2, "skipped": 0, "missing": 0},
            {"seq": 4, "run_id": "R-1", "role": "closure",
             "verdict": "pass", "decision": "close", "issues": [],
             "scores": [{"id": "c1", "pass": True}]},
            {"seq": 5, "run_id": "R-1", "role": "run_end",
             "status": "closed", "decision": "close"},
        ]
        trace = replay_trace(rows)
        for step in trace["steps"]:
            self.assertTrue(
                str(step.get("detail") or "").strip(),
                f"the replay row of {step.get('role')} is blank",
            )

    def test_the_replay_of_a_real_closed_run_has_no_blank_row(self):
        # End to end: a real run, a real ledger, a real replay. This is
        # the case that was broken - the closure row of a closed run
        # replayed as a blank line.
        tmp = tempfile.mkdtemp()
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tmp
        config["budget"]["max_iterations"] = 3
        config["budget"]["max_tokens"] = 1000000
        config["orchestration"] = {
            "parallel_workers": 1, "reviewers": 1, "review_mode": "all",
        }
        engine = LoopEngine(config, provider=_Provider())
        result = engine.run(
            "写一份说明",
            [{"id": "c1", "desc": "内容够好",
              "check": {"type": "contains", "arg": "正文"}}],
        )
        self.assertTrue(result.closed, result)
        trace = replay_trace(engine.ledger.entries())
        for step in trace["steps"]:
            self.assertTrue(
                str(step.get("detail") or "").strip(),
                f"the replay row of {step.get('role')} is blank",
            )
        closures = [s for s in trace["steps"] if s["role"] == "closure"]
        self.assertTrue(closures)
        self.assertIn("判定=pass", closures[-1]["detail"])

    def test_the_closure_row_is_the_last_word_of_a_closed_run(self):
        # The closure row and the run_end row must agree about the
        # outcome - the replay reads as one story, not two.
        rows = [
            {"seq": 1, "run_id": "R-1", "role": "closure",
             "verdict": "pass", "decision": "close", "issues": [],
             "scores": [{"id": "c1", "pass": True}]},
            {"seq": 2, "run_id": "R-1", "role": "run_end",
             "status": "closed", "decision": "close"},
        ]
        trace = replay_trace(rows)
        self.assertIn("判定=pass", trace["steps"][0]["detail"])
        self.assertIn("status=closed", trace["steps"][1]["detail"])



class TestIterationsIsAlwaysANumber(unittest.TestCase):
    """A report field that is sometimes a number and sometimes ``null``
    cannot be read by a script - and a run that crashed before its
    first iteration is exactly the case a user looks at.
    """

    def test_a_run_without_an_iteration_field_reports_zero(self):
        from mas.report import run_summary

        rows = [{"run_id": "R-1", "role": "planner"}]
        summary = run_summary("R-1", rows)
        self.assertEqual(summary["iterations"], 0)
        self.assertIsInstance(summary["iterations"], int)

    def test_a_run_without_a_closure_row_reports_the_last_iteration(self):
        from mas.report import run_summary

        rows = [
            {"run_id": "R-2", "role": "planner", "iteration": 1},
            {"run_id": "R-2", "role": "error", "iteration": 3},
        ]
        summary = run_summary("R-2", rows)
        self.assertEqual(summary["iterations"], 3)

    def test_the_engine_end_entry_wins_over_the_closure_row(self):
        from mas.report import run_summary

        rows = [
            {"run_id": "R-3", "role": "closure", "iteration": 2,
             "closed": False, "verdict": "fail"},
            {"run_id": "R-3", "role": "run_end", "iteration": 5,
             "status": "escalated", "iterations": 5, "closed": False},
        ]
        summary = run_summary("R-3", rows)
        self.assertEqual(summary["iterations"], 5)

    def test_an_empty_run_reports_zero(self):
        from mas.report import run_summary

        summary = run_summary("R-4", [])
        self.assertEqual(summary["iterations"], 0)


if __name__ == "__main__":
    unittest.main()

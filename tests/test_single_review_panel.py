"""Regression tests for the v0.7.33 single-reviewer panel.

A single reviewer is a panel of one. The single-reviewer path used to
return the raw verdict, so the ledger and the report showed
"usable: None, skipped: None, missing: None" - a reader could not tell
whether the one reviewer had answered, whether it had stayed silent, or
whether the review had not run at all.
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
from mas.report import run_summary


class _Provider:
    """A provider whose verifier either answers or stays silent."""

    name = "provider"
    model = "provider-1"

    def __init__(self, verdict=None):
        self.verdict = verdict

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            return {"text": "交付物 正文", "usage": {"total_tokens": 100}, "raw": {}}
        if role == "planner":
            return {"text": json.dumps({"summary": "s", "plan": [], "risks": []}),
                    "usage": {"total_tokens": 10}, "raw": {}}
        if role == "reflector":
            return {"text": json.dumps({"summary": "r", "issues": [],
                                        "next_action": "revise"}),
                    "usage": {"total_tokens": 20}, "raw": {}}
        if role == "verifier":
            if self.verdict is None:
                # The reviewer answered, but said nothing that could be
                # judged. That is a missing vote, not a passing one.
                return {"text": json.dumps({"summary": "看起来还行"}),
                        "usage": {"total_tokens": 30}, "raw": {}}
            return {"text": json.dumps(self.verdict),
                    "usage": {"total_tokens": 30}, "raw": {}}
        return {"text": "{}", "usage": {"total_tokens": 5}, "raw": {}}

    def describe(self):
        return {"provider": self.name, "model": self.model}


class SingleReviewerPanelTest(unittest.TestCase):
    """The one-reviewer mode must describe itself like a panel of one."""

    def _run(self, verdict=None, reviewers=1):
        tmp = tempfile.mkdtemp()
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tmp
        config["budget"]["max_iterations"] = 3
        config["budget"]["max_tokens"] = 1000000
        config["orchestration"] = {
            "parallel_workers": 1, "reviewers": reviewers, "review_mode": "all",
        }
        engine = LoopEngine(config, provider=_Provider(verdict))
        result = engine.run(
            "写一份说明",
            [{"id": "c1", "desc": "内容够好",
              "check": {"type": "contains", "arg": "正文"}}],
        )
        return result, engine

    def _review_entry(self, engine):
        entries = [r for r in engine.ledger.entries() if r.get("role") == "review"]
        self.assertTrue(entries, "the ledger must record the review")
        return entries[-1]

    def test_the_review_entry_has_numbers_not_none(self):
        result, engine = self._run()
        review = self._review_entry(engine)
        for field in ("usable", "skipped", "missing"):
            self.assertIsNotNone(
                review.get(field),
                f"{field} is None - the reader cannot tell what happened",
            )
            self.assertIsInstance(review.get(field), int)
        # The reviewer answered with a verdict for a criterion, so the
        # run closed; the numbers have to agree with that.
        self.assertTrue(result.closed, result)

    def test_a_silent_reviewer_is_counted_as_missing(self):
        result, engine = self._run(verdict=None)
        review = self._review_entry(engine)
        self.assertEqual(review.get("usable"), 0)
        self.assertEqual(review.get("skipped"), 1)
        self.assertEqual(review.get("missing"), 1)
        # The machine check decided this run, not the reviewer. The
        # criterion carries a rule, so the rule wins and the loop closes
        # - while the panel numbers still say that the reviewer itself
        # produced nothing. Both facts are true at once, and both are
        # reported.
        self.assertTrue(result.closed, result)

    def test_an_answering_reviewer_is_counted_as_usable(self):
        _result, engine = self._run(
            verdict={"verdict": "pass",
                     "scores": [{"id": "c1", "pass": True, "note": "ok"}]},
        )
        review = self._review_entry(engine)
        self.assertEqual(review.get("usable"), 1)
        self.assertEqual(review.get("skipped"), 0)
        self.assertEqual(review.get("missing"), 0)

    def test_a_reviewer_without_a_judgement_is_skipped_not_usable(self):
        # A reviewer that answered but produced nothing that could be
        # judged is skipped, exactly as it is in a panel. The one
        # reviewer mode must not read it as a pass.
        result, engine = self._run(verdict={"verdict": "pass", "scores": []})
        review = self._review_entry(engine)
        self.assertEqual(review.get("usable"), 0)
        self.assertEqual(review.get("skipped"), 1)
        self.assertEqual(review.get("missing"), 1)
        self.assertTrue(result.closed, result)

    def test_the_report_shows_the_numbers(self):
        result, engine = self._run()
        summary = run_summary(result.run_id, engine.ledger.entries())
        review = summary["review"]
        self.assertIsNotNone(review["usable"])
        self.assertIsNotNone(review["skipped"])
        self.assertIsNotNone(review["missing"])

    def test_the_two_modes_read_the_same_way(self):
        # A panel of two and a panel of one produce the same fields, so
        # a reader does not have to learn two different shapes.
        scored = {"verdict": "pass",
                  "scores": [{"id": "c1", "pass": True, "note": "ok"}]}
        _result1, engine1 = self._run(verdict=scored)
        _result2, engine2 = self._run(verdict=scored, reviewers=2)
        one = self._review_entry(engine1)
        two = self._review_entry(engine2)
        self.assertEqual(set(one) & set(two) >= {"usable", "skipped", "missing"},
                         True)

    def test_the_closure_is_not_changed_by_the_description(self):
        # The panel description is a description. It must not change
        # whether the loop closes.
        result, _engine = self._run(
            verdict={"verdict": "pass",
                     "scores": [{"id": "c1", "pass": True, "note": "ok"}]},
        )
        self.assertTrue(result.closed, result)


if __name__ == "__main__":
    unittest.main()

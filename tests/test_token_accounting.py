"""Regression tests for the v0.7.31 token accounting.

The ledger has to answer "how much did this run spend" on its own.
Before this version the figure only existed in the engine's memory:
the replay report could not show it, and a report built from the
ledger understated the cost of a run.
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
from mas.providers import ProviderError, TruncatedAnswer
from mas.report import build_report, ledger_spend, render_markdown, run_summary


def make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


class _Billing:
    """A provider that knows exactly how much it charged.

    Every call adds its price to ``spent``, so the test can compare the
    bill of the endpoint with the book of the ledger.
    """

    name = "billing"

    def __init__(self):
        self.model = "billing-1"
        self.spent = 0

    def _bill(self, amount):
        self.spent += amount
        return {"total_tokens": amount}

    def describe(self):
        return {"provider": self.name, "model": self.model}


class _Plain(_Billing):
    """Answers every role."""

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            return {"text": "交付物 正文", "usage": self._bill(100), "raw": {}}
        if role == "planner":
            return {"text": json.dumps({"summary": "s", "plan": [], "risks": []}),
                    "usage": self._bill(10), "raw": {}}
        if role == "reflector":
            return {"text": json.dumps(
                {"summary": "r", "issues": [], "next_action": "revise"}),
                "usage": self._bill(20), "raw": {}}
        if role == "verifier":
            return {"text": json.dumps({"verdict": "pass", "scores": []}),
                    "usage": self._bill(30), "raw": {}}
        return {"text": "{}", "usage": self._bill(5), "raw": {}}


class _ReAsk(_Plain):
    """The verifier stays silent until it is re-asked."""

    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "verifier":
            if "missing_criteria" in prompt:
                if self.fail:
                    self.spent += 500
                    raise ProviderError("reask boom", usage={"total_tokens": 500})
                return {"text": json.dumps({
                    "verdict": "pass",
                    "scores": [{"id": "c1", "pass": True, "note": "ok"}]}),
                    "usage": self._bill(100), "raw": {}}
            return {"text": json.dumps({"verdict": "pass", "scores": []}),
                    "usage": self._bill(30), "raw": {}}
        return super().complete(system=system, prompt=prompt, role=role,
                                expect_json=expect_json, max_tokens=max_tokens)


class _Truncated(_Billing):
    """The worker always runs out of output room."""

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            self.spent += 700
            raise TruncatedAnswer("cut off", usage={"total_tokens": 700})
        return {"text": "{}", "usage": self._bill(10), "raw": {}}


class _Broken(_Billing):
    """The worker answers and then breaks."""

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            self.spent += 800
            raise ProviderError("bad shape", usage={"total_tokens": 800})
        return {"text": "{}", "usage": self._bill(10), "raw": {}}


class TokenAccountingTest(unittest.TestCase):
    """The ledger must reconcile with the bill of the endpoint."""

    def _run(self, provider, workers=1, reviewers=1, criteria=None,
             max_iterations=3):
        tmp = tempfile.mkdtemp()
        config = make_config(tmp)
        config["budget"]["max_iterations"] = max_iterations
        config["budget"]["max_tokens"] = 1000000
        config["orchestration"] = {
            "parallel_workers": workers,
            "reviewers": reviewers,
            "review_mode": "all",
        }
        engine = LoopEngine(config, provider=provider)
        crits = criteria or [{"id": "c1", "desc": "内容够好"}]
        result = engine.run("写一份说明", crits)
        return result, engine

    def _assert_reconciles(self, provider, result, engine):
        rows = engine.ledger.entries()
        booked = ledger_spend(rows)
        self.assertEqual(
            booked, result.tokens_used,
            "the ledger must book exactly what the engine charged",
        )
        self.assertEqual(
            booked, provider.spent,
            "the ledger must book exactly what the endpoint billed",
        )

    def test_a_plain_run_reconciles(self):
        provider = _Plain()
        result, engine = self._run(provider)
        self._assert_reconciles(provider, result, engine)

    def test_a_parallel_pool_reconciles(self):
        provider = _Plain()
        result, engine = self._run(provider, workers=3)
        self._assert_reconciles(provider, result, engine)

    def test_a_panel_reconciles(self):
        provider = _Plain()
        result, engine = self._run(provider, reviewers=3)
        self._assert_reconciles(provider, result, engine)

    def test_a_follow_up_question_reconciles(self):
        provider = _ReAsk()
        result, engine = self._run(provider)
        self.assertTrue(result.closed, result.status)
        self._assert_reconciles(provider, result, engine)

    def test_a_failed_follow_up_reconciles(self):
        provider = _ReAsk(fail=True)
        result, engine = self._run(provider)
        self._assert_reconciles(provider, result, engine)

    def test_a_failed_follow_up_in_a_panel_reconciles(self):
        provider = _ReAsk(fail=True)
        result, engine = self._run(provider, reviewers=3)
        self._assert_reconciles(provider, result, engine)

    def test_a_truncated_worker_reconciles(self):
        provider = _Truncated()
        result, engine = self._run(provider)
        self._assert_reconciles(provider, result, engine)

    def test_a_broken_worker_reconciles(self):
        provider = _Broken()
        result, engine = self._run(provider)
        self.assertEqual(result.status, "error")
        self._assert_reconciles(provider, result, engine)

    def test_a_broken_pool_reconciles(self):
        provider = _Broken()
        result, engine = self._run(provider, workers=3)
        self.assertEqual(result.status, "error")
        self._assert_reconciles(provider, result, engine)

    def test_the_single_worker_path_is_booked_too(self):
        # The single worker path wrote no ledger entry at all, so the
        # money it spent was nowhere in the book.
        provider = _Plain()
        _result, engine = self._run(provider)
        rows = [r for r in engine.ledger.entries() if r.get("role") == "candidates"]
        self.assertTrue(rows, "the worker call must be booked")
        self.assertEqual(int(rows[-1].get("tokens") or 0), 100)

    def test_the_reflector_is_booked_too(self):
        # The reflector only runs when a criterion failed. It is a paid
        # call and the ledger never mentioned it.
        provider = _Plain()
        tmp = tempfile.mkdtemp()
        config = make_config(tmp)
        config["budget"]["max_iterations"] = 2
        config["budget"]["max_tokens"] = 1000000
        config["orchestration"] = {
            "parallel_workers": 1, "reviewers": 1, "review_mode": "all",
        }
        engine = LoopEngine(config, provider=provider)
        # A criterion with a machine check that never passes: the run
        # fails, the reflector runs.
        engine.run("写一份说明", [
            {"id": "c1", "desc": "永远不满足", "check": {"type": "contains", "arg": "不可能出现"}},
        ])
        rows = [r for r in engine.ledger.entries() if r.get("role") == "reflector"]
        self.assertTrue(rows, "the reflector call must be booked")
        self.assertEqual(int(rows[-1].get("tokens") or 0), 20)

    def test_the_report_shows_the_spend(self):
        provider = _Plain()
        result, engine = self._run(provider)
        rows = engine.ledger.entries()
        report = build_report(rows)
        self.assertEqual(report["totals"]["tokens"], result.tokens_used)
        self.assertEqual(report["runs"][0]["tokens"], result.tokens_used)
        markdown = render_markdown(report)
        self.assertIn("token", markdown)
        self.assertIn(str(result.tokens_used), markdown)

    def test_run_summary_answers_the_spend(self):
        rows = [
            {"run_id": "R-1", "role": "candidates", "tokens": 100},
            {"run_id": "R-1", "role": "review", "tokens": 30},
            {"run_id": "R-1", "role": "closure", "tokens_total": 130},
        ]
        summary = run_summary("R-1", rows)
        self.assertEqual(summary["tokens"], 130)

    def test_the_closure_checkpoint_is_not_double_counted(self):
        # The closure entry carries the running total, not a charge.
        # Summing it on top of the call entries would double the bill.
        rows = [
            {"run_id": "R-1", "role": "candidates", "tokens": 100},
            {"run_id": "R-1", "role": "review", "tokens": 30},
            {"run_id": "R-1", "role": "closure", "tokens_total": 130},
        ]
        self.assertEqual(ledger_spend(rows), 130)


if __name__ == "__main__":
    unittest.main()

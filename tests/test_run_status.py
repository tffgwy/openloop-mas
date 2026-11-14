"""Regression tests for the v0.7.32 run status in the report.

The ledger has to say how a run ended, and the report has to say the
same. The report used to *guess* the status from the closure rows, and
the guess was wrong for every run that did not end on a decision: a run
that stopped on the budget ceiling was reported as "not closed" - as if
it were merely unfinished - and a run that died on a provider error was
reported as "incomplete".
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
from mas.report import build_report, render_markdown, run_summary


def make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


class _Provider:
    name = "provider"
    model = "provider-1"

    def __init__(self, mode="ok"):
        self.mode = mode

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            if self.mode == "fail":
                raise ProviderError("bad shape", usage={"total_tokens": 800})
            if self.mode == "trunc":
                raise TruncatedAnswer("cut", usage={"total_tokens": 700})
            return {"text": "交付物 正文", "usage": {"total_tokens": 100}, "raw": {}}
        if role == "planner":
            return {"text": json.dumps({"summary": "s", "plan": [], "risks": []}),
                    "usage": {"total_tokens": 10}, "raw": {}}
        if role == "reflector":
            return {"text": json.dumps(
                {"summary": "r", "issues": [], "next_action": "revise"}),
                "usage": {"total_tokens": 20}, "raw": {}}
        if role == "verifier":
            if self.mode == "review_fail":
                # The artifact exists, but the reviewer dies. The run
                # ends in an error *after* the deliverable was made.
                raise ProviderError("reviewer down", usage={"total_tokens": 30})
            return {"text": json.dumps({"verdict": "pass", "scores": []}),
                    "usage": {"total_tokens": 30}, "raw": {}}
        return {"text": "{}", "usage": {"total_tokens": 5}, "raw": {}}

    def describe(self):
        return {"provider": self.name, "model": self.model}


class RunStatusTest(unittest.TestCase):
    """The report must state the status the engine actually ended with."""

    def _run(self, mode="ok", max_iterations=3, max_tokens=1000000,
             closing=True):
        tmp = tempfile.mkdtemp()
        config = make_config(tmp)
        config["budget"]["max_iterations"] = max_iterations
        config["budget"]["max_tokens"] = max_tokens
        config["orchestration"] = {
            "parallel_workers": 1, "reviewers": 1, "review_mode": "all",
        }
        engine = LoopEngine(config, provider=_Provider(mode))
        if closing:
            # A machine check the worker satisfies: the run closes.
            criteria = [{"id": "c1", "desc": "内容够好",
                        "check": {"type": "contains", "arg": "正文"}}]
        else:
            # A check nothing satisfies: the run never closes.
            criteria = [{"id": "c1", "desc": "永远不满足",
                        "check": {"type": "contains", "arg": "不可能出现"}}]
        result = engine.run("写一份说明", criteria)
        return result, engine

    def _assert_report_agrees(self, result, engine):
        summary = run_summary(result.run_id, engine.ledger.entries())
        self.assertEqual(
            summary["status"], result.status,
            "the report must state the status the run ended with",
        )
        return summary

    def test_a_provider_error_is_reported_as_an_error(self):
        result, engine = self._run(mode="fail")
        self.assertEqual(result.status, "error")
        summary = self._assert_report_agrees(result, engine)
        self.assertIn("provider error", summary["error"])

    def test_a_budget_stop_is_not_reported_as_unfinished(self):
        # The old report said "not closed" for a run that had *stopped*.
        result, engine = self._run(max_iterations=1, closing=False)
        self.assertEqual(result.status, "budget_exhausted")
        summary = self._assert_report_agrees(result, engine)
        self.assertNotEqual(
            summary["status"], "not_closed",
            "a run that stopped on the budget is not merely unfinished",
        )

    def test_a_truncated_run_reports_its_real_status(self):
        result, engine = self._run(mode="trunc")
        self._assert_report_agrees(result, engine)

    def test_a_closed_run_is_reported_as_closed(self):
        result, engine = self._run()
        summary = self._assert_report_agrees(result, engine)
        self.assertTrue(summary["closed"])

    def test_the_ledger_records_how_the_run_ended(self):
        result, engine = self._run(mode="fail")
        ends = [r for r in engine.ledger.entries() if r.get("role") == "run_end"]
        self.assertTrue(ends, "the ledger must say how the run ended")
        self.assertEqual(ends[-1].get("status"), result.status)
        self.assertEqual(ends[-1].get("closed"), False)

    def test_the_ledger_records_the_end_of_a_closed_run(self):
        _result, engine = self._run()
        ends = [r for r in engine.ledger.entries() if r.get("role") == "run_end"]
        self.assertTrue(ends)
        self.assertEqual(ends[-1].get("status"), "closed")
        self.assertTrue(ends[-1].get("closed"))

    def test_the_run_end_entry_carries_the_spend(self):
        result, engine = self._run()
        ends = [r for r in engine.ledger.entries() if r.get("role") == "run_end"]
        self.assertEqual(int(ends[-1].get("tokens_total") or 0), result.tokens_used)

    def test_the_report_shows_the_reason(self):
        _result, engine = self._run(mode="fail")
        report = build_report(engine.ledger.entries())
        markdown = render_markdown(report)
        self.assertIn("error", markdown)
        self.assertIn("provider error", markdown)

    def test_the_report_shows_the_status_of_a_stopped_run(self):
        # A run that stopped on the budget ceiling has a status the
        # report used to flatten into "not closed". The markdown must
        # name the real status.
        _result, engine = self._run(max_iterations=1, closing=False)
        report = build_report(engine.ledger.entries())
        markdown = render_markdown(report)
        self.assertIn("budget_exhausted", markdown)
        self.assertNotIn("not_closed", markdown)

    def test_the_end_entry_wins_over_the_closure_rows(self):
        # The closure row describes the last scored iteration. The end
        # entry describes the run. Where both speak, the end entry is
        # the one the engine stands behind.
        result, engine = self._run(mode="fail")
        summary = run_summary(result.run_id, engine.ledger.entries())
        self.assertFalse(summary["closed"])
        self.assertFalse(summary["issues"] is None)

    def test_a_run_that_made_an_artifact_keeps_it_in_the_report(self):
        # The reviewer died after the deliverable was written. The run
        # ends in an error, and there is no closure row at all - so a
        # report that reads only the closure rows shows an empty
        # artifact and the reader cannot find out what was actually
        # made. The end entry is the engine's own account of the run.
        result, engine = self._run(mode="review_fail")
        self.assertEqual(result.status, "error")
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][-1]
        self.assertTrue(end.get("artifact_sha"),
                        "the end entry must carry the artifact of the run")
        self.assertFalse(
            [r for r in rows if r.get("role") == "closure"],
            "this scenario has no closure row to read the artifact from",
        )
        summary = run_summary(result.run_id, rows)
        self.assertEqual(summary["artifact_sha"], end["artifact_sha"])
        self.assertFalse(summary["closed"])

    def test_a_run_that_stopped_on_a_budget_keeps_its_artifact(self):
        # A run that ran out of budget still produced an artifact.
        result, engine = self._run(max_iterations=1, closing=False)
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][-1]
        self.assertTrue(end.get("artifact_sha"))
        summary = run_summary(result.run_id, rows)
        self.assertEqual(summary["artifact_sha"], end["artifact_sha"])
        self.assertFalse(summary["closed"])

    def test_the_end_entry_carries_the_unresolved_issues(self):
        # The issues of a run that did not close are the reason it did
        # not close. They have to survive into the report.
        result, engine = self._run(max_iterations=1, closing=False)
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][-1]
        summary = run_summary(result.run_id, rows)
        self.assertEqual(summary["issues"], list(end.get("issues") or []))

    def test_the_ledger_table_says_how_the_run_ended(self):
        from mas.report import ledger_markdown
        _result, engine = self._run(mode="fail")
        table = ledger_markdown(engine.ledger.entries())
        self.assertIn("run_end", table)
        self.assertIn("status=error", table)

    def test_an_old_ledger_without_the_end_entry_still_reads(self):
        # A ledger written by an older version has no run_end entry. The
        # report must fall back to the old behaviour instead of failing.
        rows = [
            {"run_id": "R-1", "role": "closure", "verdict": "pass",
             "decision": "close", "closed": True, "iteration": 1},
        ]
        summary = run_summary("R-1", rows)
        self.assertEqual(summary["status"], "closed")


if __name__ == "__main__":
    unittest.main()

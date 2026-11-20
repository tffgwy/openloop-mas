# -*- coding: utf-8 -*-
"""v0.7.98: one report must not hold two answers.

The new capability
------------------
The closure verdict is counted at the grain of the **problem**, not
the attempt. A problem that failed once and then succeeded is closed -
the later attempt superseded the earlier one - so it no longer holds
the loop open.

Before this, the report's threads said "closed" while the verdict said
"not closed", and a resumed problem could never reach ``ok=True``.
"""

import os
import sys
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


from mas.report import (
    build_report,
    closure_verdict,
    closure_verdict_for,
    format_closure,
)


def _rows(spec):
    """Ledger rows for a list of ``(run_id, closed, ticket_id)``."""
    out = []
    seq = 0
    for run_id, closed, ticket_id in spec:
        seq += 1
        out.append(
            {
                "role": "closure",
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "iteration": 1,
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": seq,
                "verdict": "pass" if closed else "fail",
                "decision": "accept" if closed else "escalate",
                "scores": [{"id": "c1", "pass": closed}],
            }
        )
        seq += 1
        out.append(
            {
                "role": "run_end",
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "iteration": 1,
                "ts": "2026-01-01T00:00:01+00:00",
                "seq": seq,
                "status": "closed" if closed else "escalated",
                "closed": closed,
                "deliverable_ok": closed,
                "decision": "accept" if closed else "escalate",
                "error": "",
                "ticket_id": ticket_id,
                "goal": "把事情做完",
                "title": "把事情做完",
                "iterations": 1,
                "tokens": 10,
                "artifact_path": "",
                "files": [],
            }
        )
    return out


SUPERSEDED = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]
STILL_OPEN = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


class ClosureSupersedeTest(unittest.TestCase):
    """A failure that was superseded must not hold the loop open."""

    def test_a_failure_that_was_superseded_does_not_hold_the_loop_open(self):
        report = build_report(_rows(SUPERSEDED), [], [])
        verdict = closure_verdict(report)
        self.assertTrue(
            report["threads"][0]["closed"], report["threads"]
        )
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(verdict["reasons"], [], verdict["reasons"])

    def test_a_problem_whose_last_attempt_failed_is_still_open(self):
        report = build_report(_rows(STILL_OPEN), [], [])
        verdict = closure_verdict(report)
        self.assertFalse(report["threads"][0]["closed"], report["threads"])
        self.assertFalse(verdict["ok"], verdict)
        keys = {r["reason"] for r in verdict["reasons"]}
        self.assertIn("problems_open", keys)

    def test_the_report_and_the_verdict_never_disagree(self):
        for spec in (SUPERSEDED, STILL_OPEN):
            report = build_report(_rows(spec), [], [])
            verdict = closure_verdict(report)
            report_closed = all(t["closed"] for t in report["threads"])
            self.assertEqual(
                report_closed,
                verdict["ok"],
                (report["threads"], verdict["reasons"]),
            )

    def test_the_superseded_failure_is_still_counted_as_history(self):
        report = build_report(_rows(SUPERSEDED), [], [])
        totals = report["totals"]
        self.assertEqual(totals["not_closed"], 1, totals)
        self.assertEqual(totals["not_closed_open"], 0, totals)
        self.assertEqual(totals["not_closed_superseded"], 1, totals)

    def test_the_verdict_counts_the_open_attempts_not_every_failure(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        counts = verdict["counts"]
        self.assertEqual(counts["not_closed"], 1)
        self.assertEqual(counts["not_closed_open"], 0)
        self.assertEqual(counts["not_closed_superseded"], 1)

    def test_an_open_problem_still_counts_its_failed_attempt(self):
        verdict = closure_verdict_for(_rows(STILL_OPEN), [], [])
        counts = verdict["counts"]
        self.assertEqual(counts["not_closed"], 1)
        self.assertEqual(counts["not_closed_open"], 1)
        self.assertEqual(counts["not_closed_superseded"], 0)

    def test_three_failures_then_a_success_are_one_closed_problem(self):
        spec = [
            ("R-x1", False, "T-x"),
            ("R-x2", False, "T-x"),
            ("R-x3", True, "T-x"),
        ]
        report = build_report(_rows(spec), [], [])
        verdict = closure_verdict(report)
        self.assertEqual(report["totals"]["not_closed"], 2)
        self.assertEqual(report["totals"]["not_closed_superseded"], 2)
        self.assertEqual(report["totals"]["not_closed_open"], 0)
        self.assertTrue(verdict["ok"], verdict)

    def test_the_two_rates_name_their_grain(self):
        report = build_report(_rows(SUPERSEDED), [], [])
        totals = report["totals"]
        # One attempt of two closed.
        self.assertEqual(totals["closure_rate"], 0.5)
        # One problem of one closed.
        self.assertEqual(totals["problem_closure_rate"], 1.0)

    def test_the_verdict_carries_the_problem_rate(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        self.assertEqual(verdict["closure_rate"], 0.5)
        self.assertEqual(verdict["problem_closure_rate"], 1.0)

    def test_the_reading_explains_the_superseded_failure(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        text = format_closure(verdict)
        self.assertIn("已闭合", text)
        # The reader must not be left holding a closed verdict next to
        # an unexplained failed attempt.
        self.assertIn("取代", text)
        self.assertIn("问题闭环率 100%", text)

    def test_an_open_problem_still_reads_as_open(self):
        verdict = closure_verdict_for(_rows(STILL_OPEN), [], [])
        text = format_closure(verdict)
        self.assertIn("未闭", text)

    def test_the_two_surfaces_give_the_same_answer(self):
        shared = closure_verdict_for(_rows(SUPERSEDED), [], [])

        import mas.mcp_server as mcp_module

        real_engine = mcp_module._engine

        class _Ledger:
            def entries(self):
                return _rows(SUPERSEDED)

        class _Tickets:
            def list(self):
                return []

        class _Engine:
            ledger = _Ledger()
            tickets = _Tickets()

        try:
            mcp_module._engine = lambda _c, team=None: _Engine()
            payload = mcp_module.handle_tool("mas_closure", {"as_markdown": False})
        finally:
            mcp_module._engine = real_engine

        self.assertEqual(payload["ok"], shared["ok"])
        self.assertEqual(payload["headline"], shared["headline"])
        self.assertEqual(
            payload["counts"]["not_closed_superseded"],
            shared["counts"]["not_closed_superseded"],
        )

    def test_a_report_without_threads_falls_back_to_counting_every_failure(self):
        """An older report must not be read as closed by accident."""
        verdict = closure_verdict(
            {
                "totals": {"runs": 1, "not_closed": 1, "problems_open": 0},
                "threads": [],
                "drafts": {},
            }
        )
        self.assertFalse(verdict["ok"], verdict)
        keys = {r["reason"] for r in verdict["reasons"]}
        self.assertIn("runs_not_closed", keys)

    def test_an_empty_ledger_is_still_not_a_closed_loop(self):
        verdict = closure_verdict_for([], [], [])
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["empty"])

    def test_the_verdict_never_runs_a_loop(self):
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mas",
            "report.py",
        )
        with open(report_path, encoding="utf-8") as handle:
            src = handle.read()
        body = src[src.find("def closure_verdict("):]
        for forbidden in ("engine.run(", "LoopEngine("):
            self.assertNotIn(forbidden, body)


class ClosureSupersedeGateTest(unittest.TestCase):
    """The gate must be registered and it must be able to fail."""

    def test_the_gate_is_registered(self):
        import mas.selftest as selftest_module

        names = [getattr(c, "CHECK_NAME", "") for c in selftest_module.CHECKS]
        self.assertIn("closure_supersede", names)

    def test_the_gate_passes_on_a_healthy_machine(self):
        from mas.selftest_closure_supersede import (
            check_the_report_and_the_verdict_agree_on_the_grain,
        )

        result = check_the_report_and_the_verdict_agree_on_the_grain(iterations=1)
        self.assertTrue(result["ok"], result["failures"])

    def test_the_gate_notices_a_broken_grain(self):
        """Break the verdict on purpose - the gate has to notice."""
        import mas.report as report_module
        from mas.selftest_closure_supersede import (
            _closure_observations,
            _supersede_verdicts,
        )

        real_verdict = report_module.closure_verdict

        def _old_grain(report, _real=real_verdict):
            verdict = _real(report)
            totals = report.get("totals") or {}
            verdict["ok"] = False
            verdict["reasons"] = [
                {
                    "reason": "runs_not_closed",
                    "count": int(totals.get("not_closed") or 0),
                    "detail": "有 %d 次运行没有闭合"
                    % int(totals.get("not_closed") or 0),
                    "level": "fail",
                }
            ]
            return verdict

        try:
            report_module.closure_verdict = _old_grain
            verdicts = _supersede_verdicts(_closure_observations())
        finally:
            report_module.closure_verdict = real_verdict

        self.assertFalse(
            verdicts["a failure that was later superseded does not hold the loop open"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

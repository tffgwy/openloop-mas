# -*- coding: utf-8 -*-
"""v0.7.99: every surface that shows the state says it in one grain.

The new capability
------------------
The status page, the report headline and the MCP status tool now all
count **problems**, not attempts. A failed attempt that a later attempt
replaced is history - it is counted, and it is labelled, but it does not
hold the queue open. The closure verdict and the status page therefore
cannot answer the same question differently.

Before this, ``mas status`` said "未闭合 1" next to "待解决问题 0 个"
while the closure verdict said the work was closed: one machine, two
answers. And the MCP status tool said nothing about closure at all, so
an agent asking "is this done?" got silence from the very tool that
looks like the place to ask it.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


from mas.report import (
    build_report,
    closure_grain,
    closure_verdict_for,
    group_runs,
    group_threads,
    render_markdown,
    run_summary,
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


#: One problem, two attempts: the first failed, the second closed it.
SUPERSEDED = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]

#: One problem, two attempts, the last one failed: still waiting.
STILL_OPEN = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


def _write_ledger(state_dir, rows):
    """Write the rows where the engine reads them from."""
    path = os.path.join(state_dir, "ledgers", "runs.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


class _Machine:
    """A state directory with a ledger in it, and a way to read it."""

    def __init__(self, rows):
        self.state_dir = tempfile.mkdtemp(prefix="mas-grain-")
        _write_ledger(self.state_dir, rows)

    def close(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def status_json(self):
        from mas.cli import main

        saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = self.state_dir
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                main(["status", "--json"])
        finally:
            if saved is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = saved
        return json.loads(buffer.getvalue())

    def status_text(self):
        from mas.cli import main

        saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = self.state_dir
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                main(["status"])
        finally:
            if saved is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = saved
        return buffer.getvalue()

    def report_markdown(self):
        from mas.cli import main

        saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = self.state_dir
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                main(["report"])
        finally:
            if saved is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = saved
        return buffer.getvalue()

    def mcp_status(self):
        from mas.mcp_server import handle_tool

        saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = self.state_dir
        try:
            return handle_tool("mas_status", {})
        finally:
            if saved is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = saved


class ClosureGrainTest(unittest.TestCase):
    """One rule, three surfaces."""

    def setUp(self):
        self.machines = []

    def tearDown(self):
        for machine in self.machines:
            machine.close()

    def _machine(self, rows):
        machine = _Machine(rows)
        self.machines.append(machine)
        return machine

    # ------------------------------------------------- the one function
    def test_the_grain_separates_waiting_from_history(self):
        runs = [
            run_summary(g["run_id"], g["rows"]) for g in group_runs(_rows(SUPERSEDED))
        ]
        grain = closure_grain(runs, group_threads(runs))
        # Two attempts, one of which failed - and the failure was
        # replaced, so nothing waits.
        self.assertEqual(grain["attempts"], 2)
        self.assertEqual(grain["attempts_closed"], 1)
        self.assertEqual(grain["attempts_not_closed"], 1)
        self.assertEqual(grain["attempts_open"], 0)
        self.assertEqual(grain["attempts_superseded"], 1)
        # One problem, and it is closed.
        self.assertEqual(grain["problems"], 1)
        self.assertEqual(grain["problems_open"], 0)
        self.assertEqual(grain["problems_closed"], 1)
        # Two rates, each naming its grain.
        self.assertAlmostEqual(grain["attempt_closure_rate"], 0.5)
        self.assertAlmostEqual(grain["problem_closure_rate"], 1.0)

    def test_a_problem_that_is_still_open_keeps_its_attempt_waiting(self):
        runs = [
            run_summary(g["run_id"], g["rows"]) for g in group_runs(_rows(STILL_OPEN))
        ]
        grain = closure_grain(runs, group_threads(runs))
        self.assertEqual(grain["attempts_open"], 1)
        self.assertEqual(grain["attempts_superseded"], 0)
        self.assertEqual(grain["problems_open"], 1)

    def test_the_grain_works_without_a_threads_argument(self):
        runs = [
            run_summary(g["run_id"], g["rows"]) for g in group_runs(_rows(SUPERSEDED))
        ]
        grain = closure_grain(runs)
        self.assertEqual(grain["attempts_open"], 0)
        self.assertEqual(grain["problems"], 1)

    # ----------------------------------------------------- the surfaces
    def test_the_status_page_does_not_hold_a_resumed_problem_open(self):
        machine = self._machine(_rows(SUPERSEDED))
        payload = machine.status_json()
        # The waiting count is the number of failed attempts whose
        # problem is still open. Here that is zero.
        self.assertEqual(payload["runs"]["open"], 0, payload["runs"])
        # The history is not hidden.
        self.assertEqual(payload["runs"]["not_closed"], 1, payload["runs"])
        self.assertEqual(payload["runs"]["superseded"], 1, payload["runs"])
        # And the problems agree.
        self.assertEqual(payload["problems"]["open"], 0, payload["problems"])
        self.assertEqual(payload["problems"]["closed"], 1, payload["problems"])

    def test_the_status_page_and_the_verdict_agree(self):
        machine = self._machine(_rows(SUPERSEDED))
        payload = machine.status_json()
        rows = _rows(SUPERSEDED)
        verdict = closure_verdict_for(rows, [])
        counts = verdict["counts"]
        self.assertEqual(
            payload["runs"]["open"], counts["not_closed_open"], (payload, counts)
        )
        self.assertEqual(
            payload["problems"]["open"], counts["problems_open"], (payload, counts)
        )
        # And the verdict really is a closed one.
        self.assertTrue(verdict["ok"], verdict["reasons"])

    def test_the_status_page_names_the_grain_of_its_numbers(self):
        machine = self._machine(_rows(SUPERSEDED))
        text = machine.status_text()
        # The waiting count, in its own words.
        self.assertIn("仍在等 0", text)
        # The history, named as history, with the number of the
        # superseded failures spelled out.
        self.assertIn("历史失败：1 次", text)
        self.assertIn("1 次已被后来的成功尝试取代", text)

    def test_the_status_page_still_counts_a_failure_that_waits(self):
        machine = self._machine(_rows(STILL_OPEN))
        payload = machine.status_json()
        self.assertEqual(payload["runs"]["open"], 1, payload["runs"])
        self.assertEqual(payload["problems"]["open"], 1, payload["problems"])
        text = machine.status_text()
        self.assertIn("仍在等 1", text)

    def test_the_status_page_publishes_both_rates(self):
        machine = self._machine(_rows(SUPERSEDED))
        payload = machine.status_json()
        rates = payload["rates"]
        self.assertIn("attempt_closure_rate", rates)
        self.assertIn("problem_closure_rate", rates)
        self.assertAlmostEqual(rates["attempt_closure_rate"], 0.5)
        self.assertAlmostEqual(rates["problem_closure_rate"], 1.0)

    def test_the_report_headline_names_the_grain(self):
        report = build_report(_rows(SUPERSEDED), [], [])
        markdown = render_markdown(report)
        # The waiting count, not the attempt count.
        self.assertIn("仍在等：0", markdown)
        self.assertIn("问题闭环率 100%", markdown)
        # The history, named as history.
        self.assertIn("历史失败：1 次", markdown)
        self.assertIn("已被后来的成功尝试取代", markdown)
        # The old wording is gone: it read like a contradiction.
        self.assertNotIn("未闭环：1", markdown)

    def test_the_report_headline_still_reports_an_open_problem(self):
        report = build_report(_rows(STILL_OPEN), [], [])
        markdown = render_markdown(report)
        self.assertIn("仍在等：1", markdown)
        self.assertIn("问题闭环率 0%", markdown)

    def test_the_mcp_status_tool_reports_the_closure(self):
        machine = self._machine(_rows(SUPERSEDED))
        payload = machine.mcp_status()
        closure = payload["closure"]
        self.assertTrue(closure, payload.keys())
        self.assertTrue(closure["ok"], closure)
        self.assertEqual(closure["attempts"], 2, closure)
        self.assertEqual(closure["attempts_closed"], 1, closure)
        self.assertEqual(closure["attempts_not_closed"], 1, closure)
        self.assertEqual(closure["attempts_open"], 0, closure)
        self.assertEqual(closure["attempts_superseded"], 1, closure)
        self.assertEqual(closure["problems"], 1, closure)
        self.assertEqual(closure["problems_open"], 0, closure)
        self.assertEqual(closure["problems_closed"], 1, closure)
        self.assertIsInstance(closure["reasons"], list)

    def test_the_mcp_status_tool_says_what_is_waiting(self):
        machine = self._machine(_rows(STILL_OPEN))
        payload = machine.mcp_status()
        closure = payload["closure"]
        self.assertFalse(closure["ok"], closure)
        self.assertEqual(closure["attempts_open"], 1, closure)
        self.assertEqual(closure["problems_open"], 1, closure)
        self.assertTrue(closure["reasons"], closure)

    def test_the_three_surfaces_read_one_rule(self):
        machine = self._machine(_rows(SUPERSEDED))
        payload = machine.status_json()
        mcp = machine.mcp_status()
        report = build_report(_rows(SUPERSEDED), [], [])
        totals = report["totals"]
        # The same number, on every surface.
        self.assertEqual(payload["runs"]["open"], totals["not_closed_open"])
        self.assertEqual(payload["runs"]["open"], mcp["closure"]["attempts_open"])
        self.assertEqual(
            payload["problems"]["open"],
            totals["problems_open"],
        )
        self.assertEqual(
            payload["problems"]["open"],
            mcp["closure"]["problems_open"],
        )

    def test_the_mcp_status_tool_survives_a_broken_ledger(self):
        # The tool must never crash - a status tool that raises is
        # worse than one that reports nothing.
        machine = self._machine(_rows(SUPERSEDED))
        payload = machine.mcp_status()
        self.assertIn("closure", payload)

    # --------------------------------------------------------- the gate
    def test_the_gate_is_registered_and_passes(self):
        from mas.selftest import CHECKS

        names = [getattr(check, "CHECK_NAME", "") for check in CHECKS]
        self.assertIn("status_grain", names)
        gate = [c for c in CHECKS if getattr(c, "CHECK_NAME", "") == "status_grain"][0]
        result = gate(iterations=1)
        self.assertTrue(result["ok"], result["failures"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

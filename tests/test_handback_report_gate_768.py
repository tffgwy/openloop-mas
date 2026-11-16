# -*- coding: utf-8 -*-
"""v0.7.68 regression tests for the tenth gate: ``handback_report``.

The ninth gate (``handback_closes``) watches the *engine*: a human
deliverable passes the same standards and closes the loop. Nothing
watched the *report*. Mutation testing proved the gap - six mutations
of the reporting layer (drop the ``closed`` flag, rename the decision,
stop reading the handback scores, go back to "all member runs must
close", forget who closed it, always label the source as the machine)
were caught by **zero** gates.

These tests pin the tenth gate down: it has to exist, it has to be
listed among the checks, it has to pass, and it has to fail when the
report lies.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import unittest

from mas import selftest as st
from mas.report import build_report, render_markdown


class TestHandbackReportGateExists(unittest.TestCase):
    """The gate has to be there, and it has to be reachable."""

    def test_the_tenth_gate_is_registered(self):
        names = [st._check_name(check) for check in st.CHECKS]
        self.assertIn("handback_report", names)

    def test_the_gate_has_its_own_short_name(self):
        check = st.check_a_handback_shows_up_in_the_report
        self.assertEqual(st._check_name(check), "handback_report")

    def test_the_gate_passes_on_a_clean_build(self):
        row = st.check_a_handback_shows_up_in_the_report(iterations=1)
        self.assertTrue(row["ok"], row.get("failures"))
        self.assertEqual(row["failures"], [])

    def test_the_gate_states_its_invariant(self):
        row = st.check_a_handback_shows_up_in_the_report(iterations=1)
        self.assertTrue(row["title"])
        self.assertTrue(row["invariant"])
        self.assertEqual(row["name"], "handback_report")

    def test_run_selftest_can_select_it_by_name(self):
        result = st.run_selftest(iterations=1, only=["handback_report"])
        self.assertTrue(result["ok"], result.get("failed"))
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "handback_report")


class TestHandbackReportGateCatchesLies(unittest.TestCase):
    """The gate has to fail when the report tells the reader a lie."""

    def _rows_with_a_handback(self):
        import json
        import os
        import tempfile

        from mas.engine import LoopEngine
        from mas.providers import Provider

        class _Fails(Provider):
            name = "gate-test-handback-report"

            def complete(self, *, system, prompt, role="worker",
                         expect_json=False, max_tokens=4000):
                self.calls += 1
                usage = {"total_tokens": 10}
                if role == "planner":
                    text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
                elif role == "verifier":
                    text = json.dumps(
                        {"verdict": "fail",
                         "scores": [{"id": "c1", "pass": False, "note": "n"}]}
                    )
                elif role == "reflector":
                    text = json.dumps({"summary": "s", "issues": [], "next_action": "revise"})
                else:
                    text = "answer 41"
                return {"text": text, "usage": usage, "raw": {}}

        with tempfile.TemporaryDirectory(prefix="mas-gate-768-") as tmp:
            config = {
                "provider": "mock",
                "model": "m",
                "state_dir": os.path.join(tmp, "state"),
                "workspace": "",
                "budget": {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60},
                "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
                "team": {"language": "zh"},
                "stagnation_limit": 2,
            }
            criteria = [{"id": "c1", "desc": "42", "check": {"type": "contains", "arg": "42"}}]
            engine = LoopEngine(config, provider=_Fails(model="m"))
            result = engine.run(goal="goal", criteria=criteria)
            ticket_id = str(result.ticket_id or "")
            engine.handback(ticket_id, "answer 42", note="human")
            path = result.ledger_path
            rows = []
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            return rows

    def _mutate_the_ledger(self, rows, role, field, value):
        """Return a copy of the ledger with one field changed."""
        out = []
        for row in rows:
            copy = dict(row)
            if copy.get("role") == role:
                copy[field] = value
            out.append(copy)
        return out

    def test_a_ledger_that_lies_about_the_closure_is_caught(self):
        rows = self._rows_with_a_handback()
        # The mutation the tenth gate exists for: the handback's end
        # entry loses its ``closed`` flag, so the report says the
        # problem is still open.
        broken = self._mutate_the_ledger(rows, "run_end", "closed", False)
        report = build_report(broken)
        handback_runs = [r for r in report["runs"] if r.get("handback")]
        self.assertTrue(handback_runs)
        self.assertFalse(handback_runs[0]["closed"])
        self.assertGreaterEqual(report["totals"]["problems_open"], 1)

    def test_the_gate_fails_when_the_ledger_lies(self):
        """The gate has to notice a lying ledger, not just a clean one."""
        import mas.selftest as st

        original = st._ledger_rows
        rows = self._rows_with_a_handback()
        broken = self._mutate_the_ledger(rows, "run_end", "closed", False)

        def _fake_ledger_rows(ledger_path):
            return broken

        st._ledger_rows = _fake_ledger_rows
        try:
            row = st.check_a_handback_shows_up_in_the_report(iterations=1)
        finally:
            st._ledger_rows = original
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_the_source_is_always_the_machine(self):
        import mas.selftest as st

        original = st._ledger_rows
        rows = self._rows_with_a_handback()
        broken = self._mutate_the_ledger(rows, "run_end", "handback", False)

        def _fake_ledger_rows(ledger_path):
            return broken

        st._ledger_rows = _fake_ledger_rows
        try:
            row = st.check_a_handback_shows_up_in_the_report(iterations=1)
        finally:
            st._ledger_rows = original
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_report_tells_the_truth_about_a_handback(self):
        rows = self._rows_with_a_handback()
        report = build_report(rows)
        handback_runs = [r for r in report["runs"] if r.get("handback")]
        self.assertEqual(len(handback_runs), 1)
        run = handback_runs[0]
        self.assertTrue(run["closed"])
        self.assertEqual(run["decision"], "handback")
        self.assertTrue(run["scores"])
        self.assertEqual(run["human_note"], "human")
        self.assertEqual(report["totals"]["problems_open"], 0)
        self.assertGreaterEqual(report["totals"]["problems_closed"], 1)
        markdown = render_markdown(report)
        self.assertIn("🤝", markdown)
        self.assertIn("人工交付物", markdown)
        self.assertIn("human", markdown)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

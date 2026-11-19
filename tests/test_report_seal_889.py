# -*- coding: utf-8 -*-
"""The report has to see the drift that the seal can prove.

v0.7.88 sealed every closure to the bytes it was decided on, and
``mas seal`` could re-derive the verdict. But the report - the screen
a human actually reads after a run - never looked at the seal, so a
conclusion whose files were edited underneath was still reported as
"resolved" and "machine-closed".

This file pins what the report has to say:

* a healthy closure is reported as still holding;
* a closure that was edited afterwards is reported as drifted, in the
  problem overview, in the run overview and in the detail of the run;
* the JSON payload carries the reconciliation;
* a problem closed on paper is not called "resolved".
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.providers import Provider  # noqa: E402
from mas.report import build_report, render_markdown  # noqa: E402


class _Writer(Provider):
    """A worker that writes a file, and a verifier that says yes."""

    name = "report-seal-test"

    def __init__(self, model: str = "test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
        elif role == "verifier":
            text = json.dumps(
                {
                    "verdict": "pass",
                    "scores": [
                        {"id": "c1", "pass": True, "note": "ok"},
                        {"id": "c2", "pass": True, "note": "file there"},
                    ],
                }
            )
        elif role == "worker":
            text = (
                "\u4ea4\u4ed8\u7269\u6b63\u6587\n"
                '<<<MAS:FILE path="notes/finding.txt">>>>\n'
                "\u7ed3\u8bba\uff1aREPORTTEST\n"
                "<<<MAS:END FILE>>>\n"
            )
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


CRITERIA = [
    {"id": "c1", "desc": "\u542b\u6b63\u6587", "check": {"type": "contains", "arg": "\u6b63\u6587"}},
    {
        "id": "c2",
        "desc": "\u6587\u4ef6\u5199\u51fa\u6765\u4e86",
        "check": {"type": "file_exists", "arg": "notes/finding.txt"},
    },
]


def _config(tmp):
    return {
        "provider": "mock",
        "model": "test-model",
        "state_dir": os.path.join(tmp, "state"),
        "workspace": "",
        "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }


def _problems_table(markdown):
    return markdown.split("## \u95ee\u9898\u6982\u89c8", 1)[1].split("## \u8fd0\u884c\u6982\u89c8", 1)[0]


def _runs_table(markdown):
    return markdown.split("## \u8fd0\u884c\u6982\u89c8", 1)[1].split("## \u8fd0\u884c `", 1)[0]


class ReportSealTest(unittest.TestCase):
    def _closed_run(self, tmp):
        engine = LoopEngine(_config(tmp), provider=_Writer(model="test-model"))
        result = engine.run(goal="\u5199\u4e00\u4efd\u7ed3\u8bba", criteria=CRITERIA)
        self.assertTrue(result.closed, result.error)
        return engine, result

    def test_a_healthy_closure_is_reported_as_holding(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, _result = self._closed_run(tmp)
            markdown = render_markdown(build_report(engine.ledger.entries()))
            self.assertIn("\u5c01\u5b58\uff1a\u2713 \u7ed3\u8bba\u4ecd\u7136\u6210\u7acb", markdown)

    def test_an_edited_file_is_reported_as_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, result = self._closed_run(tmp)
            rows = engine.ledger.entries()
            target = Path(str(result.workspace)) / "notes" / "finding.txt"
            self.assertTrue(target.exists())
            # Edited to the same length on purpose: a length-based
            # record cannot see that, a content hash can.
            target.write_bytes("\u7ed3\u8bba\uff1aEDITED\n".encode("utf-8"))
            try:
                report = build_report(rows)
                markdown = render_markdown(report)
            finally:
                target.write_bytes(
                    "\u7ed3\u8bba\uff1aREPORTTEST\n".encode("utf-8")
                )
            self.assertIn("\u7ed3\u8bba\u5df2\u6f02\u79fb", markdown)
            self.assertIn("\u6587\u4ef6\u88ab\u6539\u8fc7", markdown)
            self.assertIn("notes/finding.txt", markdown)

    def test_the_problem_overview_does_not_call_a_drifted_problem_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, result = self._closed_run(tmp)
            rows = engine.ledger.entries()
            target = Path(str(result.workspace)) / "notes" / "finding.txt"
            original = target.read_bytes()
            target.write_bytes("\u7ed3\u8bba\uff1aEDITED\n".encode("utf-8"))
            try:
                markdown = render_markdown(build_report(rows))
            finally:
                target.write_bytes(original)
            table = _problems_table(markdown)
            self.assertIn("\u7ed3\u8bba\u5df2\u6f02\u79fb", table)
            self.assertNotIn("\u5df2\u89e3\u51b3", table)

    def test_the_run_overview_does_not_call_a_drifted_closure_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, result = self._closed_run(tmp)
            rows = engine.ledger.entries()
            target = Path(str(result.workspace)) / "notes" / "finding.txt"
            original = target.read_bytes()
            target.write_bytes("\u7ed3\u8bba\uff1aEDITED\n".encode("utf-8"))
            try:
                markdown = render_markdown(build_report(rows))
            finally:
                target.write_bytes(original)
            table = _runs_table(markdown)
            row = [line for line in table.splitlines() if line.startswith("| `R-")]
            self.assertTrue(row)
            self.assertNotIn("OK", row[0].split("|")[3])

    def test_the_json_payload_carries_the_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, result = self._closed_run(tmp)
            rows = engine.ledger.entries()
            healthy = build_report(rows)
            self.assertGreaterEqual(healthy["totals"]["sealed"], 1)
            self.assertEqual(healthy["totals"]["seal_drifted"], 0)
            self.assertTrue(healthy["seal"]["ok"])

            target = Path(str(result.workspace)) / "notes" / "finding.txt"
            original = target.read_bytes()
            target.write_bytes("\u7ed3\u8bba\uff1aEDITED\n".encode("utf-8"))
            try:
                drifted = build_report(rows)
            finally:
                target.write_bytes(original)
            self.assertGreaterEqual(drifted["totals"]["seal_drifted"], 1)
            self.assertFalse(drifted["seal"]["ok"])
            self.assertEqual(drifted["totals"]["seal_holds"], drifted["totals"]["sealed"] - 1)

    def test_a_run_without_a_seal_is_not_reported_as_drifted(self):
        """A run from before the seal existed is not a drifted run."""
        with tempfile.TemporaryDirectory() as tmp:
            engine, _result = self._closed_run(tmp)
            rows = [
                {k: v for k, v in row.items() if k != "seal"}
                for row in engine.ledger.entries()
            ]
            report = build_report(rows)
            self.assertEqual(report["totals"]["sealed"], 0)
            self.assertEqual(report["totals"]["seal_drifted"], 0)
            self.assertNotIn("\u7ed3\u8bba\u5df2\u6f02\u79fb", render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# -*- coding: utf-8 -*-
"""The thirty-sixth gate needs its own tests.

The gate watches the report's ledger table: every role the engine
writes has to carry a readable summary, and the summary has to name
the thing that happened - the file that was written, the draft that
is waiting, the answer that was cut off, the criteria that were
still missing.

These tests check the *checker*, because a checker that cannot tell a
row that said something from a row that said nothing is worse than no
checker at all.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mas import selftest_report_trace_roles as trace  # noqa: E402
from mas.report import _human_detail, _short_detail, ledger_markdown  # noqa: E402
from mas.verdict_text import role_reading  # noqa: E402


def _row(role, payload):
    row = dict(payload)
    row.update(
        {
            "role": role,
            "run_id": "R-test",
            "task_id": "T-test",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
        }
    )
    return row


class TraceRoleReadingTest(unittest.TestCase):
    def test_the_write_row_names_the_file_and_the_size(self):
        row = _row(
            "write",
            {"written": [{"path": "notes/out.txt", "bytes": 42, "created": True}]},
        )
        detail = _short_detail(row)
        self.assertIn("notes/out.txt", detail)
        self.assertIn("42", detail)

    def test_the_write_row_says_when_nothing_was_written(self):
        self.assertTrue(_short_detail(_row("write", {"written": []})).strip())

    def test_the_write_row_reports_a_write_error(self):
        row = _row("write", {"written": [], "errors": ["disk full"]})
        self.assertIn("disk full", _short_detail(row))

    def test_the_draft_row_names_the_draft_and_the_file(self):
        row = _row("draft", {"drafts": [{"id": "D-1", "path": "report.md"}]})
        detail = _short_detail(row)
        self.assertIn("D-1", detail)
        self.assertIn("report.md", detail)

    def test_the_truncated_row_says_it_will_retry(self):
        row = _row("truncated", {"error": "cut off", "retry": True, "tokens": 12})
        detail = _short_detail(row)
        self.assertIn("cut off", detail)
        self.assertIn("重试", detail)

    def test_the_reask_row_names_the_missing_criteria(self):
        row = _row("reask", {"missing": ["c1", "c2"]})
        detail = _short_detail(row)
        self.assertIn("c1", detail)
        self.assertIn("c2", detail)

    def test_the_lead_row_reads_the_advisors_summary(self):
        row = _row("lead", {"lead": {"summary": "先做 A"}})
        self.assertIn("先做 A", _short_detail(row))

    def test_the_human_reading_is_not_empty_either(self):
        for role, payload in trace.TRACE_ROLE_PAYLOADS:
            self.assertTrue(
                _human_detail(_row(role, payload)).strip(),
                "%s has no human reading" % role,
            )

    def test_the_draft_role_has_a_chinese_reading(self):
        self.assertEqual(role_reading("draft"), "提议改动")

    def test_the_exported_table_has_no_blank_summary(self):
        rows = [_row(role, payload) for role, payload in trace.TRACE_ROLE_PAYLOADS]
        table = ledger_markdown(rows)
        body = [ln for ln in table.splitlines() if ln.startswith("|")]
        body = [ln for ln in body if not ln.startswith("|---")][1:]
        self.assertEqual(len(body), len(trace.TRACE_ROLE_PAYLOADS))
        for line in body:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            self.assertTrue(cells[7], "blank summary in %s" % cells[4])


class TraceGateTest(unittest.TestCase):
    def test_the_gate_passes_on_a_healthy_report(self):
        result = trace.check_the_report_reads_every_ledger_row(iterations=2)
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual(result["name"], "report_trace_roles")

    def test_every_observation_is_made(self):
        verdicts = trace._trace_verdicts(trace._trace_observations())
        for label in trace.TRACE_ROLE_OBSERVATIONS:
            self.assertIn(label, verdicts)
            self.assertTrue(verdicts[label], label)

    def test_the_two_readings_agree(self):
        facts = trace._trace_observations()
        first = trace._trace_verdicts(facts)
        second = trace._trace_verdicts_independent(facts)
        for label in trace.TRACE_ROLE_OBSERVATIONS:
            self.assertEqual(first[label], second[label], label)

    def test_the_gate_notices_a_trace_that_says_nothing(self):
        """The gate has to be able to fail."""
        import mas.report as report_module

        real = report_module._short_detail

        def _say_nothing(row, _real=real):
            _real(row)
            return ""

        report_module._short_detail = _say_nothing
        try:
            broken = trace._trace_verdicts(trace._trace_observations())
        finally:
            report_module._short_detail = real
        self.assertFalse(broken["every engine role has a non-empty reading"])
        self.assertFalse(broken["the exported table has no blank summary"])

    def test_the_gate_notices_a_missing_write_reading(self):
        """A row that lost its file name must be caught too."""
        import mas.report as report_module

        real = report_module._short_detail

        def _write_without_the_file(row, _real=real):
            detail = _real(row)
            if str(row.get("role")) == "write":
                return "写了东西"
            return detail

        report_module._short_detail = _write_without_the_file
        try:
            broken = trace._trace_verdicts(trace._trace_observations())
        finally:
            report_module._short_detail = real
        self.assertFalse(broken["the write row names the file and the byte count"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

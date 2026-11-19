# -*- coding: utf-8 -*-
"""The report has to read the human ticket queue (v0.7.93).

The report used to read the ledger and nothing else. A ticket is the
machine's way of saying "this needs a person" - so a report that does
not read the queue reports a machine that looks finished while problems
sit waiting for a human.

These tests pin the behaviour:

* a waiting ticket is counted in the headline and named in a section
  of its own;
* the reason is read as a sentence, and the continuation command is
  printed;
* the JSON payload carries the numbers;
* the queue and the ledger are cross-checked in both directions;
* a report built without the queue says nothing about tickets - the
  behaviour before this version, kept as the regression baseline.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import unittest

from mas.report import build_report, render_markdown


def _waiting_ticket(root: pathlib.Path, ticket_id: str = "T-unit") -> dict:
    return {
        "id": ticket_id,
        "kind": "loop_escalation",
        "state": "waiting_human",
        "created_at": "2026-01-01T00:00:00+00:00",
        "resolved_at": None,
        "decision": None,
        "note": "",
        "payload": {
            "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
            "title": "\u5199\u4e00\u4efd\u62a5\u544a",
            "reason": "budget: reached max_iterations=3",
            "attempts": 3,
            "iterations_total": 3,
            "runs": ["R-unit"],
            "run_id": "R-unit",
            "criteria": [],
            "workspace": str(root),
        },
    }


class ReportTicketsTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-tickets-"))
        self.ticket = _waiting_ticket(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _rows(self, closed: bool = False):
        return [
            {
                "role": "run_end",
                "run_id": "R-unit",
                "ticket_id": "T-unit",
                "closed": closed,
                "status": "closed" if closed else "escalated",
                "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
                "error": "" if closed else "budget: reached max_iterations=3",
                "ts": "2026-01-01T00:00:00+00:00",
            },
        ]

    def test_a_waiting_ticket_is_counted_in_the_headline(self):
        report = build_report(self._rows(), [self.ticket])
        totals = report["totals"]
        self.assertEqual(1, totals["tickets"])
        self.assertEqual(1, totals["tickets_waiting"])
        self.assertEqual(0, totals["tickets_resolved"])
        markdown = render_markdown(report)
        self.assertIn("\u4eba\u5de5\u5de5\u5355", markdown)

    def test_the_report_has_a_section_naming_the_ticket(self):
        report = build_report(self._rows(), [self.ticket])
        markdown = render_markdown(report)
        self.assertIn("## \u4eba\u5de5\u5de5\u5355", markdown)
        self.assertIn("T-unit", markdown)
        self.assertIn("\u7b49\u4eba\u63a5", markdown)

    def test_the_reason_is_read_in_words_and_the_command_is_printed(self):
        report = build_report(self._rows(), [self.ticket])
        markdown = render_markdown(report)
        # The engine's own vocabulary is not what a human acts on.
        self.assertIn("\u9884\u7b97", markdown)
        self.assertIn("mas resume T-unit", markdown)

    def test_the_json_payload_carries_the_reconciliation(self):
        report = build_report(self._rows(), [self.ticket])
        queue = report["tickets"]
        self.assertEqual(1, queue["ticket_count"])
        self.assertEqual(1, queue["waiting_count"])
        self.assertEqual(["T-unit"], queue["waiting"])
        self.assertTrue(queue["ok"])
        self.assertEqual(0, queue["problem_count"])
        entry = queue["tickets"][0]
        self.assertEqual("waiting_human", entry["state"])
        self.assertEqual("\u5199\u4e00\u4efd\u62a5\u544a", entry["title"])
        # The command line spells the flag with a dash.
        self.assertIn("--max-iterations", entry["continuation"])

    def test_a_ticket_the_ledger_points_at_but_the_queue_lost_is_named(self):
        report = build_report(self._rows(), [])
        problems = report["tickets"]["problems"]
        self.assertTrue(any("T-unit" in problem for problem in problems))
        self.assertFalse(report["tickets"]["ok"])
        markdown = render_markdown(report)
        self.assertIn("T-unit", markdown)

    def test_a_ticket_that_waits_although_its_run_closed_is_named(self):
        report = build_report(self._rows(closed=True), [self.ticket])
        problems = report["tickets"]["problems"]
        self.assertTrue(any("T-unit" in problem for problem in problems))

    def test_a_ticket_the_ledger_never_mentioned_is_named(self):
        # The ledger holds no run at all, so the ticket is the only
        # sign that the problem exists - and the report has to say
        # that the queue is asking about something the ledger does not
        # hold.
        report = build_report([], [self.ticket])
        problems = report["tickets"]["problems"]
        self.assertTrue(problems)
        self.assertTrue(any("T-unit" in problem for problem in problems))

    def test_a_ledger_that_never_held_the_run_is_not_accused(self):
        # The report may be asked about one run out of many. A ticket
        # of another problem is not a hole then - it is simply not
        # part of this report.
        report = build_report(self._rows(), [self.ticket], ledger_complete=False)
        self.assertEqual(0, report["tickets"]["problem_count"])

    def test_without_the_queue_the_report_cannot_name_a_ticket(self):
        # The behaviour before this version, kept as the baseline: a
        # report built without the queue knows no ticket, so it can
        # only say that the ledger points at one that is not there.
        report = build_report(self._rows(), [])
        self.assertEqual(0, report["totals"]["tickets"])
        self.assertEqual(0, report["totals"]["tickets_waiting"])
        markdown = render_markdown(report)
        self.assertNotIn("\u7b49\u4eba\u63a5", markdown)
        self.assertNotIn("mas resume T-unit", markdown)

    def test_the_payload_is_json_serialisable(self):
        report = build_report(self._rows(), [self.ticket])
        json.dumps(report, ensure_ascii=False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

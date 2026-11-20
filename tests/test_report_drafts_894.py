# -*- coding: utf-8 -*-
"""The report has to read the draft queue (v0.7.94).

A run that wants to *change* a file that already exists does not
change it - the engine stages the change as a draft and waits for a
human. The report never read that queue, so a waiting change was
invisible, and a run that closed on its own proposal reported
"closed" while the file on the disk was still the old one.

These tests pin the behaviour:

* a waiting draft is counted in the headline and named in a section
  of its own, with the file and the approval command;
* the JSON payload carries the reconciliation;
* the queue and the ledger are cross-checked in both directions;
* a closed run whose change is still pending is named - the sharpest
  fact of the lot;
* a report built without the drafts says nothing about them, which is
  the behaviour before this version, kept as the regression baseline.
"""

from __future__ import annotations

import unittest

from mas.drafts_reading import draft_reading, draft_state_reading
from mas.report import build_report, render_markdown


def _draft(draft_id: str = "D-unit", state: str = "pending", path: str = "report.md") -> dict:
    return {
        "id": draft_id,
        "workspace": "/tmp/workspace",
        "path": path,
        "absolute": "/tmp/workspace/%s" % path,
        "state": state,
        "reason": "\u62df\u6539\u5199\u62a5\u544a",
        "created_at": "2026-01-01T00:00:00+00:00",
        "resolved_at": None,
        "before_bytes": 3,
        "after_bytes": 6,
        "changed": True,
    }


def _draft_rows(draft_id: str = "D-unit", run_id: str = "R-unit") -> list:
    return [
        {
            "role": "draft",
            "run_id": run_id,
            "task_id": "T-unit",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
            "drafts": [
                {
                    "id": draft_id,
                    "path": "report.md",
                    "absolute": "/tmp/workspace/report.md",
                    "bytes": 6,
                    "changed": True,
                    "written": False,
                    "draft": True,
                },
            ],
        },
    ]


def _run_end_rows(run_id: str = "R-unit", closed: bool = True) -> list:
    return [
        {
            "role": "run_end",
            "run_id": run_id,
            "closed": closed,
            "status": "closed" if closed else "escalated",
            "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
            "ts": "2026-01-01T00:01:00+00:00",
        },
    ]


class DraftsReadingTest(unittest.TestCase):
    def test_the_state_is_read_in_words(self):
        self.assertEqual("\u7b49\u4eba\u6279", draft_state_reading("pending"))
        self.assertEqual("\u5df2\u6279\u51c6", draft_state_reading("approved"))
        self.assertEqual("", draft_state_reading(""))

    def test_a_closed_run_with_a_pending_draft_is_found(self):
        reading = draft_reading(
            _draft_rows() + _run_end_rows(), [_draft()]
        )
        self.assertEqual(1, reading["pending_count"])
        self.assertEqual(1, reading["closed_with_pending_count"])
        self.assertEqual("R-unit", reading["closed_with_pending"][0]["run_id"])
        self.assertFalse(reading["ok"])
        self.assertTrue(
            any("R-unit" in problem for problem in reading["problems"])
        )

    def test_an_approved_draft_is_not_a_problem(self):
        reading = draft_reading(
            _draft_rows() + _run_end_rows(), [_draft(state="approved")]
        )
        self.assertEqual(0, reading["pending_count"])
        self.assertEqual(1, reading["approved_count"])
        self.assertEqual(0, reading["closed_with_pending_count"])
        self.assertTrue(reading["ok"])

    def test_a_draft_the_queue_lost_is_named(self):
        reading = draft_reading(_draft_rows() + _run_end_rows(), [])
        self.assertTrue(
            any("D-unit" in problem for problem in reading["problems"])
        )
        self.assertFalse(reading["ok"])

    def test_a_draft_the_ledger_never_mentioned_is_named(self):
        reading = draft_reading(
            _run_end_rows(), [_draft(draft_id="D-stranger")]
        )
        self.assertTrue(
            any("D-stranger" in problem for problem in reading["problems"])
        )
        self.assertFalse(reading["ok"])

    def test_an_open_run_with_a_pending_draft_is_not_a_contradiction(self):
        reading = draft_reading(
            _draft_rows() + _run_end_rows(closed=False), [_draft()]
        )
        self.assertEqual(0, reading["closed_with_pending_count"])
        self.assertTrue(reading["ok"])


class ReportDraftsTest(unittest.TestCase):
    def test_a_waiting_draft_is_counted_in_the_headline(self):
        report = build_report(
            _draft_rows() + _run_end_rows(), [], drafts=[_draft()]
        )
        totals = report["totals"]
        self.assertEqual(1, totals["drafts"])
        self.assertEqual(1, totals["drafts_pending"])
        self.assertEqual(0, totals["drafts_approved"])
        markdown = render_markdown(report)
        self.assertIn("\u5f85\u6279\u8349\u7a3f", markdown)

    def test_the_report_has_a_section_naming_the_draft(self):
        report = build_report(
            _draft_rows() + _run_end_rows(), [], drafts=[_draft()]
        )
        markdown = render_markdown(report)
        self.assertIn("## \u5f85\u6279\u8349\u7a3f", markdown)
        self.assertIn("D-unit", markdown)
        self.assertIn("\u7b49\u4eba\u6279", markdown)

    def test_the_section_names_the_file_and_the_approval_command(self):
        report = build_report(
            _draft_rows() + _run_end_rows(), [], drafts=[_draft()]
        )
        markdown = render_markdown(report)
        self.assertIn("report.md", markdown)
        self.assertIn("mas drafts approve D-unit", markdown)

    def test_the_json_payload_carries_the_reconciliation(self):
        report = build_report(
            _draft_rows() + _run_end_rows(), [], drafts=[_draft()]
        )
        queue = report["drafts"]
        self.assertEqual(1, queue["draft_count"])
        self.assertEqual(1, queue["pending_count"])
        self.assertEqual(["D-unit"], queue["pending"])
        self.assertEqual(1, len(queue["drafts"]))
        self.assertTrue(queue["ok"] is False)
        entry = queue["drafts"][0]
        self.assertEqual("R-unit", entry["run_id"])
        self.assertTrue(entry["in_ledger"])

    def test_a_closed_run_whose_change_is_pending_is_named(self):
        report = build_report(
            _draft_rows() + _run_end_rows(), [], drafts=[_draft()]
        )
        self.assertEqual(1, report["totals"]["runs_closed_with_pending_drafts"])
        markdown = render_markdown(report)
        self.assertIn("R-unit", markdown)
        self.assertIn("\u8fd8\u6392\u5728\u4eba\u524d\u9762", markdown)

    def test_a_report_without_the_queue_names_the_draft_the_ledger_lost(self):
        # The queue was not handed in, but the ledger still says the
        # run proposed a draft - so the draft cannot be approved and
        # the report has to say so instead of quietly forgetting it.
        report = build_report(_draft_rows() + _run_end_rows(), [])
        self.assertEqual(0, report["totals"]["drafts"])
        self.assertEqual(0, report["totals"]["drafts_pending"])
        problems = report["drafts"]["problems"]
        self.assertTrue(any("D-unit" in problem for problem in problems))
        markdown = render_markdown(report)
        self.assertIn("D-unit", markdown)
        # And it does not pretend a change is waiting for approval.
        self.assertNotIn("mas drafts approve D-unit", markdown)


class DraftWorkspaceReadingTest(unittest.TestCase):
    """The queue has to be found where the run actually worked.

    A run that was given a workspace of its own keeps its drafts in
    that folder - and the folder is not part of the saved
    configuration, so a reader that looked only at the configuration
    found an empty queue while a change was waiting on the disk.
    """

    def test_the_workspace_is_read_back_out_of_the_ledger(self):
        from mas.cli import _workspaces_of_drafts

        rows = [
            {
                "role": "draft",
                "run_id": "R-unit",
                "drafts": [
                    {
                        "id": "D-unit",
                        "path": "notes/report.md",
                        "absolute": "/tmp/ws/notes/report.md",
                        "bytes": 6,
                        "changed": True,
                    },
                ],
            },
        ]
        found = _workspaces_of_drafts(rows)
        self.assertEqual(1, len(found))
        self.assertEqual("/tmp/ws", found[0].as_posix())

    def test_a_row_without_an_absolute_path_is_skipped(self):
        from mas.cli import _workspaces_of_drafts

        rows = [
            {
                "role": "draft",
                "run_id": "R-unit",
                "drafts": [{"id": "D-unit", "path": "report.md", "absolute": ""}],
            },
        ]
        self.assertEqual([], _workspaces_of_drafts(rows))

    def test_a_ledger_without_draft_rows_names_no_workspace(self):
        from mas.cli import _workspaces_of_drafts

        rows = [{"role": "run_end", "run_id": "R-unit", "closed": True}]
        self.assertEqual([], _workspaces_of_drafts(rows))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

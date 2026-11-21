# -*- coding: utf-8 -*-
"""v0.8.4: the single-run report reads the queue.

The hole
--------
Every surface of the machine had learned to say the same grain - a
failed attempt that a later attempt replaced is *history*, not work in
progress. But the most common way to look at a report was still
broken:

    mas report --run-id R1

That ledger holds one run - the failed one. The attempt that actually
closed the problem, R2, happened later, in another run, and is not in
this ledger. So the same page said three different things at once:

    尝试总数：1　已闭环：0　仍在等：1　尝试闭环率：0%
    | 把事情做完 | 待处理 |          <- the problem is waiting
    | T1 | 已解决 |                 <- the queue says it is done
    - 人工工单：`T1`（已不需要续跑）  <- and the detail says so too

The verdict asked the **ledger** whether anyone was still holding the
problem. The ledger - for one run - holds nothing but the failure.

The fix
-------
The **queue** is the authority, and it already carries the evidence:
when an attempt closes a problem the engine records which attempt
closed it on the ticket (``closed_run_id``). A resolved ticket that
names that run is proof the problem was finished, so the failed attempt
it covers is history - and the report names the attempt that replaced
it.

Two things must not follow from that:

* a ticket a person closed **by hand** (``mas_ticket_resolve``) carries
  no ``closed_run_id`` - nobody recorded a successful attempt - so the
  failure stays open. The human handled the ticket; the machine still
  has nothing to show for it. Reporting that as finished would be the
  tool lying in the direction that hides work.
* a caller that hands in **no queue at all** has not looked, so it
  cannot retire a ticket, and the failure keeps its resume pointer.
"""

import os
import sys
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

from mas.report import build_report, group_threads, queue_closed_by_success, render_markdown
from mas.selftest_single_run_supersede import (
    _detail_block,
    _overview_row,
    _problem_row,
    _supersede_verdicts,
    _supersede_verdicts_independent,
    _surfaces,
    _ticket,
)


class QueueDecidesTest(unittest.TestCase):
    """The queue is the authority on "is anyone still holding it"."""

    def setUp(self):
        self.surfaces = _surfaces()

    # -- the queue says a later attempt closed it ------------------------

    def test_the_headline_does_not_count_the_replaced_failure_as_waiting(self):
        report = self.surfaces["closed_by_attempt"]
        totals = report["totals"]
        self.assertEqual(totals["not_closed_open"], 0)
        self.assertEqual(totals["not_closed_superseded"], 1)
        self.assertEqual(totals["problems_open"], 0)
        self.assertEqual(totals["problems_closed"], 1)

    def test_the_page_agrees_with_itself_in_four_places(self):
        md = self.surfaces["closed_md"]
        self.assertIn("仍在等：0", md)
        problem = _problem_row(md)
        self.assertNotIn("待处理", problem)
        self.assertIn("已解决", problem)
        row = _overview_row(md, "R-fail")
        self.assertIn("HIS", row)
        self.assertNotIn("ESC", row)

    def test_the_detail_names_the_attempt_that_replaced_it(self):
        detail = _detail_block(self.surfaces["closed_md"], "R-fail")
        self.assertIn("取代它的那次成功尝试", detail)
        self.assertIn("R-later", detail)

    def test_the_run_carries_the_superseding_attempt(self):
        runs = self.surfaces["closed_by_attempt"]["runs"]
        failed = [r for r in runs if not r.get("closed")]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["failure_grain"], "superseded")
        self.assertEqual(failed[0]["superseded_by"], "R-later")

    def test_the_credit_goes_to_the_attempt_that_closed_it(self):
        threads = self.surfaces["closed_by_attempt"]["threads"]
        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0]["closed"])
        self.assertEqual(threads[0]["closed_by"], "R-later")

    # -- a ticket a person closed by hand -------------------------------

    def test_a_hand_closed_ticket_keeps_the_failure_open(self):
        totals = self.surfaces["closed_by_hand"]["totals"]
        self.assertEqual(totals["not_closed_open"], 1)
        self.assertEqual(totals["not_closed_superseded"], 0)

    def test_a_hand_closed_ticket_still_shows_the_failure_reason(self):
        detail = _detail_block(self.surfaces["hand_md"], "R-fail")
        self.assertIn("未闭合原因", detail)
        self.assertNotIn("历史：这次失败已被", detail)

    def test_a_hand_closed_ticket_is_not_counted_as_superseded(self):
        runs = self.surfaces["closed_by_hand"]["runs"]
        failed = [r for r in runs if not r.get("closed")]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["failure_grain"], "open")
        self.assertEqual(failed[0]["superseded_by"], "")

    # -- the queue was never read ---------------------------------------

    def test_an_unread_queue_keeps_the_failure_open(self):
        totals = self.surfaces["unread"]["totals"]
        self.assertEqual(totals["not_closed_open"], 1)

    def test_an_unread_queue_keeps_the_resume_pointer(self):
        detail = _detail_block(self.surfaces["unread_md"], "R-fail")
        self.assertIn("mas resume", detail)

    def test_an_unread_queue_leaves_the_ticket_state_unknown(self):
        runs = self.surfaces["unread"]["runs"]
        for run in runs:
            self.assertFalse(run.get("ticket_known"))
            self.assertFalse(run.get("ticket_lost"))

    # -- the ticket is simply waiting ------------------------------------

    def test_a_waiting_ticket_is_an_ordinary_open_problem(self):
        totals = self.surfaces["waiting"]["totals"]
        self.assertEqual(totals["problems_open"], 1)
        self.assertEqual(totals["not_closed_open"], 1)

    # -- the helper itself -----------------------------------------------

    def test_the_helper_only_trusts_a_named_successful_attempt(self):
        closed = queue_closed_by_success(
            [_ticket("T-one", "resolved", closed_run_id="R-later", runs=["R-later"])]
        )
        self.assertEqual(closed, {"T-one": "R-later"})

    def test_the_helper_ignores_a_hand_closed_ticket(self):
        closed = queue_closed_by_success([_ticket("T-one", "resolved", runs=["R-later"])])
        self.assertEqual(closed, {})

    def test_the_helper_ignores_a_ticket_that_is_not_resolved(self):
        closed = queue_closed_by_success(
            [_ticket("T-one", "waiting_human", closed_run_id="R-later", runs=["R-later"])]
        )
        self.assertEqual(closed, {})

    def test_the_helper_ignores_an_unread_queue(self):
        self.assertEqual(queue_closed_by_success(None), {})
        self.assertEqual(queue_closed_by_success([]), {})

    def test_the_helper_ignores_a_ticket_with_no_successful_attempt(self):
        closed = queue_closed_by_success(
            [_ticket("T-one", "resolved", closed_run_id="", runs=["R-later"])]
        )
        self.assertEqual(closed, {})

    def test_the_threads_follow_the_queue(self):
        report = build_report(
            self.surfaces["rows"],
            [_ticket("T-one", "resolved", closed_run_id="R-later", runs=["R-later"])],
        )
        closed = queue_closed_by_success(
            [_ticket("T-one", "resolved", closed_run_id="R-later", runs=["R-later"])]
        )
        threads = group_threads(report["runs"], closed)
        self.assertTrue(threads[0]["closed"])
        self.assertEqual(threads[0]["closed_by"], "R-later")

    def test_the_threads_without_the_queue_keep_the_failure_open(self):
        report = build_report(self.surfaces["rows"], None)
        threads = group_threads(report["runs"])
        self.assertFalse(threads[0]["closed"])

    # -- the gate itself -------------------------------------------------

    def test_the_gate_reads_the_same_way_on_the_page_and_in_the_facts(self):
        facts = self.surfaces
        verdicts = _supersede_verdicts(facts)
        independent = _supersede_verdicts_independent(facts)
        self.assertEqual(set(verdicts), set(independent))
        for label, value in verdicts.items():
            self.assertTrue(value, label)
            self.assertEqual(value, independent[label], label)

    def test_the_gate_can_fail_when_the_queue_is_ignored(self):
        import mas.report as report_module

        real = report_module.group_threads

        def _ignore(runs, closed_by_ticket=None, _real=real):
            return _real(runs)

        report_module.group_threads = _ignore
        try:
            broken = _supersede_verdicts(_surfaces())
        finally:
            report_module.group_threads = real
        self.assertFalse(
            broken["a single-run report says the problem is finished when the queue says so"]
        )
        self.assertFalse(broken["the four lines of the page agree about that run"])

    def test_the_report_builds_without_a_queue_at_all(self):
        report = build_report(self.surfaces["rows"], None)
        self.assertEqual(report["totals"]["not_closed_open"], 1)
        md = render_markdown(report)
        self.assertIn("mas resume", _detail_block(md, "R-fail"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

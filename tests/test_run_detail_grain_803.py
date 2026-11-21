# -*- coding: utf-8 -*-
"""v0.8.3: the per-run detail says the same grain as the headline.

The hole
--------
v0.7.100 made the report headline say the grain out loud ("still
waiting 0 / history 1") and v0.8.2 made the verdict page say it. The
**per-run detail** was left behind, and it is the surface a reader
goes to when they want to know *what to do next*.

A report of a failed-then-succeeded problem said, at the top:

    尝试总数：2　已闭环：1　仍在等：0　尝试闭环率：50%
    历史失败：1 次（其中 1 次已被后来的成功尝试取代，不算待办）

and then, in the detail of the one failed run:

    - 未闭合原因：stagnation: the deliverable did not change for 3 attempts
    - 人工工单：`T1`（`mas tickets` 查看，`mas resume <工单号>` 从这里继续）

One machine, two answers - and the detail handed the reader a
**resume command** for a run that was already replaced.

The fix
-------
Every run carries the grain it belongs to (``superseded`` / ``open``),
decided by the same rule the headline uses. The resume pointer is
given only to a run whose ticket is **still waiting in the queue** - a
run keeps the id of the ticket it closed, so "has a ticket id" is not
"has work waiting".

The fix must not throw away the guidance a real pending problem
needs: a run whose ticket is still waiting keeps its reason, its
next-step hint and its resume command.
"""

import os
import sys
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


from mas.selftest_run_detail_grain import (
    _detail_block,
    _grain_text_verdicts,
    _grain_text_verdicts_independent,
    _overview_row,
    _surfaces,
)


class RunDetailGrainTest(unittest.TestCase):
    """The detail must not contradict the rest of the machine."""

    def setUp(self):
        self.surfaces = _surfaces()

    # -- the replaced attempt -----------------------------------------

    def test_the_replaced_attempt_is_filed_as_history(self):
        md = self.surfaces["superseded_md"]
        detail = _detail_block(md, "R-sup1")
        self.assertIn(
            "\u5386\u53f2\uff1a\u8fd9\u6b21\u5931\u8d25\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3",
            detail,
        )
        self.assertNotIn("\u672a\u95ed\u5408\u539f\u56e0", detail)

    def test_the_replaced_attempt_keeps_its_reason_but_as_history(self):
        md = self.surfaces["superseded_md"]
        detail = _detail_block(md, "R-sup1")
        self.assertIn("\u5f53\u65f6\u7684\u539f\u56e0\uff1a", detail)
        self.assertIn("\u505c\u6ede\uff1a", detail)

    def test_the_replaced_attempt_hands_out_no_resume_command(self):
        md = self.surfaces["superseded_md"]
        detail = _detail_block(md, "R-sup1")
        self.assertNotIn("mas resume", detail)

    def test_the_replaced_attempt_ticket_is_not_a_handle_to_pull(self):
        md = self.surfaces["superseded_md"]
        detail = _detail_block(md, "R-sup1")
        self.assertIn("\u5f53\u65f6\u7684\u4eba\u5de5\u5de5\u5355\uff1a`T-sup1`", detail)
        self.assertIn("\u4e0d\u9700\u8981\u7eed\u8dd1", detail)

    def test_the_overview_table_does_not_mark_it_as_open(self):
        md = self.surfaces["superseded_md"]
        row = _overview_row(md, "R-sup1")
        self.assertIn("HIS", row)
        self.assertNotIn("ESC", row)
        self.assertIn("\u5df2\u88ab\u53d6\u4ee3", row)

    def test_the_closed_run_does_not_hand_out_a_resume_command_either(self):
        md = self.surfaces["superseded_md"]
        detail = _detail_block(md, "R-sup2")
        self.assertNotIn("mas resume", detail)
        self.assertIn("\u5df2\u4e0d\u9700\u8981\u7eed\u8dd1", detail)

    # -- the run that is still waiting --------------------------------

    def test_a_waiting_run_keeps_its_reason(self):
        md = self.surfaces["open_md"]
        detail = _detail_block(md, "R-open2")
        self.assertIn("\u672a\u95ed\u5408\u539f\u56e0\uff1a", detail)

    def test_a_waiting_run_keeps_its_next_step(self):
        md = self.surfaces["open_md"]
        detail = _detail_block(md, "R-open2")
        self.assertIn("\u4e0b\u4e00\u6b65\uff1a", detail)

    def test_a_waiting_run_keeps_its_resume_command(self):
        md = self.surfaces["open_md"]
        detail = _detail_block(md, "R-open2")
        self.assertIn("mas resume", detail)
        self.assertIn("`T-open1`", detail)

    def test_a_waiting_run_is_still_marked_as_open(self):
        md = self.surfaces["open_md"]
        row = _overview_row(md, "R-open2")
        self.assertIn("ESC", row)

    # -- the two surfaces agree ---------------------------------------

    def test_the_detail_and_the_headline_agree_about_what_is_waiting(self):
        md = self.surfaces["superseded_md"]
        totals = self.surfaces["superseded_report"]["totals"]
        self.assertEqual(0, totals["not_closed_open"])
        self.assertIn("\u4ecd\u5728\u7b49\uff1a0", md)
        self.assertIn("\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21", md)

    def test_the_two_readings_of_the_page_agree(self):
        verdicts = _grain_text_verdicts(self.surfaces)
        independent = _grain_text_verdicts_independent(self.surfaces)
        for label, value in verdicts.items():
            self.assertEqual(
                value,
                independent.get(label),
                "the two readings disagree about %r" % label,
            )

    # -- the ticket the queue has lost --------------------------------

    def test_a_lost_ticket_is_not_called_finished(self):
        md = self.surfaces["lost_md"]
        detail = _detail_block(md, "R-lost")
        self.assertNotIn("\u5df2\u4e0d\u9700\u8981\u7eed\u8dd1", detail)
        self.assertNotIn("mas resume", detail)

    def test_a_lost_ticket_says_the_ticket_cannot_be_found(self):
        md = self.surfaces["lost_md"]
        detail = _detail_block(md, "R-lost")
        self.assertIn("\u5df2\u7ecf\u627e\u4e0d\u5230\u5b83\u4e86", detail)

    def test_every_observation_holds(self):
        for label, value in _grain_text_verdicts(self.surfaces).items():
            self.assertTrue(value, label)


if __name__ == "__main__":
    unittest.main()

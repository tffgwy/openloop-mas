# -*- coding: utf-8 -*-
"""v0.8.2: the verdict page speaks the same grain as everything else.

The new capability
------------------
v0.7.100 made the status page and the report say the grain out loud:
"still waiting" for the failed attempts whose problem is still open,
and "history" for the ones a later attempt replaced.

One surface was left behind. ``format_closure`` - the text behind
``mas closure`` and the MCP ``closure_text`` - still printed the old
undivided wording:

    [\u901a\u8fc7] \u95ed\u5408\uff1a\u6ca1\u6709\u7559\u4e0b\u4efb\u4f55\u5f85\u529e\u3002
           \u5c1d\u8bd5 2 \u6b21\uff08\u95ed\u5408 1 / \u672a\u95ed\u5408 1\uff09\uff0c\u5c1d\u8bd5\u95ed\u73af\u7387 50%

So one machine gave two answers to "is anything still waiting?": the
status page said "still waiting 0" and the verdict page said "one
attempt did not close".

Now the verdict page splits the attempts the same way every other
surface does. Nothing is hidden - the history is still counted, only
labelled.
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
    render_markdown,
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
                "goal": "\u628a\u4e8b\u60c5\u505a\u5b8c",
                "title": "\u628a\u4e8b\u60c5\u505a\u5b8c",
                "iterations": 1,
                "tokens": 10,
                "artifact_path": "",
                "files": [],
            }
        )
    return out


SUPERSEDED = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]
STILL_OPEN = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


class ClosureGrainTextTest(unittest.TestCase):
    """The verdict page must not contradict the rest of the machine."""

    def test_the_verdict_page_says_what_is_still_waiting(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        text = format_closure(verdict)
        # The resumed problem is closed, so nothing is waiting.
        self.assertIn("\u4ecd\u5728\u7b49 0", text)
        # And the old undivided wording is gone.
        self.assertNotIn("\u672a\u95ed\u5408 1", text)

    def test_the_verdict_page_names_the_history(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        text = format_closure(verdict)
        self.assertIn("\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21", text)
        self.assertIn("\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3", text)

    def test_an_open_problem_still_reads_as_waiting(self):
        verdict = closure_verdict_for(_rows(STILL_OPEN), [], [])
        text = format_closure(verdict)
        self.assertIn("\u4ecd\u5728\u7b49 1", text)
        self.assertIn("\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21", text)

    def test_the_waiting_number_is_the_one_the_verdict_is_built_on(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        text = format_closure(verdict)
        counts = verdict["counts"]
        self.assertIn("\u4ecd\u5728\u7b49 %d" % counts["not_closed_open"], text)

    def test_the_history_is_the_whole_history(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        text = format_closure(verdict)
        counts = verdict["counts"]
        self.assertIn("\u5386\u53f2\u5931\u8d25\uff1a%d \u6b21" % counts["not_closed"], text)

    def test_the_three_surfaces_say_the_same_thing(self):
        rows = _rows(SUPERSEDED)
        report = build_report(rows, [], [])
        verdict = closure_verdict(report)
        text = format_closure(verdict)
        markdown = render_markdown(report)
        counts = verdict["counts"]
        # The same waiting number on the verdict page and the report.
        self.assertIn("\u4ecd\u5728\u7b49 %d" % counts["not_closed_open"], text)
        self.assertIn("\u4ecd\u5728\u7b49\uff1a%d" % counts["not_closed_open"], markdown)
        # The same history.
        self.assertIn("\u5386\u53f2\u5931\u8d25", text)
        self.assertIn("\u5386\u53f2\u5931\u8d25\uff1a%d \u6b21" % counts["not_closed"], markdown)

    def test_a_closed_verdict_leaves_no_unexplained_failure(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        self.assertTrue(verdict["ok"])
        text = format_closure(verdict)
        # Any failure the page mentions is explained as history.
        if "\u5386\u53f2\u5931\u8d25" in text:
            self.assertIn("\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3", text)

    def test_the_two_rates_keep_their_own_grain(self):
        verdict = closure_verdict_for(_rows(SUPERSEDED), [], [])
        text = format_closure(verdict)
        self.assertIn("\u95ee\u9898\u95ed\u73af\u7387 100%", text)
        self.assertIn("\u5c1d\u8bd5\u95ed\u73af\u7387 50%", text)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""v0.7.68 regression tests: the report has to show the human step.

Two real bugs were found by probing the handback path in v0.7.67.

The first: a run that a *person* finished by hand was reported as an
open problem. The handback's end entry on the ledger carried no
``closed``, no goal and no files, and its per-criterion scores lived
on the handback row instead of a closure row - so the report, which
reads the closure row, saw an empty verdict on a closed run and
counted the problem as still open. The reader was told the opposite
of what happened.

The second: a problem was "closed" only when *every* attempt closed
it. That rule cannot ever hold for a problem that took more than one
try, so a problem that was resumed and then closed - or that a person
finished by hand - stayed in the queue forever. The queue said the
opposite of what happened, and a queue that never empties teaches the
reader to ignore it.

These tests pin both fixes down, and they pin down the third thing
the version is about: the report must *show* the human. A deliverable
that a person wrote is not a run of the loop, and the report used to
render it exactly like one.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import os
import tempfile
import unittest

from mas.engine import LoopEngine
from mas.providers import Provider
from mas.report import build_report, render_markdown, replay_trace


class _Fails(Provider):
    """A provider whose worker never satisfies the criterion."""

    name = "handback-probe-fails"

    def __init__(self, model="m", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        usage = {"total_tokens": 10}
        if role == "planner":
            return {
                "text": json.dumps({"summary": "s", "plan": ["a"], "risks": ["b"]}),
                "usage": usage,
                "raw": {},
            }
        if role == "verifier":
            return {
                "text": json.dumps(
                    {"verdict": "fail", "scores": [{"id": "c1", "pass": False, "note": "no"}]}
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "reflector":
            return {
                "text": json.dumps({"summary": "r", "issues": [], "next_action": "revise"}),
                "usage": usage,
                "raw": {},
            }
        return {"text": "hello, no answer here", "usage": usage, "raw": {}}


class _Passes(_Fails):
    """A provider whose worker satisfies the criterion."""

    name = "handback-probe-passes"

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        usage = {"total_tokens": 10}
        if role == "verifier":
            return {
                "text": json.dumps(
                    {"verdict": "pass", "scores": [{"id": "c1", "pass": True, "note": "ok"}]}
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "worker":
            # The machine check looks for "42" in the deliverable, so
            # the worker's text has to carry it - a passing verdict
            # alone does not close a run.
            return {"text": "the answer is 42", "usage": usage, "raw": {}}
        return super().complete(
            system=system,
            prompt=prompt,
            role=role,
            expect_json=expect_json,
            max_tokens=max_tokens,
        )


def _config(state_dir):
    return {
        "provider": "mock",
        "model": "m",
        "state_dir": state_dir,
        "workspace": "",
        "budget": {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
        "stagnation_limit": 2,
    }


CRITERIA = [{"id": "c1", "desc": "need the answer", "check": {"type": "contains", "arg": "42"}}]


class HandbackReportTest(unittest.TestCase):
    """A human deliverable must close the loop in the report too."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-handback-report-")
        self.engine = LoopEngine(_config(self.state_dir), provider=_Fails(model="m"))
        self.result = self.engine.run(goal="a hard question", criteria=CRITERIA)
        self.ticket_id = str(self.result.ticket_id)

    def _handback(self, text="the answer is 42", note="I wrote it by hand"):
        return self.engine.handback(self.ticket_id, text, note=note)

    def _overview(self, report):
        markdown = render_markdown(report)
        return markdown.split("## 运行概览", 1)[1].split("## 运行 `", 1)[0]

    def _problems(self, report):
        markdown = render_markdown(report)
        return markdown.split("## 问题概览", 1)[1].split("## 运行概览", 1)[0]

    # ------------------------------------------------------ bug 1: the report
    def test_a_handback_run_is_reported_as_closed(self):
        self._handback()
        report = build_report(self.engine.ledger.entries())
        runs = {run["run_id"]: run for run in report["runs"]}
        handbacks = [run for run in runs.values() if run.get("handback")]
        self.assertEqual(len(handbacks), 1, report["runs"])
        self.assertTrue(handbacks[0]["closed"])
        self.assertEqual(handbacks[0]["status"], "closed")
        self.assertTrue(handbacks[0]["deliverable_ok"])

    def test_a_handback_run_carries_the_task_it_answered(self):
        self._handback()
        report = build_report(self.engine.ledger.entries())
        handbacks = [run for run in report["runs"] if run.get("handback")]
        self.assertEqual(handbacks[0]["goal"], "a hard question")
        self.assertEqual(handbacks[0]["title"], "a hard question")

    def test_a_handback_run_carries_its_per_criterion_verdict(self):
        """The scores live on the handback row, not on a closure row.

        The report looked at the closure row only, so a human
        deliverable showed *no* judgement at all - a closed run with
        an empty verdict, and no way to tell whether anybody had
        actually checked the work.
        """
        self._handback()
        report = build_report(self.engine.ledger.entries())
        handbacks = [run for run in report["runs"] if run.get("handback")]
        scores = handbacks[0]["scores"]
        self.assertEqual(len(scores), 1)
        self.assertIs(scores[0]["pass"], True)

    def test_a_handback_run_records_the_human_note(self):
        self._handback(note="I wrote it by hand")
        report = build_report(self.engine.ledger.entries())
        handbacks = [run for run in report["runs"] if run.get("handback")]
        self.assertEqual(handbacks[0]["human_note"], "I wrote it by hand")

    def test_a_problem_finished_by_hand_is_not_left_in_the_queue(self):
        self._handback()
        report = build_report(self.engine.ledger.entries())
        totals = report["totals"]
        self.assertEqual(totals["problems_open"], 0)
        self.assertEqual(totals["problems_closed"], 1)

    def test_the_handback_decision_has_its_own_word(self):
        """Saying "close" would hide the difference between the
        machine doing it and a person doing it."""
        self._handback()
        report = build_report(self.engine.ledger.entries())
        handbacks = [run for run in report["runs"] if run.get("handback")]
        self.assertEqual(handbacks[0]["decision"], "handback")

    # -------------------------------------------------- bug 2: thread closure
    def test_a_thread_closed_by_a_handback_is_closed(self):
        self._handback()
        report = build_report(self.engine.ledger.entries())
        self.assertEqual(len(report["threads"]), 1)
        thread = report["threads"][0]
        self.assertTrue(thread["closed"])
        self.assertTrue(thread["handback"])
        self.assertTrue(thread["closed_by"].startswith("H-"))

    def test_a_thread_closed_by_a_resume_is_closed(self):
        """The same rule, without a human: a resumed run that finally
        closed the problem closes the thread."""
        second = LoopEngine(_config(self.state_dir), provider=_Passes(model="m"))
        second.ledger = self.engine.ledger
        second.tickets = self.engine.tickets
        result = second.resume(self.ticket_id, note="again")
        self.assertTrue(result.closed)
        report = build_report(self.engine.ledger.entries())
        self.assertEqual(len(report["threads"]), 1)
        thread = report["threads"][0]
        self.assertTrue(thread["closed"])
        self.assertFalse(thread["handback"])

    # ------------------------------------------------------- the human, shown
    def test_the_report_shows_the_human_deliverable(self):
        self._handback(note="I wrote it by hand")
        report = build_report(self.engine.ledger.entries())
        markdown = render_markdown(report)
        self.assertIn("人工交付物", markdown)
        self.assertIn("I wrote it by hand", markdown)

    def test_the_run_overview_says_where_the_answer_came_from(self):
        self._handback()
        report = build_report(self.engine.ledger.entries())
        overview = self._overview(report)
        # The run a person finished says so; the run the loop could
        # not finish says nothing, because nobody answered it.
        self.assertIn("🤝 人工交付物", overview)

    def test_a_run_the_loop_closed_says_the_loop_closed_it(self):
        """The other half of the mark: the source column has to be
        able to say "machine", or the handback mark means nothing."""
        engine = LoopEngine(_config(self.state_dir), provider=_Passes(model="m"))
        engine.run(goal="an easy question", criteria=CRITERIA)
        report = build_report(engine.ledger.entries())
        overview = self._overview(report)
        self.assertIn("🤖 机器闭环", overview)

    def test_the_problem_table_says_where_the_answer_came_from(self):
        self._handback()
        report = build_report(self.engine.ledger.entries())
        problems = self._problems(report)
        self.assertIn("🤝 人工完成", problems)

    def test_the_replay_shows_the_human_step(self):
        self._handback(note="I wrote it by hand")
        trace = replay_trace(self.engine.ledger.entries())
        handback_steps = [step for step in trace["steps"] if step.get("role") == "handback"]
        self.assertEqual(len(handback_steps), 1)
        step = handback_steps[0]
        self.assertIn("人工交回", step["detail"])
        self.assertIn("人工交回", step["detail_reading"])
        runs = [block for block in trace["runs"] if block.get("handback")]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["human_note"], "I wrote it by hand")

    def test_the_replay_step_is_not_an_empty_line(self):
        """The handback row used to render as an empty string - the
        trace had no branch for it - so the replay showed a blank line
        exactly where the person was."""
        self._handback()
        trace = replay_trace(self.engine.ledger.entries())
        handback_steps = [step for step in trace["steps"] if step.get("role") == "handback"]
        self.assertTrue(handback_steps[0]["detail"].strip())
        self.assertTrue(handback_steps[0]["detail_reading"].strip())

    def test_a_rejected_handback_is_still_reported_as_a_handback(self):
        """A handback that did not pass the standards is a handback
        too - it must not vanish from the report."""
        result = self.engine.handback(self.ticket_id, "nothing useful here", note="try")
        self.assertFalse(result.closed)
        report = build_report(self.engine.ledger.entries())
        handbacks = [run for run in report["runs"] if run.get("handback")]
        self.assertEqual(len(handbacks), 1)
        self.assertFalse(handbacks[0]["closed"])
        self.assertTrue(handbacks[0]["scores"])
        markdown = render_markdown(report)
        self.assertIn("人工交付物", markdown)


if __name__ == "__main__":
    unittest.main()

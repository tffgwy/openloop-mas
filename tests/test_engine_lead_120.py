"""The advisor's own words have to reach the report (v0.12.0).

Before this release the advisor's answer stopped at the ledger. The
ticket held it. The replay trace held it. The report - the page a
human actually reads after the run - did not, because
``run_summary()`` collected the ``reflector`` rows and never
collected the ``lead`` rows at all.

Measured before the fix:

    === does the ledger hold the advisor's own words? ===
      lead rows: 2
        has LEAD_REASON: True
        has LEAD_NOTE  : True
    === does the REPORT show the advisor's own words? ===
      report has LEAD_REASON: False
      report has LEAD_NOTE  : True      <-- only by accident, via issues

So a run that consulted an advisor read exactly like a run that never
consulted one. These tests pin the fixed behaviour: the report shows
the advisor's reason, its decision and its judgement of the
criterion, hangs each answer on the round it was given in, says it
once per round, and says nothing at all about an advisor that was
never consulted.

What does **not** change is pinned here too: the advisor stays an
advisor. It cannot overrule a machine check and it cannot close a
run - the report now shows what it said, but it says nothing that
changes the engine's verdict.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import tempfile
import unittest
from typing import Any, Dict, List, Tuple

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.providers import Provider
from mas.report import build_report, render_markdown
from mas.teams import load_team

#: The advisor's own reason. The marker is unique on purpose: a
#: repeated string would also show up in the tail of a clipped text,
#: and the test would read a broken world as a working one.
ADVISOR_REASON = "advisor-reason-marker-ADVISOR-REASON-UNIQUE-120"

#: The advisor's own note about the criterion it judged. It is
#: different from the reason on purpose - the note has to show up in
#: the judgement and the reason in the advice, and a single marker
#: would let one of the two pass by accident.
ADVISOR_NOTE = "advisor-note-marker-ADVISOR-NOTE-UNIQUE-120"

#: The word the report uses for the advisor's advice.
ADVISOR_WORD = "\u4e3b\u7ebf\u7684\u5efa\u8bae"


class _Probe(Provider):
    """A provider whose advisor answers whatever the test asked for.

    ``silent_verifier`` decides whether the advisor is consulted at
    all: a criterion that the machine checks has decided is not the
    advisor's to judge.
    """

    name = "probe"

    def __init__(self, silent_verifier: bool = True) -> None:
        super().__init__(model="probe-model")
        self.silent_verifier = silent_verifier
        self.calls: List[Tuple[str, str]] = []

    def complete(self, *, system="", prompt="", role="worker",
                 expect_json=False, max_tokens=4000):
        self.calls.append((role, prompt))
        usage = {"total_tokens": 10}
        if role == "lead":
            text = json.dumps({
                "decision": "revise",
                "reason": ADVISOR_REASON,
                "judged": [
                    {"id": "c1", "met": False, "note": ADVISOR_NOTE}
                ],
            })
        elif role == "verifier":
            scores: List[Dict[str, Any]] = []
            if not self.silent_verifier:
                scores = [
                    {"id": "c1", "pass": False, "note": "the machine decided"}
                ]
            text = json.dumps({"verdict": "fail", "scores": scores,
                               "issues": []})
        elif role == "reflector":
            text = json.dumps({
                "summary": "reflected", "issues": ["change this"],
                "next_action": "revise",
            })
        else:
            text = "deliverable"
        return {"text": text, "usage": usage, "raw": {}}


def _criteria():
    """One criterion with no machine check."""
    return [{"id": "c1", "desc": "must contain the keyword"}]


def _run(probe, max_iterations=3):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tempfile.mkdtemp()
    config["budget"] = dict(config["budget"])
    config["budget"]["max_iterations"] = max_iterations
    engine = LoopEngine(config, team=load_team("default"))
    engine.provider = probe
    result = engine.run(goal="make a thing", criteria=_criteria())
    rows = engine.ledger.entries()
    report = build_report(rows)
    return {
        "result": result,
        "rows": rows,
        "report": report,
        "markdown": render_markdown(report),
        "runs": list(report.get("runs") or []),
        "lead_rows": [
            row for row in rows if str(row.get("role") or "") == "lead"
        ],
    }


def _first_run(facts):
    runs = list(facts.get("runs") or [])
    return runs[0] if runs else {}


class TestTheReportShowsTheAdvisor(unittest.TestCase):
    def setUp(self):
        self.facts = _run(_Probe(silent_verifier=True))
        self.markdown = str(self.facts["markdown"])

    def test_the_advisor_was_actually_consulted(self):
        """Without this the rest of the test would prove nothing."""
        self.assertTrue(self.facts["lead_rows"])

    def test_the_report_shows_the_advisors_own_reason(self):
        self.assertIn(ADVISOR_REASON, self.markdown)

    def test_the_report_shows_what_the_advisor_decided(self):
        self.assertIn("the advisor decided: revise", self.markdown)

    def test_the_report_shows_the_advisors_judgement_of_the_criterion(self):
        self.assertIn("[c1]", self.markdown)
        self.assertIn(ADVISOR_NOTE, self.markdown)
        self.assertTrue(
            "not met" in self.markdown
            or "\u672a\u6ee1\u8db3" in self.markdown
        )

    def test_the_payload_carries_the_advisors_answers(self):
        run = _first_run(self.facts)
        advisories = list(run.get("advisories") or [])
        self.assertTrue(advisories)
        self.assertEqual(int(run.get("advisories_count") or 0),
                         len(advisories))
        self.assertEqual(
            int(run.get("advisories_count") or 0),
            len(self.facts["lead_rows"]),
        )
        for advice in advisories:
            self.assertEqual(str(advice.get("decision") or ""), "revise")
            self.assertEqual(str(advice.get("reason") or ""), ADVISOR_REASON)

    def test_the_payload_carries_the_advisors_judgement(self):
        run = _first_run(self.facts)
        judged = [
            item
            for advice in (run.get("advisories") or [])
            for item in (advice.get("judgements") or [])
            if isinstance(item, dict)
        ]
        self.assertTrue(judged)
        self.assertTrue(
            any(
                str(item.get("id") or "") == "c1"
                and ADVISOR_NOTE in str(item.get("note") or "")
                for item in judged
            )
        )

    def test_the_advice_is_attached_to_the_round_it_was_given_in(self):
        run = _first_run(self.facts)
        timeline = list(run.get("timeline") or [])
        with_advice = [
            entry for entry in timeline if str(entry.get("advisor") or "")
        ]
        self.assertEqual(len(with_advice), len(self.facts["lead_rows"]))
        for entry in with_advice:
            self.assertIn(ADVISOR_REASON, str(entry.get("advisor") or ""))
            self.assertEqual(
                str(entry.get("advisor_decision") or ""), "revise"
            )
            self.assertEqual(
                str(entry.get("advisor_reason") or ""), ADVISOR_REASON
            )

    def test_the_advice_is_shown_once_per_round_and_not_twice(self):
        lines = [
            line for line in self.markdown.splitlines()
            if ADVISOR_WORD in line
        ]
        self.assertEqual(len(lines), len(self.facts["lead_rows"]))
        for line in lines:
            self.assertIn(ADVISOR_REASON, line)


class TestTheReportIsSilentAboutAnAdvisorThatWasNeverAsked(unittest.TestCase):
    def setUp(self):
        self.facts = _run(_Probe(silent_verifier=False))
        self.markdown = str(self.facts["markdown"])

    def test_the_advisor_was_not_consulted(self):
        self.assertFalse(self.facts["lead_rows"])

    def test_the_report_says_nothing_about_an_advisor(self):
        self.assertNotIn(ADVISOR_WORD, self.markdown)
        self.assertNotIn(ADVISOR_REASON, self.markdown)
        self.assertNotIn(ADVISOR_NOTE, self.markdown)

    def test_the_payload_carries_no_advisories(self):
        run = _first_run(self.facts)
        self.assertEqual(int(run.get("advisories_count") or 0), 0)
        self.assertFalse(list(run.get("advisories") or []))
        for entry in (run.get("timeline") or []):
            self.assertFalse(str(entry.get("advisor") or ""))


class TestTheReportShowsTheAdvisorInASingleRoundRun(unittest.TestCase):
    """A run that closes in one go takes the other rendering branch.

    A branch that forgets the advisor is exactly the kind of hole that
    only shows up in a run that never iterated.
    """

    def test_the_single_round_report_shows_the_advisor(self):
        facts = _run(_Probe(silent_verifier=True), max_iterations=1)
        self.assertTrue(facts["lead_rows"])
        markdown = str(facts["markdown"])
        self.assertIn(ADVISOR_REASON, markdown)
        self.assertIn(ADVISOR_WORD, markdown)

    def test_the_single_round_payload_carries_the_advisor(self):
        facts = _run(_Probe(silent_verifier=True), max_iterations=1)
        run = _first_run(facts)
        self.assertEqual(int(run.get("advisories_count") or 0), 1)
        timeline = list(run.get("timeline") or [])
        self.assertEqual(len(timeline), 1)
        self.assertIn(ADVISOR_REASON, str(timeline[0].get("advisor") or ""))


class TestTheAdvisorStillCannotOverruleTheEngine(unittest.TestCase):
    """The report now shows what the advisor said - and that changes
    nothing about what the engine did.
    """

    def test_a_criterion_the_advisor_judged_as_not_met_does_not_close(self):
        facts = _run(_Probe(silent_verifier=True))
        self.assertFalse(facts["result"].closed)


class TestTheReportReadsTheAdvisorsVerdictTheSameWayTheEngineDoes(unittest.TestCase):
    """One decision, one reading.

    The engine folds the advisor's answer into the criteria with
    ``_as_bool``, so a model that answers the string ``"true"`` was
    recorded as a satisfied criterion. A report that read the same
    answer strictly would print the very same run as "not met" - a
    second, competing truth about one decision.
    """

    def test_the_string_true_is_read_as_satisfied(self):
        from mas.report import _advisor_judgements

        row = {
            "lead": {
                "decision": "revise",
                "reason": ADVISOR_REASON,
                "judged": [{"id": "c1", "met": "true", "note": ADVISOR_NOTE}],
            }
        }
        judged = _advisor_judgements(row)
        self.assertTrue(judged)
        self.assertEqual(str(judged[0].get("met") or ""), "yes")
        self.assertIn("met", str(judged[0].get("mark") or ""))
        self.assertNotIn("not met", str(judged[0].get("mark") or ""))

    def test_the_string_false_is_read_as_not_satisfied(self):
        from mas.report import _advisor_judgements

        row = {
            "lead": {
                "judged": [{"id": "c1", "met": "false", "note": ADVISOR_NOTE}],
            }
        }
        judged = _advisor_judgements(row)
        self.assertEqual(str(judged[0].get("met") or ""), "no")
        self.assertIn("not met", str(judged[0].get("mark") or ""))

    def test_an_unreadable_verdict_is_neither_met_nor_not_met(self):
        from mas.report import _advisor_judgements

        row = {
            "lead": {
                "judged": [{"id": "c1", "met": 0.5, "note": ADVISOR_NOTE}],
            }
        }
        judged = _advisor_judgements(row)
        self.assertEqual(str(judged[0].get("met") or ""), "")
        self.assertNotIn("not met", str(judged[0].get("mark") or ""))
        self.assertNotIn("met", str(judged[0].get("mark") or ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

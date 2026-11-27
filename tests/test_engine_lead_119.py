"""The advisor's judgement has to reach the next worker (v0.11.9).

Before this release the advisor's answer stopped at the ledger.
Measured with a probe provider:

    === the advisor judged c1 as NOT met ===
      result.issues : ['c1: unjudged: the review gave no verdict']
      -> the issue still says 'unjudged'        : True
      -> the advisor's own note is nowhere in it : True

    === worker #2 (the one after the advisor spoke) ===
      sees lead NOTE     : False
      sees lead REASON   : False
      sees 'unjudged'    : True

Two facts were broken, and they are the same fact seen from two
sides:

* the issues list was built **before** the advisor was consulted and
  was never rebuilt afterwards, so a criterion the advisor had judged
  "not met", with its own note attached, still showed up as
  "unjudged: the review gave no verdict" - a complaint about a
  *missing* answer, thrown at a worker who cannot produce a verdict;
* the advisor's reason - the one sentence that says *what* is missing -
  never travelled to the attempt that has to fix it.

These tests pin the fixed behaviour: the issues are built from the
scores as they are *after* the advisor spoke, and the advisor's own
words reach the next attempt and the reflector.

What does **not** change is pinned here too: the advisor is an
advisor, not a judge. A failed machine check stays failed, and the
advisor's "close" cannot close a run whose checks did not pass.
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
from typing import Any, Dict, List, Optional, Tuple

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.providers import Provider
from mas.teams import load_team

#: The advisor's reason. The marker is unique on purpose: a repeated
#: string would also show up in the tail of a clipped text, and the
#: test would read a broken world as a working one.
LEAD_REASON = "advisor-reason-marker-LEAD-REASON-UNIQUE-119"

#: The advisor's own note about the criterion it judged. It is
#: different from the reason on purpose - the note has to show up in
#: the issues and the reason in the advice block, and a single marker
#: would let one of the two pass by accident.
LEAD_NOTE = "advisor-note-marker-LEAD-NOTE-UNIQUE-119"

#: The machine's complaint about the same criterion - the one the
#: issues list used to carry instead of the advisor's note.
MACHINE_COMPLAINT = "unjudged"


class _Probe(Provider):
    """A provider whose advisor answers whatever the test asked for."""

    name = "probe"

    def __init__(self, decision: str, met: Optional[bool] = False,
                 worker_text: str = "deliverable") -> None:
        super().__init__(model="probe-model")
        self.decision = decision
        self.met = met
        self.worker_text = worker_text
        self.calls: List[Tuple[str, str]] = []

    def complete(self, *, system="", prompt="", role="worker",
                 expect_json=False, max_tokens=4000):
        self.calls.append((role, prompt))
        usage = {"total_tokens": 10}
        if role == "lead":
            judged: List[Dict[str, Any]] = []
            if self.met is not None:
                judged.append(
                    {"id": "c1", "met": self.met, "note": LEAD_NOTE}
                )
            text = json.dumps({
                "decision": self.decision,
                "reason": LEAD_REASON,
                "judged": judged,
            })
        elif role == "verifier":
            # The verifier stays silent, so the criterion has no
            # verdict and the advisor is consulted at all.
            text = json.dumps({"verdict": "fail", "scores": [], "issues": []})
        elif role == "reflector":
            text = json.dumps({
                "summary": "reflected", "issues": ["change this"],
                "next_action": "revise",
            })
        else:
            text = self.worker_text
        return {"text": text, "usage": usage, "raw": {}}


def _make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


def _criteria():
    """One criterion with no machine check.

    A criterion with a machine check is decided by the machine, and an
    advisor that disagrees with a fact is not consulted at all - so the
    advisor could not be observed with one.
    """
    return [{"id": "c1", "desc": "must contain the keyword"}]


def _run(probe, max_iterations=3):
    config = _make_config(
        tempfile.mkdtemp(), budget={"max_iterations": max_iterations}
    )
    engine = LoopEngine(config, team=load_team("default"))
    engine.provider = probe
    result = engine.run(goal="make a thing", criteria=_criteria())
    return probe, result, engine


def _prompts(probe, role):
    return [prompt for name, prompt in probe.calls if name == role]


def _brief(prompt):
    """The readable half of a prompt - everything before the raw state."""
    marker = "<<MAS_STATE>>"
    if not prompt:
        return ""
    if marker not in prompt:
        return prompt
    return prompt.split(marker, 1)[0]


def _state(prompt):
    """The raw state a prompt carried."""
    marker = "<<MAS_STATE>>"
    if not prompt or marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1]
    end = tail.find("<<END>>")
    if end == -1:
        return {}
    try:
        data = json.loads(tail[:end].strip())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class TestTheIssuesCarryTheAdvisorsOwnWords(unittest.TestCase):
    def test_the_issue_no_longer_says_nobody_judged_the_criterion(self):
        __, result, __ = _run(_Probe("revise", met=False))
        issues = [str(item) for item in (result.issues or [])]
        self.assertTrue(issues)
        self.assertFalse(
            any(MACHINE_COMPLAINT in item for item in issues),
            "the issue still claims nobody judged it: %r" % (issues,),
        )

    def test_the_issue_carries_the_advisors_note(self):
        __, result, __ = _run(_Probe("revise", met=False))
        issues = [str(item) for item in (result.issues or [])]
        self.assertTrue(
            any(LEAD_NOTE in item for item in issues),
            "the advisor's own note is nowhere in the issues: %r" % (issues,),
        )

    def test_the_issue_still_names_the_criterion(self):
        __, result, __ = _run(_Probe("revise", met=False))
        issues = [str(item) for item in (result.issues or [])]
        self.assertTrue(any(item.startswith("c1") for item in issues))

    def test_the_score_carries_the_advisors_note_too(self):
        __, result, __ = _run(_Probe("revise", met=False))
        scores = [s for s in (result.scores or []) if s.get("id") == "c1"]
        self.assertTrue(scores)
        self.assertEqual(str(scores[0].get("source") or ""), "lead")
        self.assertIn(LEAD_NOTE, str(scores[0].get("note") or ""))


class TestTheNextAttemptSeesTheAdvisor(unittest.TestCase):
    def setUp(self):
        self.probe, self.result, __ = _run(_Probe("revise", met=False))

    def test_the_next_attempt_exists(self):
        self.assertGreaterEqual(len(_prompts(self.probe, "worker")), 2)

    def test_the_next_attempt_is_shown_the_advisors_section(self):
        second = _prompts(self.probe, "worker")[1]
        self.assertIn("ADVISOR", _brief(second).upper())

    def test_the_next_attempt_sees_the_advisors_reason(self):
        second = _prompts(self.probe, "worker")[1]
        self.assertIn(LEAD_REASON, _brief(second))

    def test_the_next_attempt_sees_the_advisors_judgement(self):
        second = _prompts(self.probe, "worker")[1]
        brief = _brief(second)
        self.assertIn(LEAD_NOTE, brief)
        self.assertIn("[c1]", brief)

    def test_the_next_attempt_no_longer_sees_the_machine_complaint(self):
        second = _prompts(self.probe, "worker")[1]
        self.assertNotIn(MACHINE_COMPLAINT, _brief(second))

    def test_the_next_attempt_carries_the_advice_in_the_raw_state(self):
        second = _prompts(self.probe, "worker")[1]
        advice = _state(second).get("lead_advice") or {}
        self.assertIsInstance(advice, dict)
        self.assertIn(LEAD_REASON, str(advice.get("reason") or ""))

    def test_the_reflector_is_shown_the_advisors_words(self):
        reflectors = _prompts(self.probe, "reflector")
        self.assertTrue(reflectors)
        self.assertIn(LEAD_REASON, _brief(reflectors[0]))

    def test_the_reflector_carries_the_advice_in_the_raw_state(self):
        reflectors = _prompts(self.probe, "reflector")
        advice = _state(reflectors[0]).get("lead_advice") or {}
        self.assertIsInstance(advice, dict)
        self.assertIn(LEAD_REASON, str(advice.get("reason") or ""))


class TestTheAdvisorStillCannotOverruleTheEngine(unittest.TestCase):
    def test_the_escalation_still_stops_the_run_early(self):
        probe, result, __ = _run(_Probe("escalate", met=False))
        self.assertEqual(result.status, "escalated")
        self.assertFalse(result.closed)
        self.assertEqual(len(_prompts(probe, "lead")), 1)
        self.assertEqual(len(_prompts(probe, "worker")), 1)

    def test_a_criterion_the_advisor_judged_as_met_still_closes(self):
        __, result, __ = _run(_Probe("revise", met=True))
        self.assertTrue(result.closed)

    def test_a_lead_that_says_close_still_does_not_close_the_run(self):
        __, result, __ = _run(_Probe("close", met=False))
        self.assertFalse(result.closed)
        self.assertNotEqual(result.status, "closed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

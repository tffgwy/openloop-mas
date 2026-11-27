"""The lead's decision has to be read (v0.11.8).

Before this release the advisor's ``decision`` was never read by the
engine. Measured with a probe provider:

* a lead that said "escalate", one that said "close" and one that
  said "revise" produced **byte-identical** runs;
* the advisor's request for a human was ignored, so the run kept
  burning rounds until the iteration ceiling - the request cost the
  user every remaining round;
* and the engine's own stagnation escalation was reset to "revise"
  by the advisor branch, so a stuck loop got one more round instead
  of being handed over.

These tests pin the fixed behaviour against the run's outcome and
against the ticket the run left behind.
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
LEAD_REASON = "advisor-reason-marker-LEAD-REASON-UNIQUE"


class _Probe(Provider):
    """A provider whose advisor answers whatever the test asked for."""

    name = "probe"

    def __init__(self, decision: str, met: Optional[bool] = False,
                 worker_text: str = "交付物") -> None:
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
                    {"id": "c1", "met": self.met, "note": "advisor note"}
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
                "summary": "复盘", "issues": ["改"], "next_action": "revise",
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

    A criterion with a machine check is decided by the machine, and
    the advisor is not consulted at all when the machine spoke - so
    the advisor could not be observed with one.
    """
    return [{"id": "c1", "desc": "必须包含关键词"}]


def _run(probe, max_iterations=3, stagnation_limit=None):
    config = _make_config(
        tempfile.mkdtemp(), budget={"max_iterations": max_iterations}
    )
    if stagnation_limit is not None:
        config["stagnation_limit"] = stagnation_limit
    engine = LoopEngine(config, team=load_team("default"))
    engine.provider = probe
    result = engine.run(goal="做一个东西", criteria=_criteria())
    ticket = engine.tickets.get(result.ticket_id) if result.ticket_id else {}
    return probe, result, dict(ticket.get("payload") or {})


def _roles(probe, role):
    return [name for name, _ in probe.calls if name == role]


class TestTheLeadDecisionIsRead(unittest.TestCase):
    def test_a_lead_that_says_escalate_stops_the_run(self):
        __, result, __ = _run(_Probe("escalate"))
        self.assertFalse(result.closed)
        self.assertEqual(result.status, "escalated")

    def test_the_escalation_is_entered_in_the_history(self):
        __, result, __ = _run(_Probe("escalate"))
        decisions = [row.get("decision") for row in result.history]
        self.assertIn("escalate", decisions)

    def test_the_run_stops_after_the_first_advisor_call(self):
        # The advisor asked for a human. The run must not spend the
        # rounds it was allowed - the request is there to stop the
        # spending, not to be noted and ignored.
        probe, _result, _ = _run(_Probe("escalate"), max_iterations=3)
        self.assertEqual(len(_roles(probe, "lead")), 1)
        self.assertEqual(len(_roles(probe, "worker")), 1)

    def test_the_reason_names_the_advisor(self):
        __, _result, payload = _run(_Probe("escalate"))
        reason = str(payload.get("reason") or "")
        self.assertIn("lead escalation", reason.lower())

    def test_the_reason_carries_the_advisors_own_words(self):
        __, _result, payload = _run(_Probe("escalate"))
        self.assertIn(LEAD_REASON, str(payload.get("reason") or ""))

    def test_the_ticket_carries_the_structured_answer(self):
        __, _result, payload = _run(_Probe("escalate"))
        verdict = payload.get("lead_verdict")
        self.assertIsInstance(verdict, dict)
        self.assertEqual(str(verdict.get("decision") or ""), "escalate")
        self.assertEqual(str(verdict.get("reason") or ""), LEAD_REASON)

    def test_the_run_stops_on_the_advisor_not_on_the_budget(self):
        # The run was allowed three rounds. It stopped after one, on
        # the advisor's judgement - not because it ran out of budget.
        __, result, _payload = _run(_Probe("escalate"), max_iterations=3)
        self.assertNotIn("budget", str(result.error or "").lower())
        self.assertNotEqual(result.status, "budget_exhausted")


class TestTheLeadCannotOverruleTheEngine(unittest.TestCase):
    def test_a_lead_that_says_close_does_not_close_the_run(self):
        # The advisor is an advisor. It cannot turn an unjudged
        # criterion into a pass by saying so.
        __, result, _payload = _run(_Probe("close", met=False))
        self.assertFalse(result.closed)
        self.assertNotEqual(result.status, "closed")

    def test_a_lead_that_says_close_does_not_overrule_a_failed_check(self):
        # A criterion the machine decided is not the advisor's to
        # decide - and the advisor is not even consulted then, so
        # there is nothing for it to overrule in the first place.
        config = _make_config(tempfile.mkdtemp())
        probe = _Probe("close", met=True)
        engine = LoopEngine(config, team=load_team("default"))
        engine.provider = probe
        result = engine.run(goal="做一个东西", criteria=[{
            "id": "c1", "desc": "必须包含关键词",
            "check": {"type": "contains", "arg": "关键词"},
        }])
        self.assertFalse(result.closed)
        self.assertEqual(_roles(probe, "lead"), [])

    def test_a_lead_that_says_revise_keeps_the_run_open(self):
        __, result, _payload = _run(_Probe("revise", met=False))
        self.assertFalse(result.closed)


class TestTheEngineStopSurvivesTheAdvisor(unittest.TestCase):
    def test_a_stagnation_stop_is_not_demoted_to_revise(self):
        # The engine escalated on its own evidence. The advisor's
        # answer must not undo that - otherwise the stuck loop gets
        # one more round instead of being handed over.
        __, result, _payload = _run(
            _Probe("close", met=False, worker_text="恒定交付物"),
            max_iterations=6,
            stagnation_limit=2,
        )
        self.assertEqual(result.status, "escalated")
        self.assertIn("stagnation", str(result.error or "").lower())

    def test_the_stagnation_reason_survives_on_the_ticket(self):
        __, _result, payload = _run(
            _Probe("close", met=False, worker_text="恒定交付物"),
            max_iterations=6,
            stagnation_limit=2,
        )
        self.assertIn(
            "stagnation", str(payload.get("reason") or "").lower()
        )

    def test_the_stagnation_stop_is_not_overwritten_by_the_advisor(self):
        # The advisor said "close" here. The reason on the ticket
        # still has to be the stagnation - the engine's own evidence
        # - and not the advisor's word.
        __, _result, payload = _run(
            _Probe("close", met=False, worker_text="恒定交付物"),
            max_iterations=6,
            stagnation_limit=2,
        )
        self.assertNotIn(
            "lead escalation", str(payload.get("reason") or "").lower()
        )


class TestTheAdvisorStillGetsItsSay(unittest.TestCase):
    def test_the_advisor_is_consulted_when_nobody_could_judge(self):
        probe, _result, _payload = _run(_Probe("revise", met=False))
        self.assertEqual(len(_roles(probe, "lead")), 3)

    def test_the_advisors_judgement_still_fills_the_gap(self):
        # The advisor's "judged" list was always honoured, and it
        # still is: a criterion nobody else could judge becomes the
        # advisor's to decide.
        __, result, _payload = _run(_Probe("revise", met=True))
        scores = [s for s in (result.scores or []) if s.get("id") == "c1"]
        self.assertTrue(scores)
        self.assertEqual(str(scores[0].get("source") or ""), "lead")

    def test_the_advisors_judgement_can_close_the_loop(self):
        # Nothing changed for the "judged" list: a lead that judged
        # every criterion as met still closes the run, because that is
        # the one thing the advisor is allowed to decide.
        __, result, _payload = _run(_Probe("revise", met=True))
        self.assertTrue(result.closed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

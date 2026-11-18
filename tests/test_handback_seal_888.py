# -*- coding: utf-8 -*-
"""The handback path must be sealed too - and must not fall over.

v0.7.88 sealed every closure to the bytes it was decided on. The
handback path - a human deliverable judged by the loop's own
standards - is a closure like any other, so it must be sealed the same
way.

It was not. The two handback paths passed a variable that did not
exist in their scope, so a handback raised ``NameError`` *after* the
work was judged and written. The deliverable was safe, the closure
was decided - and the run died on the way to the ledger. The bug was
invisible to the tests because none of them asked for a seal from a
handback.

This file pins two things:

* a handback that closes carries a seal, and that seal holds;
* a handback that is judged does not raise, whatever it decides.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.providers import Provider  # noqa: E402
from mas.seal import seal_report, verify_seal  # noqa: E402


class _AlwaysFails(Provider):
    """The verifier always says no - the loop escalates to a human."""

    name = "handback-seal-test"

    def __init__(self, model: str = "test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        self.calls += 1
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
        elif role == "verifier":
            text = json.dumps(
                {"verdict": "fail", "scores": [{"id": "c1", "pass": False, "note": "no"}]}
            )
        elif role == "reflector":
            text = json.dumps({"summary": "s", "issues": [], "next_action": "revise"})
        elif role == "worker":
            text = "the answer is 41."
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


CRITERIA = [{"id": "c1", "desc": "42 appears", "check": {"type": "contains", "arg": "42"}}]


def _config(tmp, **budget):
    base = {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60}
    base.update(budget)
    return {
        "provider": "mock",
        "model": "test-model",
        "state_dir": os.path.join(tmp, "state"),
        "workspace": "",
        "budget": base,
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
        "stagnation_limit": 2,
    }


class HandbackSealTest(unittest.TestCase):
    def _escalated(self, tmp):
        engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="test-model"))
        result = engine.run(goal="write a report", criteria=CRITERIA)
        self.assertFalse(result.closed)
        return engine, str(result.ticket_id)

    def test_a_handback_that_closes_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            result = engine.handback(ticket_id, "the answer is 42.", note="by hand")
            self.assertTrue(result.closed, result.error)

    def test_a_handback_that_fails_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            result = engine.handback(ticket_id, "the answer is 41.")
            self.assertFalse(result.closed)

    def test_a_handback_is_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            engine.handback(ticket_id, "the answer is 42.")
            rows = engine.ledger.entries()
            ends = [r for r in rows if r.get("role") == "run_end"]
            self.assertTrue(ends)
            handback_end = [r for r in ends if r.get("handback")]
            self.assertTrue(handback_end, "the handback wrote no run_end entry")
            seal = handback_end[-1].get("seal")
            self.assertIsInstance(seal, dict)
            self.assertTrue(seal)
            self.assertEqual(seal.get("verdict"), "pass")
            self.assertEqual(seal.get("decision"), "handback")
            self.assertTrue(seal.get("criteria"))

    def test_the_handback_seal_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            engine.handback(ticket_id, "the answer is 42.")
            rows = engine.ledger.entries()
            ends = [r for r in rows if r.get("role") == "run_end" and r.get("handback")]
            artifact = None
            path = ends[-1].get("artifact_path")
            if path:
                artifact = Path(path).read_text(encoding="utf-8")
            report = verify_seal(ends[-1]["seal"], artifact)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["state"], "holds")

    def test_the_ledger_report_sees_the_handback_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            engine.handback(ticket_id, "the answer is 42.")
            report = seal_report(engine.ledger.entries())
            self.assertGreaterEqual(report["sealed"], 1)
            self.assertTrue(report["ok"], report["results"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

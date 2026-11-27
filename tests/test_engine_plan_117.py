"""The plan has to reach the *first* worker (v0.11.7).

Before this release the planner's answer was paid for, booked in the
ledger, and then handed to the worker of the **second** attempt only.
Measured with a probe provider that recorded every prompt:

* the state of an iteration is built *before* the iteration runs, and
  the planner runs *inside* the iteration - so the state was built
  with the plan of a planner that had not run yet: an empty dict;
* the planner's answer went into ``plan_payload`` and never went back
  into ``state``;
* the first attempt - the one that actually produces the deliverable
  from nothing - was written **blind**;
* in a run allowed a single iteration the plan reached **no** worker
  at all: the planner was called, the money was spent, and the answer
  was thrown away.

These tests pin the fixed behaviour against the prompts themselves,
because the prompts are what a real model reads.
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
from typing import List, Tuple

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.providers import Provider
from mas.teams import load_team

# The markers are unique on purpose: a repeated string would also show
# up in the tail of a clipped prompt, and the test would read a broken
# world as a working one.
PLAN_SUMMARY = "计划摘要标记-PLAN-SUMMARY-UNIQUE"
PLAN_STEP = "计划步骤标记-PLAN-STEP-UNIQUE"


def _make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


class _Probe(Provider):
    """A provider that keeps every prompt it is given."""

    name = "probe"

    def __init__(self) -> None:
        super().__init__(model="probe-model")
        self.calls: List[Tuple[str, str]] = []

    def complete(self, *, system="", prompt="", role="worker",
                 expect_json=False, max_tokens=4000):
        self.calls.append((role, prompt))
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({
                "summary": PLAN_SUMMARY,
                "plan": [PLAN_STEP],
                "risks": [],
            })
        elif role == "reflector":
            text = json.dumps({
                "summary": "复盘",
                "issues": ["改"],
                "next_action": "revise",
            })
        elif role == "verifier":
            text = json.dumps({
                "verdict": "fail",
                "scores": [{"id": "c1", "pass": False, "note": "missing"}],
                "issues": ["c1"],
            })
        else:
            worker_calls = len([r for r, _ in self.calls if r == "worker"])
            text = "第一版交付物" if worker_calls <= 1 else "第二版交付物"
        return {"text": text, "usage": usage, "raw": {}}


def _criteria():
    """One criterion the deliverable cannot satisfy."""
    return [{
        "id": "c1",
        "desc": "必须包含关键词",
        "check": {"type": "contains", "arg": "关键词"},
    }]


def _run(probe, max_iterations=3):
    config = _make_config(
        tempfile.mkdtemp(), budget={"max_iterations": max_iterations}
    )
    engine = LoopEngine(config, team=load_team("default"))
    engine.provider = probe
    engine.run(goal="做一个东西", criteria=_criteria())
    return probe.calls


def _prompts(calls, role):
    return [prompt for name, prompt in calls if name == role]


def _state_of(prompt):
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


class TestThePlanReachesTheFirstWorker(unittest.TestCase):
    def test_the_first_attempt_sees_the_plan_summary(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 1)
        self.assertIn(PLAN_SUMMARY, workers[0])

    def test_the_first_attempt_sees_the_plan_steps(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 1)
        self.assertIn(PLAN_STEP, workers[0])

    def test_the_plan_is_in_the_state_and_not_only_in_the_prose(self):
        # A brief can be right while the state that travels with it
        # is empty - and then the verifier and the reflector read the
        # empty one.
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        plan = _state_of(workers[0]).get("plan") or {}
        self.assertIsInstance(plan, dict)
        self.assertEqual(str(plan.get("summary") or ""), PLAN_SUMMARY)
        self.assertIn(PLAN_STEP, [str(s) for s in (plan.get("plan") or [])])

    def test_a_single_iteration_run_reaches_its_worker_with_the_plan(self):
        # The case where the plan used to reach nobody at all.
        calls = _run(_Probe(), max_iterations=1)
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 1)
        self.assertIn(PLAN_SUMMARY, workers[0])
        self.assertIn(PLAN_STEP, workers[0])

    def test_the_second_attempt_still_sees_the_plan(self):
        # The fix must not trade one attempt for another.
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 2)
        self.assertIn(PLAN_SUMMARY, workers[1])

    def test_the_planner_was_actually_called_and_paid_for(self):
        # A plan that appears without a planner call would mean the
        # gate is looking at a fiction.
        calls = _run(_Probe())
        self.assertGreaterEqual(len(_prompts(calls, "planner")), 1)

    def test_the_plan_is_the_planners_own_answer(self):
        calls = _run(_Probe())
        planners = _prompts(calls, "planner")
        self.assertGreaterEqual(len(planners), 1)
        workers = _prompts(calls, "worker")
        self.assertIn(PLAN_SUMMARY, workers[0])
        self.assertNotIn("PLANNER-NOT-CALLED", workers[0])


class TestTheStateCarriesThePlan(unittest.TestCase):
    def test_the_state_of_the_first_iteration_is_not_an_empty_plan(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        state = _state_of(workers[0])
        self.assertTrue(state.get("plan"))

    def test_the_planner_still_runs_only_once(self):
        calls = _run(_Probe())
        self.assertEqual(len(_prompts(calls, "planner")), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

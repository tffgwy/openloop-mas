# -*- coding: utf-8 -*-
"""A takeover must see the work, and must get a budget of its own.

v0.7.66 fixed four things that made a takeover a formality:

1. a run that ran out of tokens was filed as an escalation, so the
   human queue could not tell a budget problem from a stuck worker;
2. the resumed run could not see the deliverable of the last attempt,
   so the worker was asked for it from nothing;
3. the resumed run inherited the ceilings of the machine-wide
   configuration, so it stopped at the same place again;
4. the "continue this" command every screen printed inherited the same
   ceilings, so following the advice on screen was a dead end.

Every test here runs offline: no endpoint, no key, no network.
"""
import json
import os
import tempfile
import unittest

from mas.engine import LoopEngine
from mas.providers import Provider, ProviderError
from mas.reason_text import continuation_command, widen_budget

CRITERIA = [
    {"id": "c1", "desc": "正文里要出现 42", "check": {"type": "contains", "arg": "42"}},
]


class _Provider(Provider):
    """A worker whose text the human can repair, and a verifier that never passes."""

    name = "test-takeover"

    def __init__(self, model="m", **options):
        super().__init__(model=model, **options)
        self.repaired = False
        self.worker_prompts = []

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        usage = {"total_tokens": 10}
        if role == "planner":
            return {"text": json.dumps({"summary": "s", "plan": ["p"], "risks": []}),
                    "usage": usage, "raw": {}}
        if role == "verifier":
            return {"text": json.dumps({"verdict": "fail",
                                        "scores": [{"id": "c1", "pass": False, "note": "n"}]}),
                    "usage": usage, "raw": {}}
        if role == "reflector":
            return {"text": json.dumps({"summary": "s", "issues": [],
                                        "next_action": "revise"}),
                    "usage": usage, "raw": {}}
        if role == "worker":
            self.worker_prompts.append(prompt)
            return {"text": "结论。数据是 42。" if self.repaired else "结论。数据是 41。",
                    "usage": usage, "raw": {}}
        return {"text": "{}", "usage": usage, "raw": {}}


class _BrokenProvider(Provider):
    name = "test-broken"

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        raise ProviderError("the endpoint is not reachable")


def _config(tmp, **budget):
    base = {"max_iterations": 6, "max_tokens": 1000000, "deadline_sec": 60}
    base.update(budget)
    return {
        "provider": "mock",
        "model": "m",
        "state_dir": os.path.join(tmp, "state"),
        "workspace": "",
        "budget": base,
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
        "stagnation_limit": 2,
    }


class StopIsClassifiedTest(unittest.TestCase):
    """How the run stopped has to be visible on the ticket."""

    def _run(self, **budget):
        tmp = tempfile.TemporaryDirectory(prefix="mas-stop-")
        self.addCleanup(tmp.cleanup)
        provider = _Provider(model="m")
        engine = LoopEngine(_config(tmp.name, **budget), provider=provider)
        result = engine.run(goal="写一份 Q3 报告", criteria=CRITERIA)
        return engine, result

    def test_the_token_ceiling_is_a_budget_stop_not_an_escalation(self):
        engine, result = self._run(max_tokens=1)
        self.assertFalse(result.closed)
        self.assertEqual(result.status, "budget_exhausted", result.error)
        ticket = engine.tickets.get(result.ticket_id)
        self.assertEqual(ticket["kind"], "budget_exhausted")
        self.assertEqual(ticket["payload"]["status"], "budget_exhausted")
        self.assertIn("tokens", ticket["payload"]["reason"])

    def test_the_iteration_ceiling_is_a_budget_stop(self):
        engine, result = self._run(max_iterations=1)
        self.assertEqual(result.status, "budget_exhausted", result.error)
        ticket = engine.tickets.get(result.ticket_id)
        self.assertEqual(ticket["kind"], "budget_exhausted")
        self.assertIn("max_iterations", ticket["payload"]["reason"])

    def test_the_wall_clock_ceiling_is_a_budget_stop(self):
        engine, result = self._run(deadline_sec=0)
        self.assertEqual(result.status, "budget_exhausted", result.error)
        ticket = engine.tickets.get(result.ticket_id)
        self.assertEqual(ticket["kind"], "budget_exhausted")
        self.assertIn("deadline_sec", ticket["payload"]["reason"])

    def test_a_dead_backend_is_its_own_kind(self):
        tmp = tempfile.TemporaryDirectory(prefix="mas-broken-")
        self.addCleanup(tmp.cleanup)
        engine = LoopEngine(_config(tmp.name), provider=_BrokenProvider(model="m"))
        result = engine.run(goal="写一份 Q3 报告", criteria=CRITERIA)
        self.assertEqual(result.status, "error", result.error)
        ticket = engine.tickets.get(result.ticket_id)
        self.assertEqual(ticket["kind"], "run_error")
        self.assertEqual(ticket["payload"]["status"], "error")

    def test_the_ticket_carries_the_budget_the_run_ran_under(self):
        engine, result = self._run(max_tokens=1)
        budget = engine.tickets.get(result.ticket_id)["payload"]["budget"]
        self.assertEqual(int(budget["max_tokens"]), 1)
        self.assertIn("max_iterations", budget)
        self.assertIn("deadline_sec", budget)


class TheTakeoverSeesTheWorkTest(unittest.TestCase):
    def test_the_worker_of_the_takeover_is_shown_the_last_deliverable(self):
        tmp = tempfile.TemporaryDirectory(prefix="mas-see-")
        self.addCleanup(tmp.cleanup)
        provider = _Provider(model="m")
        engine = LoopEngine(_config(tmp.name, max_tokens=1), provider=provider)
        result = engine.run(goal="写一份 Q3 报告", criteria=CRITERIA)
        self.assertFalse(result.closed)

        provider.repaired = True
        engine.resume(
            result.ticket_id,
            note="人工接手",
            max_iterations=8,
            deadline_sec=60,
            max_tokens=20000,
        )
        last_prompt = provider.worker_prompts[-1]
        self.assertIn("PREVIOUS", last_prompt)
        self.assertIn("数据是 41", last_prompt)


class TheTakeoverGetsAFreshBudgetTest(unittest.TestCase):
    def test_a_takeover_can_close_under_a_budget_of_its_own(self):
        tmp = tempfile.TemporaryDirectory(prefix="mas-budget-")
        self.addCleanup(tmp.cleanup)
        provider = _Provider(model="m")
        engine = LoopEngine(_config(tmp.name, max_tokens=1), provider=provider)
        result = engine.run(goal="写一份 Q3 报告", criteria=CRITERIA)
        self.assertFalse(result.closed)

        provider.repaired = True
        resumed = engine.resume(
            result.ticket_id,
            note="人工接手",
            max_iterations=8,
            deadline_sec=60,
            max_tokens=20000,
        )
        self.assertTrue(resumed.closed, resumed.to_dict())
        by_id = {str(s.get("id")): s for s in (resumed.scores or [])}
        self.assertEqual(by_id["c1"]["source"], "rule")
        ticket = engine.tickets.get(result.ticket_id)
        self.assertEqual(ticket["state"], "resolved")

    def test_the_override_does_not_leak_into_the_next_run(self):
        tmp = tempfile.TemporaryDirectory(prefix="mas-leak-")
        self.addCleanup(tmp.cleanup)
        provider = _Provider(model="m")
        engine = LoopEngine(_config(tmp.name, max_tokens=1), provider=provider)
        result = engine.run(goal="写一份 Q3 报告", criteria=CRITERIA)
        self.assertFalse(result.closed)
        provider.repaired = True
        engine.resume(result.ticket_id, note="接手", max_iterations=8,
                      deadline_sec=60, max_tokens=20000)
        # The next run on the same engine has to be back on the
        # configured ceiling - the override was for that run only.
        self.assertEqual(int(engine.config["budget"]["max_tokens"]), 1)

    def test_the_run_result_carries_the_budget_it_ran_under(self):
        tmp = tempfile.TemporaryDirectory(prefix="mas-result-")
        self.addCleanup(tmp.cleanup)
        provider = _Provider(model="m")
        engine = LoopEngine(_config(tmp.name, max_tokens=1), provider=provider)
        result = engine.run(goal="写一份 Q3 报告", criteria=CRITERIA)
        self.assertEqual(int(result.budget["max_tokens"]), 1)
        self.assertEqual(int(result.to_dict()["budget"]["max_tokens"]), 1)


class TheContinuationCommandTest(unittest.TestCase):
    def test_a_budget_stop_comes_with_a_wider_budget(self):
        command = continuation_command(
            "T-1", "budget: tokens 40 >= 1",
            {"max_iterations": 6, "deadline_sec": 60, "max_tokens": 1},
        )
        self.assertTrue(command.startswith("mas resume T-1"))
        self.assertIn("--max-tokens", command)
        self.assertIn("--note", command)

    def test_a_widened_ceiling_is_always_wider(self):
        for reason, budget in (
            ("budget: tokens 40 >= 1", {"max_tokens": 1}),
            ("budget: reached max_iterations=1", {"max_iterations": 1}),
            ("budget: exceeded deadline_sec=0", {"deadline_sec": 0}),
        ):
            widened = widen_budget(reason, budget)
            self.assertTrue(widened, reason)
            for name, value in widened.items():
                self.assertGreater(int(value), int(budget.get(name) or 0), reason)

    def test_a_reason_without_a_ceiling_widens_nothing(self):
        self.assertEqual(widen_budget("stagnation: the deliverable did not change", {}), {})
        self.assertEqual(widen_budget("provider error: the endpoint is not reachable", {}), {})

    def test_the_command_never_points_at_another_ticket(self):
        command = continuation_command(
            "T-9", "budget: reached max_iterations=2", {"max_iterations": 2}
        )
        self.assertIn("T-9", command)
        self.assertNotIn("T-8", command)

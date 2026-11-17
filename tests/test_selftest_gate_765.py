# -*- coding: utf-8 -*-
"""The handover gate of v0.7.65 must exist and mean something.

v0.7.65 added a seventh check to the flaky-bug gate:

* ``handover_ticket`` - a run that could not close itself must leave a
  ticket a human can actually take over, and the ticket's acceptance
  criteria must be able to close the loop again.

A gate that never turns red is decoration. These tests check that the
check is registered, runs offline, and is wired into the command.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class HandoverTicketGateTest(unittest.TestCase):
    def test_the_check_is_registered(self):
        from mas.selftest import CHECKS

        names = {getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS}
        self.assertIn("handover_ticket", names)
        self.assertEqual(len(CHECKS), 21)

    def test_the_check_passes_offline(self):
        from mas.selftest import (
            check_an_escalated_run_leaves_a_ticket_a_human_can_take_over,
        )

        row = check_an_escalated_run_leaves_a_ticket_a_human_can_take_over(iterations=2)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "handover_ticket")

    def test_the_check_states_what_it_guarantees(self):
        from mas.selftest import (
            check_an_escalated_run_leaves_a_ticket_a_human_can_take_over,
        )

        row = check_an_escalated_run_leaves_a_ticket_a_human_can_take_over(iterations=1)
        self.assertTrue(row.get("title"))
        self.assertTrue(row.get("invariant"))
        self.assertIsInstance(row.get("failures"), list)

    def test_the_check_can_be_selected_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["handover_ticket"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "handover_ticket")
        self.assertTrue(result["ok"], result)

    def test_the_check_is_slow_enough_to_be_capped(self):
        """Three checks run a whole loop per iteration - they are capped."""
        from mas.selftest import run_selftest

        result = run_selftest(iterations=50, only=["handover_ticket"])
        row = result["checks"][0]
        self.assertLessEqual(int(row["iterations"]), 20)

    def test_the_full_gate_still_passes(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["checks"]), 21)


class TicketPayloadTest(unittest.TestCase):
    """Every ticket a run leaves behind carries the same set of keys."""

    def _escalate(self):
        import tempfile

        from mas.engine import LoopEngine
        from mas.providers import Provider

        class _Stuck(Provider):
            name = "test-stuck"

            def complete(self, *, system, prompt, role="worker",
                         expect_json=False, max_tokens=4000):
                usage = {"total_tokens": 10}
                if role == "planner":
                    return {"text": json.dumps({"summary": "s", "plan": ["p"],
                                                "risks": ["r"]}),
                            "usage": usage, "raw": {}}
                if role == "verifier":
                    return {"text": json.dumps({"verdict": "fail", "scores": [
                        {"id": "c1", "pass": False, "note": "no"}]}),
                        "usage": usage, "raw": {}}
                if role == "reflector":
                    return {"text": json.dumps({"summary": "s", "issues": [],
                                                "next_action": "revise"}),
                            "usage": usage, "raw": {}}
                if role == "worker":
                    return {"text": "结论。数据是 41。", "usage": usage, "raw": {}}
                return {"text": "{}", "usage": usage, "raw": {}}

        tmp = tempfile.TemporaryDirectory(prefix="mas-ticket-payload-")
        self.addCleanup(tmp.cleanup)
        config = {
            "provider": "mock", "model": "m",
            "state_dir": str(Path(tmp.name) / "state"),
            "workspace": "",
            "budget": {"max_iterations": 6, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
            "stagnation_limit": 2,
        }
        engine = LoopEngine(config, provider=_Stuck(model="m"))
        result = engine.run(goal="写一份 Q3 报告", criteria=[
            {"id": "c1", "desc": "正文里要出现 42",
             "check": {"type": "contains", "arg": "42"}}])
        return engine, result

    def test_the_escalation_ticket_carries_the_full_context(self):
        engine, result = self._escalate()
        self.assertFalse(result.closed)
        self.assertEqual(result.status, "escalated")
        ticket = engine.tickets.get(result.ticket_id)
        payload = ticket["payload"]
        for key in ("run_id", "task_id", "goal", "title", "criteria",
                    "last_artifact", "issues", "attempts", "iterations_total",
                    "runs", "history", "stagnation", "reason", "status"):
            self.assertIn(key, payload)
        self.assertEqual(payload["status"], "escalated")

    def test_the_criteria_come_back_as_machine_checks(self):
        engine, result = self._escalate()
        payload = engine.tickets.get(result.ticket_id)["payload"]
        self.assertTrue(payload["criteria"])
        for raw in payload["criteria"]:
            self.assertIsInstance(raw.get("check"), dict)
            self.assertEqual(raw["check"].get("arg"), "42")

    def test_the_ticket_is_waiting_for_a_human(self):
        engine, result = self._escalate()
        ticket = engine.tickets.get(result.ticket_id)
        self.assertEqual(ticket["state"], "waiting_human")
        self.assertEqual(ticket["kind"], "loop_escalation")
        self.assertIn("stagnation", ticket["payload"]["reason"])


class CliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_the_json_form_lists_eighteen_checks(self):
        proc = self._run("selftest", "--iterations", "1", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload["checks"]), 21)
        self.assertTrue(payload["ok"])

    def test_it_runs_without_a_model_endpoint(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        for key in ("MAS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", "selftest", "--iterations", "1"],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""A human-written deliverable must be able to close the loop.

v0.7.67 added the handback path: a human who has already written the
answer submits it, and the *same* acceptance criteria that the loop
was held to decide it.

The properties that make it a closure rather than a rubber stamp:

* the criteria come back from the ticket, so the deliverable is judged
  by the rules the task was written with;
* a deliverable that fails does not close - the failing criteria are
  written back onto the ticket, which stays in the human queue;
* a dead reviewer does not destroy the work - the machine checks are
  already decided when the reviewer is called.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.providers import Provider, ProviderError  # noqa: E402


class _AlwaysFails(Provider):
    """The verifier always says no - the model cannot fake a closure."""

    name = "handback-test"

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


class _DeadBackend(Provider):
    """A backend that is simply not there."""

    name = "handback-test-dead"

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        raise ProviderError("the endpoint is not reachable")


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


class HandbackTest(unittest.TestCase):
    def _escalated(self, tmp, provider=None):
        engine = LoopEngine(
            _config(tmp), provider=provider or _AlwaysFails(model="test-model")
        )
        result = engine.run(goal="write a report", criteria=CRITERIA)
        self.assertFalse(result.closed)
        self.assertTrue(result.ticket_id)
        return engine, str(result.ticket_id)

    def test_a_good_deliverable_closes_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            result = engine.handback(ticket_id, "the answer is 42.", note="by hand")
            self.assertTrue(result.closed, result.error)
            self.assertEqual(result.status, "closed")

    def test_the_closure_comes_from_the_machine_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            result = engine.handback(ticket_id, "the answer is 42.")
            by_id = {str(s.get("id")): s for s in result.scores}
            self.assertEqual(by_id["c1"]["source"], "rule")
            self.assertTrue(by_id["c1"]["pass"])

    def test_the_accepted_deliverable_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            result = engine.handback(ticket_id, "the answer is 42.")
            self.assertTrue(result.artifact_path)
            self.assertTrue(Path(result.artifact_path).exists())
            self.assertIn("42", Path(result.artifact_path).read_text(encoding="utf-8"))

    def test_the_ticket_is_closed_with_the_handback_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            engine.handback(ticket_id, "the answer is 42.")
            ticket = engine.tickets.get(ticket_id)
            self.assertEqual(ticket["state"], "resolved")
            self.assertEqual(ticket["decision"], "handback")

    def test_a_bad_deliverable_does_not_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            result = engine.handback(ticket_id, "the answer is 41.")
            self.assertFalse(result.closed)
            self.assertTrue(result.issues)

    def test_a_rejected_handback_stays_in_the_human_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            engine.handback(ticket_id, "the answer is 41.")
            ticket = engine.tickets.get(ticket_id)
            self.assertEqual(ticket["state"], "waiting_human")

    def test_a_rejected_handback_records_the_failure_on_the_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            engine.handback(ticket_id, "the answer is 41.")
            ticket = engine.tickets.get(ticket_id)
            attempts = ticket["payload"].get("resume_attempts") or []
            self.assertTrue(attempts)

    def test_a_dead_reviewer_does_not_destroy_the_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(
                tmp, provider=_DeadBackend(model="test-model")
            )
            result = engine.handback(ticket_id, "the answer is 42.")
            self.assertTrue(result.closed, result.error)

    def test_the_ticket_carries_the_workspace_of_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            ticket = engine.tickets.get(ticket_id)
            self.assertTrue(ticket["payload"].get("workspace"))

    def test_the_ticket_carries_the_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            ticket = engine.tickets.get(ticket_id)
            self.assertTrue(ticket["payload"].get("criteria"))

    def test_an_unknown_ticket_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="test-model"))
            with self.assertRaises(KeyError):
                engine.handback("T-does-not-exist", "text")

    def test_an_empty_deliverable_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            with self.assertRaises(ValueError):
                engine.handback(ticket_id, "   ")

    def test_a_ticket_without_criteria_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="test-model"))
            ticket = engine.tickets.create("loop_escalation", {"goal": "x"})
            with self.assertRaises(ValueError):
                engine.handback(str(ticket["id"]), "text")


class CliTest(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_the_command_exists(self):
        proc = self._run("handback", "--help")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("handback", proc.stdout)

    def test_it_needs_a_source(self):
        proc = self._run("handback", "T-1")
        self.assertNotEqual(proc.returncode, 0)

    def test_an_unknown_ticket_is_reported(self):
        proc = self._run("handback", "T-does-not-exist", "--text", "hello")
        self.assertNotEqual(proc.returncode, 0)


class McpTest(unittest.TestCase):
    def test_the_tool_is_advertised(self):
        from mas.mcp_server import TOOLS

        names = {tool["name"] for tool in TOOLS}
        self.assertIn("mas_handback", names)

    def test_the_tool_runs(self):
        from mas.mcp_server import handle_tool

        with tempfile.TemporaryDirectory() as tmp:
            env_state = os.environ.get("MAS_STATE_DIR")
            os.environ["MAS_STATE_DIR"] = tmp
            try:
                result = handle_tool(
                    "mas_handback", {"ticket_id": "T-nope", "artifact": "x"}
                )
                self.assertIn("error", result)
            finally:
                if env_state is None:
                    os.environ.pop("MAS_STATE_DIR", None)
                else:
                    os.environ["MAS_STATE_DIR"] = env_state

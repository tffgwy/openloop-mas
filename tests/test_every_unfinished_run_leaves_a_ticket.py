# -*- coding: utf-8 -*-
"""v0.7.49 regression tests: no run may end unfinished without a ticket.

Only the "escalate" ending opened a human ticket. A run that stopped
on the iteration ceiling, that ran out of wall-clock time, that ran
out of tokens, or that died on a provider error ended with a status
and *no ticket at all*. The status page then said "未闭环 1" while
the human queue was empty: the loop reported an unfinished job and
offered nobody a way to finish it.

Measured on the live model: a run that hit ``max_iterations`` left no
ticket - the deliverable existed and nothing pointed at it.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.cli import main


def _invoke(argv, state_dir, provider="mock"):
    """Run the CLI and capture the exit code and the output."""
    os.environ["MAS_STATE_DIR"] = state_dir
    os.environ["MAS_PROVIDER"] = provider
    os.environ.pop("MAS_API_KEY", None)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class UnfinishedRunLeavesATicketTest(unittest.TestCase):
    """Every ending that is not "closed" must open a ticket."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-handover-")
        self._saved_config_path = os.environ.get("MAS_CONFIG_PATH")
        self._saved_state_dir = os.environ.get("MAS_STATE_DIR")

    def tearDown(self):
        if self._saved_config_path is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._saved_config_path
        # The state directory this module pointed at must not survive the
        # test: the next module would otherwise read a throw-away
        # directory as if it were the user's own configuration.
        if self._saved_state_dir is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state_dir
        # The state directory this module pointed at must not survive the
        # test: the next module would otherwise read a throw-away
        # directory as if it were the user's own configuration.
        if self._saved_state_dir is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state_dir

    def _config_with_budget(self, **budget):
        """Write a config for this state dir with a custom budget.

        The config is written into the state dir, never over the
        user's own ``~/.mas/config.json``: a test that rewrites the
        real config leaves the machine in a different state than it
        found it.
        """
        from mas.config import load_config

        os.environ.pop("MAS_CONFIG_PATH", None)
        config = load_config()
        config["state_dir"] = self.state_dir
        config["provider"] = "mock"
        config["model"] = "mock-1"
        config["base_url"] = ""
        config["budget"] = {
            "deadline_sec": 900,
            "max_iterations": 8,
            "max_tokens": 200000,
        }
        config["budget"].update(budget)
        path = pathlib.Path(self.state_dir) / "config.json"
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.environ["MAS_CONFIG_PATH"] = str(path)
        return config

    def _tickets(self):
        _code, out = _invoke(["tickets", "--json"], self.state_dir)
        return json.loads(out)["tickets"]

    def _run(self, goal="注定失败的任务", criteria="regex:^ZZZ_NEVER_MATCHES$"):
        return _invoke(
            ["run", "--goal", goal, "--criteria", criteria], self.state_dir
        )

    # ---------------------------------------------------------- budget
    def test_the_iteration_ceiling_leaves_a_ticket(self):
        self._config_with_budget(max_iterations=1)
        code, out = self._run()
        self.assertNotEqual(code, 0, out)
        self.assertIn("budget_exhausted", out)
        tickets = self._tickets()
        self.assertEqual(len(tickets), 1, tickets)

    def test_the_wall_clock_ceiling_leaves_a_ticket(self):
        self._config_with_budget(deadline_sec=0)
        code, out = self._run()
        self.assertNotEqual(code, 0, out)
        tickets = self._tickets()
        self.assertEqual(len(tickets), 1, tickets)

    def test_the_token_ceiling_leaves_a_ticket(self):
        self._config_with_budget(max_tokens=1)
        code, out = self._run()
        self.assertNotEqual(code, 0, out)
        tickets = self._tickets()
        self.assertEqual(len(tickets), 1, tickets)

    # ---------------------------------------------------------- errors
    def test_a_provider_error_leaves_a_ticket(self):
        # An endpoint nothing listens on: the run dies on the first
        # provider call.
        os.environ.pop("MAS_CONFIG_PATH", None)
        from mas.config import load_config

        config = load_config()
        config["state_dir"] = self.state_dir
        config["provider"] = "openai_compat"
        config["model"] = "some-model"
        config["base_url"] = "http://127.0.0.1:1/v1"
        config["budget"] = {
            "deadline_sec": 900,
            "max_iterations": 8,
            "max_tokens": 200000,
        }
        path = pathlib.Path(self.state_dir) / "config.json"
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.environ["MAS_CONFIG_PATH"] = str(path)
        code, out = _invoke(
            ["run", "--goal", "会失败的任务", "--criteria", "contains:x"],
            self.state_dir,
            provider="openai_compat",
        )
        self.assertNotEqual(code, 0, out)
        tickets = self._tickets()
        self.assertEqual(len(tickets), 1, tickets)

    # ------------------------------------------------------- the ticket
    def test_the_ticket_names_the_reason(self):
        self._config_with_budget(max_iterations=1)
        self._run()
        ticket = self._tickets()[0]
        self.assertIn("max_iterations", str(ticket["payload"]["reason"]))

    def test_the_ticket_names_the_goal(self):
        self._config_with_budget(max_iterations=1)
        self._run(goal="预算不够的任务")
        ticket = self._tickets()[0]
        self.assertEqual(ticket["payload"]["goal"], "预算不够的任务")

    def test_the_ticket_carries_the_deliverable(self):
        self._config_with_budget(max_iterations=1)
        self._run()
        ticket = self._tickets()[0]
        self.assertTrue(ticket["payload"]["last_artifact"])

    def test_the_ticket_kind_says_what_happened(self):
        self._config_with_budget(max_iterations=1)
        self._run()
        ticket = self._tickets()[0]
        self.assertEqual(ticket["kind"], "budget_exhausted")

    def test_a_provider_error_gets_its_own_kind(self):
        # "hit the ceiling" and "the backend died" call for different
        # human reactions - raise the budget versus fix the endpoint.
        # A ticket that calls both of them the same thing cannot tell
        # the human which one happened.
        os.environ.pop("MAS_CONFIG_PATH", None)
        from mas.config import load_config

        config = load_config()
        config["state_dir"] = self.state_dir
        config["provider"] = "openai_compat"
        config["model"] = "some-model"
        config["base_url"] = "http://127.0.0.1:1/v1"
        config["budget"] = {
            "deadline_sec": 900,
            "max_iterations": 8,
            "max_tokens": 200000,
        }
        path = pathlib.Path(self.state_dir) / "config.json"
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.environ["MAS_CONFIG_PATH"] = str(path)
        _invoke(
            ["run", "--goal", "会失败的任务", "--criteria", "contains:x"],
            self.state_dir,
            provider="openai_compat",
        )
        ticket = self._tickets()[0]
        self.assertEqual(ticket["kind"], "run_error")

    def test_the_ticket_is_listed_for_a_human(self):
        self._config_with_budget(max_iterations=1)
        self._run()
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("待处理工单：1 个", out)
        self.assertIn("接着干：mas resume T-", out)

    def test_the_status_page_counts_the_ticket(self):
        self._config_with_budget(max_iterations=1)
        self._run()
        _code, out = _invoke(["status"], self.state_dir)
        self.assertIn("待处理工单：1 个", out)
        self.assertIn("未闭环 1", out)

    # -------------------------------------------------- no double ticket
    def test_a_closed_run_leaves_no_ticket(self):
        self._config_with_budget()
        code, out = self._run(
            goal="会成功的任务", criteria="contains:交付物"
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(self._tickets(), [])

    def test_a_resume_does_not_open_a_second_ticket(self):
        self._config_with_budget()
        self._run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        tickets = self._tickets()
        self.assertEqual(len(tickets), 1, tickets)
        self.assertEqual(tickets[0]["id"], ticket_id)

    def test_the_ledger_books_the_ticket(self):
        self._config_with_budget(max_iterations=1)
        self._run()
        ticket_id = self._tickets()[0]["id"]
        _code, out = _invoke(["ledger", "--json"], self.state_dir)
        rows = json.loads(out)["entries"]
        ends = [r for r in rows if r.get("role") == "run_end"]
        self.assertTrue(ends)
        self.assertEqual(ends[-1].get("ticket_id"), ticket_id)


if __name__ == "__main__":
    unittest.main()

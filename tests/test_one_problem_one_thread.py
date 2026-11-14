# -*- coding: utf-8 -*-
"""v0.7.50 regression tests: one problem, one thread.

The ledger counts *runs*; the human queue counts *problems*. A run
that escalates opens a ticket, and every resume on that ticket makes
another run on the same problem. The status page and the report then
counted the runs, so a single unresolved problem that was resumed
three times showed up as "未闭环 4" next to a queue of one ticket. The
report and the queue disagreed about how much work was left, and the
ticket itself said "已尝试 5 轮" when fifteen rounds had actually been
spent on it.

Measured on the live ledger: after one resume the status page said
"未闭环 2" while the ticket queue held exactly one ticket, and the
ticket reported only the last attempt's round count.
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


class OneProblemOneThreadTest(unittest.TestCase):
    """A problem and its attempts must be told apart."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-thread-")
        self._saved_config_path = os.environ.get("MAS_CONFIG_PATH")

    def tearDown(self):
        if self._saved_config_path is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._saved_config_path

    def _config_with_budget(self, **budget):
        """Write an isolated config for this state dir."""
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

    def _status_json(self):
        _code, out = _invoke(["status", "--json"], self.state_dir)
        return json.loads(out)

    def _report_json(self):
        _code, out = _invoke(["report", "--json"], self.state_dir)
        return json.loads(out)

    def _failing_run(self, goal="注定失败的任务"):
        return _invoke(
            [
                "run",
                "--goal",
                goal,
                "--criteria",
                "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
            ],
            self.state_dir,
        )

    # ------------------------------------------------------ the problem
    def test_one_problem_is_one_problem_after_a_resume(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        status = self._status_json()
        self.assertEqual(status["runs"]["total"], 2, status["runs"])
        # Two attempts, one problem.
        self.assertEqual(status["problems"]["total"], 1, status["problems"])
        self.assertEqual(status["problems"]["open"], 1, status["problems"])

    def test_the_report_counts_problems_separately_from_runs(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        report = self._report_json()
        totals = report["totals"]
        self.assertEqual(totals["runs"], 2, totals)
        self.assertEqual(totals["problems"], 1, totals)
        self.assertEqual(totals["problems_open"], 1, totals)

    def test_the_status_page_shows_the_problem_count(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["status"], self.state_dir)
        self.assertIn("待解决问题：1 个", out)

    def test_the_status_page_shows_the_problem_count_in_json(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        status = self._status_json()
        self.assertEqual(status["problems"]["open"], 1, status["problems"])
        self.assertEqual(status["problems"]["total"], 1, status["problems"])

    def test_the_report_knows_the_threads(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        report = self._report_json()
        threads = report["threads"]
        self.assertEqual(len(threads), 1, threads)
        self.assertEqual(threads[0]["run_count"], 2, threads[0])
        self.assertFalse(threads[0]["closed"], threads[0])

    def test_a_closed_run_is_its_own_thread(self):
        self._config_with_budget()
        code, out = _invoke(
            [
                "run",
                "--goal",
                "会成功的任务",
                "--criteria",
                "contains:交付物",
            ],
            self.state_dir,
        )
        self.assertEqual(code, 0, out)
        report = self._report_json()
        # A run that closed on its own never became anybody's problem:
        # it is a thread of one.
        self.assertEqual(report["totals"]["problems"], report["totals"]["runs"])
        self.assertEqual(report["totals"]["problems_open"], 0)

    def test_two_separate_problems_are_two_threads(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run(goal="问题甲")
        self._failing_run(goal="问题乙")
        report = self._report_json()
        self.assertEqual(report["totals"]["problems"], 2, report["totals"])
        self.assertEqual(report["totals"]["problems_open"], 2, report["totals"])

    # -------------------------------------------------- the ticket cost
    def test_the_ticket_counts_the_rounds_of_every_attempt(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        first = self._tickets()[0]["payload"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        payload = self._tickets()[0]["payload"]
        self.assertEqual(
            payload["iterations_total"],
            int(first["attempts"]) + int(payload["attempts"]),
            payload,
        )

    def test_the_first_ticket_already_knows_its_run(self):
        # The very first escalation must record the run it belongs to.
        # A ticket that starts life without its run id makes the thread
        # look one attempt shorter than it is, and the cumulative round
        # count comes out short by a whole attempt.
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        payload = self._tickets()[0]["payload"]
        self.assertTrue(payload.get("runs"), payload)
        self.assertEqual(payload["runs"][0], payload["run_id"], payload)
        self.assertEqual(payload.get("iterations_total"), payload["attempts"], payload)

    def test_an_escalated_run_also_joins_its_own_thread(self):
        # The stagnation escalation opens the ticket through a
        # different code path than the budget ceiling. A path that
        # forgets to record the run leaves the thread one attempt
        # short, and the cumulative round count comes out wrong.
        self._config_with_budget(max_iterations=8)
        code, out = _invoke(
            [
                "run",
                "--goal",
                "会停滞的任务",
                "--criteria",
                "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
            ],
            self.state_dir,
        )
        self.assertNotEqual(code, 0, out)
        tickets = self._tickets()
        self.assertEqual(len(tickets), 1, tickets)
        payload = tickets[0]["payload"]
        self.assertEqual(tickets[0]["kind"], "loop_escalation", tickets[0])
        self.assertTrue(payload.get("runs"), payload)
        self.assertEqual(payload["runs"][0], payload["run_id"], payload)
        self.assertEqual(payload.get("iterations_total"), payload["attempts"], payload)

    def test_a_legacy_ticket_without_a_run_list_still_joins_up(self):
        # Tickets written before the thread bookkeeping existed carry
        # only a run_id. On resume they must still be joined to their
        # first run, or the thread silently loses an attempt.
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        ticket_path = pathlib.Path(self.state_dir) / "tickets" / f"{ticket_id}.json"
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        payload = ticket["payload"]
        payload.pop("runs", None)
        payload.pop("iterations_total", None)
        ticket_path.write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        payload = self._tickets()[0]["payload"]
        self.assertEqual(len(payload["runs"]), 2, payload["runs"])

    def test_the_ticket_lists_every_run_on_the_problem(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        payload = self._tickets()[0]["payload"]
        self.assertEqual(len(payload["runs"]), 2, payload["runs"])

    def test_the_ticket_page_shows_the_cumulative_rounds(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("累计已烧", out)
        self.assertIn("关联运行：2 次尝试", out)

    def test_a_closing_resume_names_the_run_that_closed_it(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        # Swap the criterion of the stored ticket for one the mock
        # backend can satisfy, so the resumed run closes the loop.
        tickets_dir = pathlib.Path(self.state_dir) / "tickets"
        ticket_path = tickets_dir / f"{ticket_id}.json"
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        ticket["payload"]["criteria"] = [
            {
                "id": "c1",
                "desc": "contains:交付物",
                "check": {"type": "contains", "arg": {"value": "交付物"}},
                "required": True,
            }
        ]
        ticket_path.write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, out = _invoke(["resume", ticket_id, "--note", "收尾"], self.state_dir)
        self.assertEqual(code, 0, out)
        resolved = [
            t for t in self._tickets() if t["id"] == ticket_id
        ][0]
        self.assertEqual(resolved["state"], "resolved", resolved)
        payload = resolved["payload"]
        self.assertTrue(payload.get("closed_run_id"), payload)
        self.assertIn(payload["closed_run_id"], payload["runs"], payload)

    # -------------------------------------------- the ledger and the join
    def test_the_ledger_records_the_thread_of_a_resumed_run(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["ledger", "--json"], self.state_dir)
        rows = json.loads(out)["entries"]
        ends = [r for r in rows if r.get("role") == "run_end"]
        self.assertEqual(len(ends), 2, ends)
        self.assertEqual(ends[-1].get("parent_ticket_id"), ticket_id, ends[-1])

    def test_a_closing_resume_is_booked_on_the_ticket(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        # The mock backend answers a contains-check by echoing it, so a
        # resumed run with a satisfiable criterion closes the loop.
        os.environ.pop("MAS_CONFIG_PATH", None)
        from mas.config import load_config

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
        path = pathlib.Path(self.state_dir) / "config.json"
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.environ["MAS_CONFIG_PATH"] = str(path)
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn(ticket_id, out)


class ThreadCountingTest(unittest.TestCase):
    """The report helpers themselves, without the CLI around them."""

    def test_thread_of_uses_the_ticket_when_there_is_one(self):
        from mas.report import thread_of

        self.assertEqual(
            thread_of({"ticket_id": "T-1", "run_id": "R-1"}), "T-1"
        )
        self.assertEqual(thread_of({"ticket_id": "", "run_id": "R-1"}), "R-1")

    def test_group_threads_keeps_the_order_of_first_appearance(self):
        from mas.report import group_threads

        threads = group_threads(
            [
                {"run_id": "R-1", "ticket_id": "T-1", "iterations": 2},
                {"run_id": "R-2", "ticket_id": "", "iterations": 1},
                {"run_id": "R-3", "ticket_id": "T-1", "iterations": 3},
            ]
        )
        self.assertEqual([t["thread_id"] for t in threads], ["T-1", "R-2"])
        self.assertEqual(threads[0]["run_count"], 2)
        self.assertEqual(threads[0]["iterations"], 5)

    def test_a_thread_is_closed_only_when_all_its_runs_closed(self):
        from mas.report import group_threads

        threads = group_threads(
            [
                {"run_id": "R-1", "ticket_id": "T-1", "closed": True},
                {"run_id": "R-2", "ticket_id": "T-1", "closed": False},
            ]
        )
        self.assertFalse(threads[0]["closed"])

    def test_a_resumed_run_joins_the_thread_of_its_ticket(self):
        from mas.report import group_threads

        threads = group_threads(
            [
                {"run_id": "R-1", "ticket_id": "T-1", "closed": False},
                {"run_id": "R-2", "ticket_id": "T-1", "closed": False},
            ]
        )
        self.assertEqual(len(threads), 1, threads)
        self.assertEqual(threads[0]["runs"], ["R-1", "R-2"])


if __name__ == "__main__":
    unittest.main()

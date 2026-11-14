# -*- coding: utf-8 -*-
"""v0.7.51 regression tests: the replay must say what a person picks up.

``mas replay`` printed one flat list of ledger rows. Twenty rows for
two attempts on one problem read exactly like twenty rows for two
problems - the reader could not tell "the loop tried this twice" from
"the loop handled two things". And the replay stopped at the last line
of the trace: an escalated run replayed fine, showed what went wrong,
and never said where to pick it up. The report closed that gap in
v0.7.49; the replay left it open.

Measured on the live ledger: after one resume the replay printed a
single flat block of twenty rows, with no grouping and no hint that a
ticket was waiting.
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


class ReplayNamesTheProblemTest(unittest.TestCase):
    """The replay has to say which attempts belong to which problem."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-replay-thread-")
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

    def _failing_run(self, goal="注定失败的问题"):
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

    # ------------------------------------------------------ the grouping
    def test_the_replay_groups_the_steps_by_run(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["replay"], self.state_dir)
        self.assertIn("── 运行 R-", out)

    def test_the_replay_names_the_task_of_each_run(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["replay"], self.state_dir)
        self.assertIn("任务：注定失败的问题", out)

    def test_the_replay_says_how_many_attempts_one_problem_took(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["replay"], self.state_dir)
        self.assertIn("按问题分组", out)
        self.assertIn("2 次尝试", out)

    def test_the_replay_reports_the_open_problem(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["replay"], self.state_dir)
        self.assertIn("待处理", out)

    # ------------------------------------------------- the handover hint
    def test_the_replay_gives_the_command_to_pick_the_run_up(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _code, out = _invoke(["replay"], self.state_dir)
        self.assertIn(f"接着干：mas resume {ticket_id}", out)

    def test_a_closed_run_gets_no_handover_hint(self):
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
        _code, rp = _invoke(["replay"], self.state_dir)
        self.assertNotIn("接着干：mas resume", rp)

    # ------------------------------------------------------ the json side
    def test_the_replay_json_carries_the_runs(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["replay", "--json"], self.state_dir)
        trace = json.loads(out)
        self.assertEqual(len(trace["runs"]), 1, trace["runs"])
        self.assertEqual(trace["runs"][0]["goal"], "注定失败的问题", trace["runs"])
        self.assertTrue(trace["runs"][0]["ticket_id"], trace["runs"])

    def test_the_replay_json_carries_the_threads(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        ticket_id = self._tickets()[0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["replay", "--json"], self.state_dir)
        trace = json.loads(out)
        self.assertEqual(len(trace["threads"]), 1, trace["threads"])
        self.assertEqual(trace["threads"][0]["run_count"], 2, trace["threads"])
        self.assertEqual(trace["problems_open"], 1, trace)

    def test_the_replay_json_steps_carry_their_run(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["replay", "--json"], self.state_dir)
        trace = json.loads(out)
        for step in trace["steps"]:
            self.assertTrue(step.get("run_id"), step)


class ReportThreadTableTest(unittest.TestCase):
    """The report must lead with the problems, not the attempts."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-report-thread-")
        self._saved_config_path = os.environ.get("MAS_CONFIG_PATH")

    def tearDown(self):
        if self._saved_config_path is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._saved_config_path

    def _config_with_budget(self, **budget):
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

    def _failing_run(self, goal="注定失败的问题"):
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

    def test_the_report_leads_with_the_problem_table(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["report"], self.state_dir)
        self.assertIn("## 问题概览", out)
        self.assertIn("## 运行概览", out)
        # The problem table has to come first, or the reader meets the
        # attempt table before the one that answers "how much is left".
        self.assertLess(out.index("## 问题概览"), out.index("## 运行概览"))

    def test_the_problem_table_counts_the_attempts(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["tickets", "--json"], self.state_dir)
        ticket_id = json.loads(out)["tickets"][0]["id"]
        _invoke(["resume", ticket_id, "--note", "再试一次"], self.state_dir)
        _code, report = _invoke(["report"], self.state_dir)
        self.assertIn("| 注定失败的问题 | 待处理 | 2 |", report)

    def test_the_problem_table_names_the_ticket(self):
        self._config_with_budget(max_iterations=2)
        self._failing_run()
        _code, out = _invoke(["tickets", "--json"], self.state_dir)
        ticket_id = json.loads(out)["tickets"][0]["id"]
        _code, report = _invoke(["report"], self.state_dir)
        self.assertIn(f"`{ticket_id}`", report)

    def test_a_closed_problem_is_marked_solved(self):
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
        _code, report = _invoke(["report"], self.state_dir)
        self.assertIn("## 问题概览", report)
        self.assertIn("已解决", report)


class ReplayTraceShapeTest(unittest.TestCase):
    """The trace helpers themselves."""

    def test_the_trace_groups_the_steps_by_run(self):
        from mas.report import replay_trace

        rows = [
            {"seq": 1, "run_id": "R-1", "role": "planner", "detail": "x"},
            {"seq": 2, "run_id": "R-1", "role": "run_end", "closed": True},
            {"seq": 3, "run_id": "R-2", "role": "run_end", "closed": False},
        ]
        trace = replay_trace(rows)
        self.assertEqual(len(trace["runs"]), 2, trace["runs"])
        self.assertEqual(trace["runs"][0]["run_id"], "R-1")
        self.assertEqual(trace["runs"][1]["run_id"], "R-2")


if __name__ == "__main__":
    unittest.main()

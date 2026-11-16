# -*- coding: utf-8 -*-
"""v0.7.46 regression tests: the ledger must know what each run was ABOUT.

The gap was measured on the live model, not guessed: with a real
provider the ledger contained no trace of the task. A replay could say
"a run closed, 241 characters, sha fe8848013b" and the reader had no
way to learn whether it was the onboarding guide or the Q3 report. The
mock backend hid this, because its planner summary happens to echo the
goal - so the gap only shows up against a real model.
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
import re
import tempfile
import unittest

from mas.cli import main
from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.report import build_report, render_markdown, run_summary


def _run_cli(argv, state_dir):
    os.environ["MAS_STATE_DIR"] = state_dir
    os.environ["MAS_PROVIDER"] = "mock"
    os.environ.pop("MAS_API_KEY", None)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class LedgerRemembersTheTaskTest(unittest.TestCase):
    """The run's end entry has to name the task."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-task-")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        self._saved_provider = os.environ.get("MAS_PROVIDER")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        if self._saved_provider is None:
            os.environ.pop("MAS_PROVIDER", None)
        else:
            os.environ["MAS_PROVIDER"] = self._saved_provider

    def _engine(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["state_dir"] = self.state_dir
        return LoopEngine(cfg)

    def test_the_end_entry_books_the_goal(self):
        engine = self._engine()
        engine.run("给新用户写一份上手指南",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}])
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][0]
        self.assertEqual(end.get("goal"), "给新用户写一份上手指南")

    def test_the_end_entry_books_the_title(self):
        engine = self._engine()
        engine.run("写一份周报",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}],
                   title="第 12 周周报")
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][0]
        self.assertEqual(end.get("title"), "第 12 周周报")

    def test_the_title_falls_back_to_the_goal(self):
        engine = self._engine()
        engine.run("写一份周报",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}])
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][0]
        self.assertEqual(end.get("title"), "写一份周报")

    def test_the_summary_carries_the_task(self):
        engine = self._engine()
        result = engine.run(
            "给新用户写一份上手指南",
            [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}],
        )
        summary = run_summary(result.run_id, engine.ledger.entries())
        self.assertEqual(summary["goal"], "给新用户写一份上手指南")
        self.assertEqual(summary["title"], "给新用户写一份上手指南")

    def test_the_report_names_the_task_in_the_overview(self):
        engine = self._engine()
        engine.run("给新用户写一份上手指南",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}])
        markdown = render_markdown(build_report(engine.ledger.entries()))
        self.assertIn("给新用户写一份上手指南", markdown)
        # The overview table has to carry it too, not only the
        # per-run section.
        overview = markdown.split("## 运行概览", 1)[1].split("## 运行 `", 1)[0]
        self.assertIn("给新用户写一份上手指南", overview)

    def test_the_report_names_the_task_in_the_run_section(self):
        engine = self._engine()
        engine.run("给新用户写一份上手指南",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}])
        markdown = render_markdown(build_report(engine.ledger.entries()))
        self.assertIn("- 任务：给新用户写一份上手指南", markdown)

    def test_the_overview_table_has_a_task_column(self):
        engine = self._engine()
        engine.run("写一份周报",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}])
        markdown = render_markdown(build_report(engine.ledger.entries()))
        header = [ln for ln in markdown.splitlines() if ln.startswith("| 运行 ID")][0]
        self.assertIn("任务", header)

    def test_a_pipe_in_the_goal_does_not_break_the_table(self):
        engine = self._engine()
        engine.run("写 A | B",
                   [{"id": "c1", "desc": "d", "check": {"type": "contains", "arg": "交付物"}}])
        markdown = render_markdown(build_report(engine.ledger.entries()))
        overview = markdown.split("## 运行概览", 1)[1].split("## 运行 `", 1)[0]
        table_rows = [ln for ln in overview.splitlines() if ln.startswith("| `R-")]
        self.assertEqual(len(table_rows), 1)
        # The goal is present and the cell count is unchanged. A pipe
        # inside the goal has to be escaped, otherwise it splits the
        # row and the table renders wrong.
        self.assertIn("写 A", table_rows[0])
        self.assertIn("\\|", table_rows[0])
        cells = re.split(r"(?<!\\)\|", table_rows[0])
        # 10 cells + 2 empty edges. The table grew a "source" column,
        # which says whether the answer came from the loop or from a
        # person.
        self.assertEqual(len(cells), 12)

    def test_the_artifacts_listing_names_the_task(self):
        code, out = _run_cli(
            ["run", "--goal", "给新用户写一份上手指南",
             "--criteria", "contains:交付物"],
            self.state_dir,
        )
        self.assertEqual(code, 0, out)
        code, out = _run_cli(["artifacts"], self.state_dir)
        self.assertEqual(code, 0, out)
        self.assertIn("给新用户写一份上手指南", out)

    def test_the_artifacts_json_carries_the_task(self):
        code, out = _run_cli(
            ["run", "--goal", "给新用户写一份上手指南",
             "--criteria", "contains:交付物", "--json"],
            self.state_dir,
        )
        self.assertEqual(code, 0, out)
        code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        row = json.loads(out)["artifacts"][0]
        self.assertEqual(row["goal"], "给新用户写一份上手指南")
        self.assertEqual(row["title"], "给新用户写一份上手指南")

    def test_two_runs_are_told_apart_by_their_task(self):
        _run_cli(["run", "--goal", "给新用户写一份上手指南",
                  "--criteria", "contains:交付物"], self.state_dir)
        _run_cli(["run", "--goal", "做一份第 12 周周报",
                  "--criteria", "contains:交付物"], self.state_dir)
        code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        self.assertEqual(code, 0, out)
        goals = {row["goal"] for row in json.loads(out)["artifacts"]}
        self.assertEqual(
            goals, {"给新用户写一份上手指南", "做一份第 12 周周报"}
        )


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""v0.8.9: a run knows its own approval was taken back.

The regression this file locks down is the one v0.8.9 fixed. v0.8.8
hung every human decision on the run that proposed it, so the *ledger*
could be asked "which of my runs once had a wrong approval?" - but the
**run** could not answer it.

``group_runs()`` deliberately leaves the decision rows out of the run
rows (a human decision about a draft is not a round of the loop, and
folding it in would invent a run with no goal and no closure). That
reasoning is right about the grouping and left a hole in the reading:
the run summary said "closed", counted five entries and had no word
for the fact that one of its own approvals had been taken back. The
reader who opened the one run that had been wrong was shown a clean,
successful run.

What has to hold after this release:

* the decision rows are still **not** run rows - the run count does not
  grow because a person decided something;
* they are filed beside the run that proposed them;
* the run summary knows how many of its approvals were taken back;
* the report's run table shows the number;
* the run's own detail section says it in words;
* the other run - the one that was never wrong - sees nothing;
* the command line and the MCP surface agree with the report.
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.drafts import approve_draft, stage_draft, undo_draft
from mas.report import (
    DRAFT_DECISION_ROLES,
    decisions_of_run,
    group_runs,
    run_summaries,
    run_summary,
)


class _Env:
    def __init__(self, state_dir, config_path):
        self._values = {"MAS_STATE_DIR": state_dir, "MAS_CONFIG_PATH": config_path}
        self._saved = {}

    def __enter__(self):
        for name, value in self._values.items():
            self._saved[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *exc):
        for name, saved in self._saved.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


def _decision_row(run_id, draft_id, role):
    return {
        "role": role,
        "event": role,
        "run_id": run_id,
        "task_id": "T-" + run_id,
        "draft_id": draft_id,
        "path": "note.txt",
        "workspace": "ws",
        "seq": 1 if role == "draft_approve" else 2,
        "reverted_line_count": 3 if role == "draft_undo" else None,
    }


class TheGroupingTest(unittest.TestCase):
    """The decisions are filed beside the run, not inside it."""

    def setUp(self):
        self.rows = [
            {"role": "planner", "run_id": "R-a"},
            {"role": "run_end", "run_id": "R-a"},
            {"role": "planner", "run_id": "R-b"},
            {"role": "run_end", "run_id": "R-b"},
            _decision_row("R-a", "D-1", "draft_approve"),
            _decision_row("R-a", "D-1", "draft_undo"),
            _decision_row("R-b", "D-2", "draft_approve"),
            _decision_row("R-b", "D-2", "draft_undo"),
        ]

    def test_a_decision_is_not_a_run(self):
        # The old rule, kept: folding a decision into a run would
        # invent a run with no goal and no closure.
        groups = group_runs(self.rows)
        self.assertEqual(len(groups), 2)
        for group in groups:
            for row in group["rows"]:
                self.assertNotIn(str(row.get("role") or ""), DRAFT_DECISION_ROLES)

    def test_the_decisions_are_filed_beside_their_run(self):
        groups = {group["run_id"]: group for group in group_runs(self.rows)}
        self.assertEqual(len(groups["R-a"].get("decisions") or []), 2)
        self.assertEqual(len(groups["R-b"].get("decisions") or []), 2)
        for row in groups["R-a"]["decisions"]:
            self.assertEqual(row.get("run_id"), "R-a")

    def test_a_decision_without_a_thread_belongs_to_no_run(self):
        rows = [
            *self.rows,
            _decision_row("", "D-3", "draft_approve"),
            _decision_row("", "D-3", "draft_undo"),
        ]
        groups = group_runs(rows)
        self.assertEqual(len(groups), 2)
        for group in groups:
            for row in group.get("decisions") or []:
                self.assertNotEqual(str(row.get("draft_id") or ""), "D-3")

    def test_a_decision_is_not_spread_over_every_run(self):
        # It would make each run claim a decision that was never its
        # own - and that is a different kind of lie.
        groups = {group["run_id"]: group for group in group_runs(self.rows)}
        self.assertEqual(len(groups["R-a"].get("decisions") or []), 2)
        self.assertEqual(len(groups["R-b"].get("decisions") or []), 2)


class TheRunSummaryTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"role": "planner", "run_id": "R-a", "goal": "a"},
            {"role": "run_end", "run_id": "R-a"},
            {"role": "planner", "run_id": "R-b", "goal": "b"},
            {"role": "run_end", "run_id": "R-b"},
            _decision_row("R-a", "D-1", "draft_approve"),
            _decision_row("R-a", "D-1", "draft_undo"),
        ]

    def test_the_run_knows_its_own_taken_back_approval(self):
        summaries = {s["run_id"]: s for s in run_summaries(self.rows)}
        self.assertEqual(summaries["R-a"].get("taken_back_approvals"), 1)
        self.assertEqual(summaries["R-b"].get("taken_back_approvals"), 0)

    def test_the_decisions_carry_the_drafts(self):
        summaries = {s["run_id"]: s for s in run_summaries(self.rows)}
        decisions = summaries["R-a"].get("decisions") or {}
        self.assertIn("D-1", decisions.get("draft_ids") or [])
        self.assertEqual(decisions.get("reverted_lines"), 3)

    def test_the_summary_still_works_without_decisions(self):
        # The old call signature has to keep working - other callers
        # read a run's rows alone.
        summary = run_summary("R-a", [r for r in self.rows if r["run_id"] == "R-a"])
        self.assertEqual(summary.get("taken_back_approvals"), 0)
        self.assertEqual((summary.get("decisions") or {}).get("taken_back"), 0)

    def test_the_decisions_of_a_run_are_asked_for_by_name(self):
        groups = {group["run_id"]: group for group in group_runs(self.rows)}
        decisions = decisions_of_run(groups["R-a"].get("decisions") or [], "R-a")
        self.assertEqual(decisions.get("taken_back"), 1)
        other = decisions_of_run(groups["R-a"].get("decisions") or [], "R-b")
        self.assertEqual(other.get("taken_back"), 0)


class _TwoRunsWithARealLoop(unittest.TestCase):
    """Two closed runs in one ledger; one of them got a wrong approval."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-test-knows-")
        root = pathlib.Path(self._tmp.name)
        self.state = root / "state"
        self.artifacts = self.state / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.config_path = root / "config.json"
        self.workspace = self.artifacts / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "note.txt").write_text("final answer line\n", encoding="utf-8")

        from mas.engine import LoopEngine
        from mas.providers import Provider

        class _Offline(Provider):
            name = "test-knows"

            def __init__(self):
                super().__init__(model="test-model")

            def complete(self, *, system="", prompt="", role="worker",
                         expect_json=False, max_tokens=4000):
                usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
                if role == "worker":
                    return {"text": "final answer line\n", "usage": usage, "raw": {}}
                return {
                    "text": json.dumps({
                        "verdict": "pass",
                        "scores": [{"id": "c1", "pass": True, "note": "ok"}],
                        "issues": [], "summary": "s", "plan": ["p"], "risks": [],
                        "decision": "approve", "reason": "ok",
                    }),
                    "usage": usage, "raw": {},
                }

        config = {
            "provider": "mock", "model": "test-model",
            "state_dir": str(self.state), "workspace": str(self.workspace),
            "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
        }
        self.run_ids = []
        with _Env(str(self.state), str(self.config_path)):
            engine = LoopEngine(config, provider=_Offline())
            criteria = [{
                "id": "c1", "desc": "final",
                "check": {"type": "contains", "arg": "final answer"},
            }]
            for goal in ("写一份说明", "再写一份说明"):
                result = engine.run(goal=goal, criteria=criteria).to_dict()
                self.run_ids.append(str(result.get("run_id") or ""))

        self.mine = self.run_ids[0]
        self.other = self.run_ids[1]

        with _Env(str(self.state), str(self.config_path)):
            staged = stage_draft(
                self.workspace, "note.txt",
                b"final answer line\nreplaced by a person\n",
                reason="the loop proposes a change", source="test",
                run_id=self.mine, task_id="T-" + self.mine,
            )
            self.draft_id = str(staged.get("id") or "")
            approve_draft(self.workspace, self.draft_id, note="looks fine")
            undo_draft(self.workspace, self.draft_id, note="approved the wrong thing")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, args):
        from mas.cli import main

        buffer = io.StringIO()
        with _Env(str(self.state), str(self.config_path)):
            try:
                with contextlib.redirect_stdout(buffer):
                    main(args)
            except SystemExit:
                pass
        return buffer.getvalue()

    def _json(self, args):
        text = self._run(args)
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - a broken page is the subject
            return {}

    def _section(self, markdown, run_id):
        kept = []
        inside = False
        for line in (markdown or "").split("\n"):
            if line.startswith("## "):
                inside = ("`%s`" % run_id) in line
                continue
            if inside:
                kept.append(line)
        return "\n".join(kept)


class TheReportTest(_TwoRunsWithARealLoop):
    def test_the_run_table_shows_the_number(self):
        payload = self._json(["report", "--json"])
        rows = {r.get("run_id"): r for r in (payload.get("runs") or [])}
        self.assertEqual(rows[self.mine].get("taken_back_approvals"), 1)
        self.assertEqual(rows[self.other].get("taken_back_approvals"), 0)

    def test_the_run_detail_says_it_in_words(self):
        markdown = self._run(["report"])
        section = self._section(markdown, self.mine)
        self.assertIn("人工决定", section)
        self.assertIn("撤回", section)
        self.assertIn(self.draft_id, section)

    def test_the_other_run_says_nothing(self):
        markdown = self._run(["report"])
        section = self._section(markdown, self.other)
        self.assertNotIn("人工决定", section)

    def test_the_command_line_agrees(self):
        payload = self._json(["report", "--json"])
        rows = {r.get("run_id"): r for r in (payload.get("runs") or [])}
        self.assertEqual(rows[self.mine].get("taken_back_approvals"), 1)

    def test_the_mcp_surface_agrees(self):
        from mas.mcp_server import handle_tool

        # The MCP surface reads the machine's own state directory, so
        # it is asked inside the environment that points at this
        # test's ledger - otherwise it would answer about the real
        # machine and the assertion would be about the wrong thing.
        with _Env(str(self.state), str(self.config_path)):
            report = handle_tool("mas_report", {"as_markdown": False})
            rows = {r.get("run_id"): r for r in (report.get("runs") or [])}
            self.assertEqual(rows[self.mine].get("taken_back_approvals"), 1)

            replay = handle_tool("mas_replay", {})
            blocks = {b.get("run_id"): b for b in (replay.get("runs") or [])}
            self.assertEqual(blocks[self.mine].get("taken_back_approvals"), 1)
            self.assertEqual(blocks[self.other].get("taken_back_approvals"), 0)


class TheGateTest(unittest.TestCase):
    def test_the_gate_is_registered(self):
        from mas.selftest import CHECKS

        names = [getattr(check, "CHECK_NAME", "") for check in CHECKS]
        self.assertIn("a_run_knows_its_approval", names)

    def test_the_gate_passes(self):
        from mas.selftest_run_knows_approval import (
            check_a_run_knows_its_approval,
        )

        result = check_a_run_knows_its_approval(iterations=1)
        self.assertTrue(result.get("ok"), result.get("failures"))
        self.assertEqual(result.get("failures"), [])


if __name__ == "__main__":
    unittest.main()

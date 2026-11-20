# -*- coding: utf-8 -*-
"""v0.7.97: somebody has to say whether the work is closed.

The new capability
------------------
``mas closure`` (CLI) and ``mas_closure`` (MCP) answer the question
this project exists to answer: is the work closed, and if not, which
part of it is still open?

Both surfaces read the ledger, the ticket queue and the draft queue,
and neither of them runs a new loop.
"""

import os
import shutil
import sys
import tempfile
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


from mas.report import (
    build_report,
    closure_verdict,
    closure_verdict_for,
    format_closure,
)


def _rows(status="closed", ticket_id="", closed=True):
    return [
        {
            "role": "closure",
            "run_id": "R-testclo",
            "task_id": "T-testclo",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
            "verdict": "pass" if closed else "fail",
            "decision": "accept" if closed else "escalate",
            "scores": [{"id": "c1", "pass": closed}],
        },
        {
            "role": "run_end",
            "run_id": "R-testclo",
            "task_id": "T-testclo",
            "iteration": 1,
            "ts": "2026-01-01T00:00:01+00:00",
            "seq": 2,
            "status": status,
            "closed": closed,
            "deliverable_ok": closed,
            "decision": "accept" if closed else "escalate",
            "error": "",
            "ticket_id": ticket_id,
            "goal": "写一份结论",
            "title": "写一份结论",
            "iterations": 1,
            "tokens": 100,
            "artifact_path": "",
            "files": [],
        },
    ]


def _ticket(state="waiting_human"):
    return {
        "id": "T-testclo",
        "kind": "closure_failed",
        "state": state,
        "created_at": "2026-01-01T00:00:02+00:00",
        "resolved_at": None,
        "decision": None,
        "note": "",
        "payload": {"run_id": "R-testclo"},
    }


def _draft():
    return {
        "id": "D-testclo",
        "state": "pending",
        "path": "out.md",
        "workspace": "/tmp/ws",
        "reason": "probe",
        "created_at": "2026-01-01T00:00:03+00:00",
        "resolved_at": None,
    }


class ClosureVerdictTest(unittest.TestCase):
    def test_a_closed_run_with_nothing_waiting_is_closed(self):
        verdict = closure_verdict_for(_rows(), [], [])
        self.assertTrue(verdict["ok"])
        self.assertFalse(verdict["empty"])
        self.assertEqual(verdict["reasons"], [])
        self.assertEqual(verdict["counts"]["closed"], 1)

    def test_an_empty_ledger_is_not_a_closed_loop(self):
        verdict = closure_verdict_for([], [], [])
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["empty"])
        self.assertIn("还没有任何运行记录", verdict["headline"])

    def test_a_run_that_escalated_is_not_closed_and_says_why(self):
        verdict = closure_verdict_for(
            _rows(status="escalated", ticket_id="T-testclo", closed=False),
            [_ticket()],
            [],
        )
        self.assertFalse(verdict["ok"])
        keys = {r["reason"] for r in verdict["reasons"]}
        self.assertIn("runs_not_closed", keys)
        self.assertIn("tickets_waiting", keys)
        for reason in verdict["reasons"]:
            self.assertTrue(reason["detail"])
            self.assertIn(reason["level"], ("fail", "warn"))

    def test_a_resolved_ticket_does_not_keep_the_work_open(self):
        verdict = closure_verdict_for(
            _rows(status="escalated", ticket_id="T-testclo", closed=False),
            [_ticket(state="resolved")],
            [],
        )
        keys = {r["reason"] for r in verdict["reasons"]}
        self.assertNotIn("tickets_waiting", keys)

    def test_a_pending_draft_keeps_the_work_open(self):
        verdict = closure_verdict_for(_rows(), [], [_draft()])
        self.assertFalse(verdict["ok"])
        keys = {r["reason"] for r in verdict["reasons"]}
        self.assertIn("drafts_pending", keys)

    def test_the_verdict_reads_the_report_rather_than_guessing(self):
        report = build_report(_rows(), [], [])
        verdict = closure_verdict(report)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["counts"]["runs"], report["totals"]["runs"])

    def test_the_two_surfaces_give_the_same_answer(self):
        rows = _rows(status="escalated", ticket_id="T-testclo", closed=False)
        shared = closure_verdict_for(rows, [_ticket()], [])

        import mas.mcp_server as mcp_module

        real_engine = mcp_module._engine

        class _Ledger:
            def entries(self):
                return list(rows)

        class _Tickets:
            def list(self):
                return [_ticket()]

        class _Engine:
            def __init__(self):
                self.ledger = _Ledger()
                self.tickets = _Tickets()

        try:
            mcp_module._engine = lambda _c, team=None: _Engine()
            payload = mcp_module.handle_tool("mas_closure", {"as_markdown": False})
        finally:
            mcp_module._engine = real_engine

        self.assertEqual(payload["ok"], shared["ok"])
        self.assertEqual(payload["empty"], shared["empty"])
        self.assertEqual(payload["headline"], shared["headline"])
        self.assertEqual(
            [r["reason"] for r in payload["reasons"]],
            [r["reason"] for r in shared["reasons"]],
        )

    def test_the_tool_is_advertised(self):
        import mas.mcp_server as mcp_module

        names = [str(t.get("name") or "") for t in mcp_module.TOOLS]
        self.assertIn("mas_closure", names)

    def test_the_reading_names_the_open_part(self):
        verdict = closure_verdict_for(
            _rows(status="escalated", ticket_id="T-testclo", closed=False),
            [_ticket()],
            [],
        )
        text = format_closure(verdict)
        self.assertIn("未闭合", text)
        self.assertIn("工单在等人", text)

    def test_the_empty_reading_does_not_claim_closure(self):
        verdict = closure_verdict_for([], [], [])
        text = format_closure(verdict)
        self.assertNotIn("已闭合", text)
        self.assertIn("无法判定", text)

    def test_the_verdict_never_runs_a_loop(self):
        """The verdict reads. It must not start a new run."""
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mas",
            "report.py",
        )
        with open(report_path, encoding="utf-8") as handle:
            src = handle.read()
        body = src[src.find("def closure_verdict("):]
        for forbidden in ("engine.run(", "LoopEngine(", ".run("):
            self.assertNotIn(forbidden, body)


class SharedDraftWalkTest(unittest.TestCase):
    """The CLI and the MCP server must find the same drafts."""

    def test_the_walk_is_one_implementation(self):
        import mas.cli as cli_module
        import mas.drafts as drafts_module
        import mas.mcp_server as mcp_module

        self.assertTrue(hasattr(drafts_module, "all_drafts_of_machine"))
        # Both surfaces must call the shared walk, not carry their own.
        for module in (cli_module, mcp_module):
            with open(module.__file__, encoding="utf-8") as handle:
                source = handle.read()
            self.assertIn("all_drafts_of_machine", source)
            self.assertNotIn("def _all_drafts_", source)

    def test_the_walk_finds_a_draft_in_the_artifacts_root(self):
        from mas.drafts import all_drafts_of_machine

        tmp = tempfile.mkdtemp(prefix="mas-closure-walk-")
        try:
            run_dir = os.path.join(tmp, "artifacts", "R-abc")
            os.makedirs(run_dir)
            target = os.path.join(run_dir, "out.md")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("old\n")
            from mas.drafts import stage_draft

            staged = stage_draft(run_dir, "out.md", b"new\n", reason="probe")
            found = all_drafts_of_machine(tmp)
            self.assertIn(staged["id"], [d["id"] for d in found])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_walk_survives_a_broken_folder(self):
        from mas.drafts import all_drafts_of_machine

        tmp = tempfile.mkdtemp(prefix="mas-closure-walk-")
        try:
            os.makedirs(os.path.join(tmp, "artifacts"))
            found = all_drafts_of_machine(tmp)
            self.assertEqual(found, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ClosureCliTest(unittest.TestCase):
    """The CLI command must exist and read the same records."""

    def test_the_command_is_registered(self):
        import mas.cli as cli_module

        self.assertTrue(hasattr(cli_module, "cmd_closure"))

    def test_the_json_output_carries_the_verdict(self):
        import mas.cli as cli_module

        tmp = tempfile.mkdtemp(prefix="mas-closure-cli-")
        old = os.environ.get("MAS_STATE_DIR")
        try:
            os.environ["MAS_STATE_DIR"] = tmp
            import importlib

            importlib.reload(cli_module)
            import argparse

            args = argparse.Namespace(json=True, run_id="")
            with open(os.devnull, "w", encoding="utf-8") as sink:
                stdout = sys.stdout
                sys.stdout = sink
                try:
                    code = cli_module.cmd_closure(args)
                finally:
                    sys.stdout = stdout
            self.assertEqual(code, 2)  # an empty ledger is not closed
        finally:
            if old is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

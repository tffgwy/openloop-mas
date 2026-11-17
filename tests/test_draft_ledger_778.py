# -*- coding: utf-8 -*-
"""v0.7.78 tests: a human decision leaves a trace that cannot be changed.

v0.7.77 made the undo put the file back byte for byte. But the undo
was still only a *file operation*: once it was done, nothing in the
machine's own records said that an approval had been taken back. The
thread looked like an ordinary approved change.

v0.7.78 writes the decision down twice:

* into a hash-linked chain next to the workspace - every entry carries
  the hash of the one before it, so a changed entry is visible;
* into the main ledger, where ``mas ledger`` shows it.

The two copies have to agree, and ``mas audit`` checks that. This file
covers:

* the chain records every decision, in order;
* the chain links entry to entry and verifies;
* a changed entry, a deleted entry, a changed ledger copy and a missing
  ledger copy are each noticed;
* the command line and the MCP tool can both check the chain;
* the report does not mistake a decision for a run.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import argparse
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.audit import AuditChain, audit_report
from mas.drafts import approve_draft, stage_draft, undo_draft
from mas.ledger import Ledger
from mas.selftest import CHECKS, EXPECTED_CHECKS


def _tool_names():
    from mas.mcp_server import TOOLS

    return [str(t.get("name") or "") for t in TOOLS if t.get("name")]


class _StateCase(unittest.TestCase):
    """A test case with its own state directory, so nothing leaks out."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-audit-test-"))
        self.state = self.root / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.previous_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.state)
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self.previous_state

    def workspace(self, name="ws"):
        base = self.root / name
        base.mkdir(parents=True, exist_ok=True)
        (base / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        return base

    def decide(self, workspace, note="approved the wrong thing"):
        staged = stage_draft(
            workspace, "notes.txt", "alpha\nbeta-agent\n".encode("utf-8")
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(workspace, draft_id)
        undo_draft(workspace, draft_id, note=note)
        return draft_id

    def ledger_rows(self):
        path = self.state / "ledgers" / "runs.jsonl"
        return Ledger(path).entries()


class ChainWritingTests(_StateCase):
    """Every decision leaves an entry, and the entries are linked."""

    def test_every_decision_leaves_an_entry_in_order(self):
        workspace = self.workspace()
        self.decide(workspace)
        chain = AuditChain(workspace / ".mas-drafts" / "audit.jsonl")
        events = [str(e.get("event") or "") for e in chain.entries()]
        self.assertEqual(
            events,
            ["draft_stage", "draft_approve", "draft_undo"],
        )

    def test_the_chain_links_entry_to_entry(self):
        workspace = self.workspace()
        self.decide(workspace)
        chain = AuditChain(workspace / ".mas-drafts" / "audit.jsonl")
        entries = chain.entries()
        self.assertTrue(entries)
        self.assertEqual(str(entries[0].get("prev_sha256") or ""), "0" * 64)
        for index in range(1, len(entries)):
            self.assertEqual(
                str(entries[index].get("prev_sha256") or ""),
                str(entries[index - 1].get("sha256") or ""),
            )
        report = chain.verify()
        self.assertTrue(report.get("ok"), report.get("problems"))
        self.assertEqual(int(report.get("entries") or 0), len(entries))

    def test_the_same_draft_keeps_every_decision(self):
        workspace = self.workspace()
        draft_id = self.decide(workspace)
        chain = AuditChain(workspace / ".mas-drafts" / "audit.jsonl")
        ids = {str(e.get("draft_id") or "") for e in chain.entries()}
        self.assertEqual(ids, {draft_id})

    def test_a_ledger_copy_is_written_for_the_approve_and_the_undo(self):
        workspace = self.workspace()
        self.decide(workspace)
        roles = [str(row.get("role") or "") for row in self.ledger_rows()]
        self.assertEqual(roles.count("draft_approve"), 1)
        self.assertEqual(roles.count("draft_undo"), 1)

    def test_the_ledger_copy_carries_the_fields_of_the_chain_entry(self):
        workspace = self.workspace()
        draft_id = self.decide(workspace, note="the reason")
        chain = AuditChain(workspace / ".mas-drafts" / "audit.jsonl")
        entries = {
            str(e.get("sha256") or ""): e for e in chain.entries()
        }
        found = False
        for row in self.ledger_rows():
            key = str(row.get("audit_sha256") or "")
            if key not in entries:
                continue
            found = True
            entry = entries[key]
            self.assertEqual(str(row.get("draft_id") or ""), draft_id)
            self.assertEqual(str(row.get("note") or ""), str(entry.get("note") or ""))
            self.assertEqual(str(row.get("path") or ""), str(entry.get("path") or ""))
        self.assertTrue(found, "the ledger copy was not found")


class TamperDetectionTests(_StateCase):
    """The chain notices when it is changed."""

    def test_a_changed_entry_is_noticed(self):
        workspace = self.workspace()
        self.decide(workspace)
        path = workspace / ".mas-drafts" / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["note"] = "changed behind the back of the reader"
        lines[1] = json.dumps(row, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = AuditChain(path).verify()
        self.assertFalse(report.get("ok"))
        self.assertTrue(report.get("problems"))

    def test_a_deleted_entry_is_noticed(self):
        workspace = self.workspace()
        self.decide(workspace)
        path = workspace / ".mas-drafts" / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
        report = AuditChain(path).verify()
        self.assertFalse(report.get("ok"))
        self.assertTrue(report.get("problems"))

    def test_a_deleted_last_entry_is_caught_by_the_ledger_copy(self):
        # The honest limit of a single chain: nothing after the last
        # entry points at what was removed, so the chain still verifies.
        # The second copy - the ledger - is what catches it.
        workspace = self.workspace()
        self.decide(workspace)
        path = workspace / ".mas-drafts" / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        chain_report = AuditChain(path).verify()
        self.assertTrue(chain_report.get("ok"))
        rows = [
            row
            for row in self.ledger_rows()
            if str(row.get("workspace") or "") == str(workspace)
        ]
        report = audit_report(workspace, rows)
        self.assertFalse(report.get("ok"))
        self.assertTrue(report.get("problems"))

    def test_a_changed_ledger_copy_is_noticed(self):
        workspace = self.workspace()
        self.decide(workspace)
        copy = Ledger(self.state / "copy.jsonl")
        for source in self.ledger_rows():
            row = dict(source)
            if str(row.get("role") or "") == "draft_undo":
                row["note"] = "the ledger was edited on its own"
            copy.append(row)
        report = audit_report(workspace, copy.entries())
        self.assertFalse(report.get("ok"))
        self.assertTrue(report.get("problems"))

    def test_a_missing_ledger_copy_is_noticed(self):
        workspace = self.workspace()
        self.decide(workspace)
        dropped = Ledger(self.state / "dropped.jsonl")
        for source in self.ledger_rows():
            if str(source.get("role") or "") == "draft_undo":
                continue
            dropped.append(source)
        report = audit_report(workspace, dropped.entries())
        self.assertFalse(report.get("ok"))
        self.assertTrue(report.get("problems"))

    def test_an_untouched_chain_agrees_with_the_ledger(self):
        workspace = self.workspace()
        self.decide(workspace)
        report = audit_report(workspace, self.ledger_rows())
        self.assertTrue(report.get("ok"), report.get("problems"))
        self.assertEqual(int(report.get("chain_count") or 0), 1)

    def test_a_proposal_does_not_shift_the_numbering_of_the_decisions(self):
        """A proposal is not a decision, and it must not shift the numbering.

        The chain holds the proposal as well as the decisions; the
        ledger holds only the decisions. If the two copies were matched
        by a counter that each side kept for itself, the proposal
        would push the numbering of the decisions apart and every
        decision would look like it was missing from the other side.
        """
        workspace = self.workspace()
        self.decide(workspace)
        report = audit_report(workspace, self.ledger_rows())
        self.assertTrue(
            report.get("ok"), "an untouched chain must agree: %s"
            % report.get("problems"),
        )

    def test_a_changed_entry_is_named_as_a_changed_entry(self):
        """The message has to say what happened, not just that it did.

        An entry whose content was edited still *is* the entry the
        ledger knows about - it is only its hash and its fields that no
        longer agree. Reporting it as "not in the ledger" would name
        the wrong thing: the copy is there, it was changed.
        """
        workspace = self.workspace()
        self.decide(workspace)
        path = workspace / ".mas-drafts" / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["note"] = "changed"
        lines[1] = json.dumps(row, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = audit_report(workspace, self.ledger_rows())
        self.assertFalse(report.get("ok"))
        problems = " | ".join(report.get("problems") or [])
        self.assertIn("内容被改过", problems)
        self.assertNotIn("没有进账本", problems)

    def test_a_problem_is_not_reported_twice(self):
        """One problem, one line."""
        workspace = self.workspace()
        self.decide(workspace)
        path = workspace / ".mas-drafts" / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["note"] = "changed"
        lines[1] = json.dumps(row, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = audit_report(workspace, self.ledger_rows())
        problems = list(report.get("problems") or [])
        self.assertEqual(len(problems), len(set(problems)), problems)

    def test_two_workspaces_do_not_report_each_other(self):
        first = self.workspace("one")
        second = self.workspace("two")
        self.decide(first)
        self.decide(second)
        rows = self.ledger_rows()
        for name, workspace in (("one", first), ("two", second)):
            report = audit_report(workspace, rows)
            self.assertTrue(
                report.get("ok"), "%s: %s" % (name, report.get("problems"))
            )


class CommandLineTests(_StateCase):
    """``mas audit`` can check the chain."""

    def _run(self, workspace, as_json=False):
        from mas.cli import cmd_audit

        args = argparse.Namespace(workspace=str(workspace), json=as_json)
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_audit(args)
        return int(code), out.getvalue(), err.getvalue()

    def test_the_command_line_says_the_chain_is_intact(self):
        workspace = self.workspace()
        self.decide(workspace)
        code, text, _ = self._run(workspace)
        self.assertEqual(code, 0)
        self.assertIn("审计链", text)
        self.assertIn("完好", text)

    def test_the_command_line_reports_a_changed_chain(self):
        workspace = self.workspace()
        self.decide(workspace)
        path = workspace / ".mas-drafts" / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["note"] = "changed"
        lines[1] = json.dumps(row, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, text, _ = self._run(workspace)
        self.assertEqual(code, 1)
        self.assertIn("问题", text)

    def test_the_command_line_can_print_json(self):
        workspace = self.workspace()
        self.decide(workspace)
        code, text, _ = self._run(workspace, as_json=True)
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(int(payload.get("chain_count") or 0), 1)

    def test_a_workspace_without_a_chain_is_not_a_failure(self):
        workspace = self.workspace()
        code, text, _ = self._run(workspace)
        self.assertEqual(code, 0)
        self.assertIn("还没有", text)


class McpToolTests(_StateCase):
    """The MCP server can check the chain too."""

    def test_the_mcp_tool_is_registered(self):
        self.assertIn("mas_audit", _tool_names())

    def test_the_mcp_tool_reports_the_chain(self):
        from mas.mcp_server import handle_tool

        workspace = self.workspace()
        self.decide(workspace)
        payload = handle_tool(
            "mas_audit", {"workspace": str(workspace)}
        )
        self.assertTrue(payload.get("ok"), payload)
        self.assertEqual(int(payload.get("chain_count") or 0), 1)


class ReportTests(_StateCase):
    """A decision is not a run."""

    def test_the_report_does_not_count_a_decision_as_a_run(self):
        from mas.report import group_runs

        workspace = self.workspace()
        self.decide(workspace)
        rows = self.ledger_rows()
        self.assertTrue(rows)
        grouped = group_runs(rows)
        self.assertEqual(len(grouped), 0, grouped)


class GateRegistrationTests(unittest.TestCase):
    """The twenty-first gate is registered and it can fail."""

    def test_the_gate_is_the_twenty_first(self):
        names = [getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS]
        self.assertEqual(len(CHECKS), EXPECTED_CHECKS)
        self.assertIn("audit_chain", names)

    def test_the_gate_passes(self):
        from mas.selftest_audit import (
            check_a_human_decision_leaves_a_trace_that_cannot_be_changed,
        )

        result = check_a_human_decision_leaves_a_trace_that_cannot_be_changed(
            iterations=1
        )
        self.assertTrue(result.get("ok"), result.get("failures"))

    def test_the_gate_notices_that_it_cannot_fail(self):
        import mas.audit as audit_module
        from mas.selftest_audit import (
            check_a_human_decision_leaves_a_trace_that_cannot_be_changed,
        )

        real_verify = audit_module.AuditChain.verify

        def _always_passes(self):
            return {"ok": True, "entries": 999, "head": "", "problems": []}

        try:
            audit_module.AuditChain.verify = _always_passes
            result = check_a_human_decision_leaves_a_trace_that_cannot_be_changed(
                iterations=1
            )
        finally:
            audit_module.AuditChain.verify = real_verify
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("failures"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

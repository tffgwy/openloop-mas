# -*- coding: utf-8 -*-
"""The thirty-first gate: the report has to read the decision chains.

The gap
-------
Every human decision - the proposal, the approval, the undo - leaves
a hash-linked entry in a chain next to the workspace it belongs to,
and a copy in the main ledger. ``mas audit`` checks those chains and
``mas status`` shows the result.

The *report* never looked. It is the screen a human reads after the
fact, and it kept reporting a clean history for decisions whose
records had been edited on the disk: the ledger row said "approved",
the chain said something else, and the report said nothing at all.

That is the third hole of the same family - after the seal in
v0.7.89 and the taken-back approval in v0.7.90. A capability that
exists, and a main view that does not consume it, is a capability
nobody has.

So this gate watches the *report*:

* a healthy chain is reported as intact;
* a chain whose entry was changed on the disk is named, with the
  entry that was changed;
* a chain that is gone while the ledger still holds the decision is
  named - a deleted chain leaves nothing to read, so the report has
  to notice the *absence*;
* the headline counts the chains;
* the JSON payload carries the reconciliation, so a script can ask
  the question without parsing Markdown.

And the gate proves it can fail: the report's audit reading is
broken on purpose - every chain is reported as intact - and the gate
has to notice that the report can no longer tell a healthy chain
from a changed one.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
REPORT_AUDIT_OBSERVATIONS = (
    "a healthy chain is reported as intact",
    "a changed chain is named in the report",
    "a missing chain is named in the report",
    "the headline counts the audit chains",
    "the JSON payload carries the audit reconciliation",
)

#: The words the report uses for a chain whose entry was changed.
CHANGED_WORD = "\u5185\u5bb9\u88ab\u6539\u8fc7"

#: The words the report uses for a chain that is not there at all.
MISSING_WORD = "\u5ba1\u8ba1\u94fe\u4e0d\u89c1\u4e86"


def _make_workspace(root: pathlib.Path, name: str) -> pathlib.Path:
    """A workspace with one proposed change, approved and taken back."""
    from mas.drafts import approve_draft, stage_draft, undo_draft

    workspace = pathlib.Path(root) / name
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    staged = stage_draft(
        workspace,
        "notes.txt",
        "alpha\nbeta-agent\n".encode("utf-8"),
        reason="the agent proposes this",
    )
    draft_id = str(staged.get("id") or "")
    approve_draft(workspace, draft_id)
    undo_draft(workspace, draft_id, note="approved the wrong thing")
    return workspace


def _report_audit_observations(root: Any) -> Dict[str, Any]:
    """Look at what the report says about the decision chains.

    Two reports are read, because they answer two different
    questions: the report of a *healthy* workspace (does the report
    say the chain is intact?) and the report of a *damaged* one (does
    the report name what was changed, and what is gone?).
    """
    from mas.ledger import Ledger
    from mas.report import build_report, render_markdown

    base = pathlib.Path(root)
    state = base / "state"
    state.mkdir(parents=True, exist_ok=True)

    # The decisions have to land in a ledger of this test's own, not
    # in the ledger of the machine the test runs on.
    previous = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = str(state)
    try:
        healthy = _make_workspace(base, "healthy")
        healthy_rows = Ledger(state / "ledgers" / "runs.jsonl").entries()
        changed = _make_workspace(base, "changed")
        missing = _make_workspace(base, "missing")
        rows = Ledger(state / "ledgers" / "runs.jsonl").entries()
    finally:
        if previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = previous

    # A chain whose entry was changed behind the reader's back.
    chain_file = changed / ".mas-drafts" / "audit.jsonl"
    lines = chain_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["note"] = "changed behind the back of the reader"
    lines[1] = json.dumps(entry, ensure_ascii=False)
    chain_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # A chain that is gone while the ledger still holds the decision.
    (missing / ".mas-drafts" / "audit.jsonl").unlink()

    healthy_report = build_report(healthy_rows)
    damaged_report = build_report(rows)
    seen: Dict[str, Any] = {
        "healthy_markdown": render_markdown(healthy_report),
        "healthy_json": healthy_report,
        "damaged_markdown": render_markdown(damaged_report),
        "damaged_json": damaged_report,
        "healthy_path": str(healthy / ".mas-drafts" / "audit.jsonl"),
    }
    return seen


def _report_audit_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from the Markdown it read."""
    verdicts: Dict[str, bool] = {}
    healthy = str(facts.get("healthy_markdown") or "")
    damaged = str(facts.get("damaged_markdown") or "")

    verdicts["a healthy chain is reported as intact"] = (
        "\u5ba1\u8ba1\u94fe\u590d\u6838\uff1a" in healthy
        and "\u5168\u90e8\u5b8c\u597d" in healthy
    )
    verdicts["a changed chain is named in the report"] = (
        CHANGED_WORD in damaged
    )
    verdicts["a missing chain is named in the report"] = (
        MISSING_WORD in damaged
    )
    verdicts["the headline counts the audit chains"] = (
        "\u5ba1\u8ba1\u94fe\u590d\u6838\uff1a" in damaged
        and "## \u5ba1\u8ba1\u94fe\u590d\u6838" in damaged
    )
    verdicts["the JSON payload carries the audit reconciliation"] = (
        isinstance((facts.get("damaged_json") or {}).get("audit"), dict)
        and isinstance(
            (facts.get("damaged_json") or {}).get("totals"), dict
        )
    )
    return verdicts


def _report_audit_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same conclusions, read from the payload instead of the text.

    Two readings that agree are a fact; one reading is a guess about
    how the words happen to be spelled.
    """
    verdicts: Dict[str, bool] = {}
    healthy_payload = facts.get("healthy_json") or {}
    damaged_payload = facts.get("damaged_json") or {}
    healthy_audit = healthy_payload.get("audit") or {}
    damaged_audit = damaged_payload.get("audit") or {}
    healthy_totals = healthy_payload.get("totals") or {}
    damaged_totals = damaged_payload.get("totals") or {}

    healthy_path = str(facts.get("healthy_path") or "")
    healthy_chain = None
    for chain in list(healthy_audit.get("chains") or []):
        if str(chain.get("path") or "") == healthy_path:
            healthy_chain = chain
            break
    verdicts["a healthy chain is reported as intact"] = (
        healthy_chain is not None
        and bool(healthy_chain.get("ok"))
        and not list(healthy_chain.get("problems") or [])
        and bool(healthy_audit.get("ok"))
        and not list(healthy_audit.get("problems") or [])
    )

    problems = [str(item) for item in (damaged_audit.get("problems") or [])]
    verdicts["a changed chain is named in the report"] = any(
        CHANGED_WORD in problem for problem in problems
    )
    verdicts["a missing chain is named in the report"] = any(
        MISSING_WORD in problem for problem in problems
    )
    chains = list(damaged_audit.get("chains") or [])
    verdicts["the headline counts the audit chains"] = (
        int(damaged_totals.get("audit_chains") or 0) == len(chains)
        and int(damaged_totals.get("audit_chains") or 0) > 0
        and int(damaged_totals.get("audit_entries") or 0) > 0
        and int(healthy_totals.get("audit_chains") or 0)
        == len(list(healthy_audit.get("chains") or []))
    )
    verdicts["the JSON payload carries the audit reconciliation"] = (
        int(damaged_totals.get("audit_problems") or 0) == len(problems)
        and int(damaged_totals.get("audit_problems") or 0) > 0
        and bool(damaged_audit.get("ok")) is False
        and "ok" in healthy_audit
        and "chains" in healthy_audit
    )
    return verdicts


def check_the_report_reads_the_decision_chains(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The report has to read the decision chains - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-audit-"))
        rounds += 1
        try:
            try:
                facts = _report_audit_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the report check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _report_audit_verdicts(facts)
            independent = _report_audit_verdicts_independent(facts)
            for label in REPORT_AUDIT_OBSERVATIONS:
                if label not in verdicts:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                    continue
                if not verdicts[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))
                if independent.get(label) != verdicts.get(label):
                    failures.append(
                        "round %d: %s - the two readings disagree (%s vs %s)"
                        % (
                            round_index + 1,
                            label,
                            verdicts.get(label),
                            independent.get(label),
                        )
                    )

            # The gate proves it can fail: the report's audit reading is
            # broken on purpose - every chain is reported as intact -
            # and the gate has to notice.
            import mas.report as report_module

            real_reading = report_module.audit_reading

            def _always_intact(rows, _real=real_reading):
                _real(rows)
                return {
                    "workspaces": [],
                    "chains": [],
                    "chain_count": 0,
                    "entries": 0,
                    "problems": [],
                    "problem_count": 0,
                    "ok": True,
                }

            try:
                report_module.audit_reading = _always_intact
                broken: Dict[str, bool] = {}
                try:
                    broken = _report_audit_verdicts(_report_audit_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.audit_reading = real_reading
            if broken.get("a changed chain is named in the report"):
                failures.append(
                    "round %d: the gate did not notice that the report's "
                    "audit reading is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "report_audit",
        "title": "\u590d\u76d8\u62a5\u544a\u8bfb\u4e86\u4eba\u5de5\u51b3\u5b9a\u7684\u5ba1\u8ba1\u94fe",
        "skipped": False,
        "invariant": (
            "\u590d\u76d8\u62a5\u544a\u8bfb\u5ba1\u8ba1\u94fe\uff1a\u9876\u90e8\u8ba1\u6570\u3001"
            "\u88ab\u6539\u8fc7\u7684\u94fe\u88ab\u70b9\u540d\u3001\u4e0d\u89c1\u4e86\u7684\u94fe\u88ab\u70b9\u540d\u3001"
            "JSON \u5e26\u6570\u5b57"
        ),
        "detail": "\u590d\u76d8\u62a5\u544a\u662f\u5426\u8bfb\u4e86\u4eba\u5de5\u51b3\u5b9a\u7684\u5ba1\u8ba1\u94fe\uff1amas report",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_reads_the_decision_chains.CHECK_NAME = "report_audit"  # type: ignore[attr-defined]

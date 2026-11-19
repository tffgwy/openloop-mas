# -*- coding: utf-8 -*-
"""The thirtieth gate: the report has to say which approvals were wrong.

The gap
-------
A wrong approval is a fact the machine already keeps. The audit chain
holds it as a hash-linked entry, the ledger holds a copy of it, and
``mas undos`` can list them: which approvals were taken back, when,
and why.

The *report* said nothing. ``run_summary`` never even looked at the
``draft_approve`` and ``draft_undo`` rows - a human decision about a
draft is not a run, so the report had no place for it and no place
for it was made. A closure could be reported as "machine-closed"
while the reader's own bad approvals from three weeks ago sat in the
ledger, unmentioned.

That is the same shape of hole as v0.7.89's: a capability that
exists, and a main view that does not consume it.

So this gate watches the *report*:

* a taken-back approval is counted in the headline;
* it gets a section of its own, naming the draft, the file, how many
  times it was taken back and how many lines went back;
* the JSON payload carries the numbers, so a script can ask the
  question without parsing Markdown;
* a ledger that holds only decisions is **not** reported as empty -
  saying "the ledger is empty" about a ledger full of wrong
  approvals would be a false statement.

And the gate proves it can fail: the report's undo counting is broken
on purpose - every approval is reported as standing - and the gate
has to notice that the report can no longer tell a wrong approval
from a right one.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
REPORT_UNDO_OBSERVATIONS = (
    "a taken-back approval is counted in the headline",
    "the report has a section naming the taken-back approvals",
    "the section names the draft and the file",
    "the JSON payload carries the undo reconciliation",
    "a ledger of only decisions is not called empty",
)


def _decision_rows(workspace: pathlib.Path) -> List[Dict[str, Any]]:
    """One approval and one undo, as the ledger holds them."""
    draft_id = "D-selftestundo"
    return [
        {
            "role": "draft_approve",
            "event": "draft_approve",
            "run_id": "",
            "draft_id": draft_id,
            "path": "notes.txt",
            "workspace": str(workspace),
            "seq": 1,
            "ts": "2026-11-19T00:00:00Z",
            "note": "looks right",
            "approved_sha256": "a" * 64,
            "current_sha256": "a" * 64,
            "would_lose_count": 0,
            "forced": False,
            "changed_line_count": 3,
        },
        {
            "role": "draft_undo",
            "event": "draft_undo",
            "run_id": "",
            "draft_id": draft_id,
            "path": "notes.txt",
            "workspace": str(workspace),
            "seq": 2,
            "ts": "2026-11-19T00:01:00Z",
            "note": "actually wrong",
            "approved_sha256": "a" * 64,
            "current_sha256": "a" * 64,
            "would_lose_count": 0,
            "forced": False,
            "reverted_line_count": 3,
        },
    ]


def _report_undo_observations(root: Any) -> Dict[str, Any]:
    """Look at what the report says about a wrong approval."""
    from mas.report import build_report, render_markdown

    workspace = pathlib.Path(root) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    rows = _decision_rows(workspace)

    report = build_report(rows)
    seen: Dict[str, Any] = {
        "rows": rows,
        "markdown": render_markdown(report),
        "json": report,
    }

    # A ledger that holds a run as well - the normal case. The
    # decision rows must not turn into a phantom run, and the
    # taken-back approval must still be reported.
    mixed = list(rows)
    mixed.append(
        {
            "role": "run_end",
            "run_id": "R-selftestundo",
            "status": "closed",
            "decision": "pass",
            "goal": "selftest",
            "iterations": 1,
            "tokens": 5,
        }
    )
    mixed_report = build_report(mixed)
    seen["mixed_markdown"] = render_markdown(mixed_report)
    seen["mixed_json"] = mixed_report
    seen["mixed_run_count"] = len(mixed_report.get("runs") or [])
    return seen


def _report_undo_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}
    markdown = str(facts.get("markdown") or "")
    payload = facts.get("json") or {}

    verdicts["a taken-back approval is counted in the headline"] = (
        "\u64a4\u9500\u56de\u6536\uff1a1 \u6b21\u6279\u51c6\u88ab\u64a4\u9500" in markdown
    )

    verdicts["the report has a section naming the taken-back approvals"] = (
        "## \u88ab\u64a4\u9500\u7684\u6279\u51c6" in markdown
    )

    verdicts["the section names the draft and the file"] = (
        "D-selftestundo" in markdown and "notes.txt" in markdown
    )

    totals = payload.get("totals") if isinstance(payload, dict) else None
    undos = payload.get("undos") if isinstance(payload, dict) else None
    verdicts["the JSON payload carries the undo reconciliation"] = (
        isinstance(totals, dict)
        and int(totals.get("approvals_taken_back") or 0) == 1
        and int(totals.get("drafts_taken_back") or 0) == 1
        and int(totals.get("lines_reverted") or 0) == 3
        and isinstance(undos, dict)
        and len(undos.get("drafts") or []) == 1
    )

    # A ledger that holds only decisions is not empty. Saying so
    # would be a false statement about the record.
    verdicts["a ledger of only decisions is not called empty"] = (
        "\u8d26\u672c\u4e3a\u7a7a" not in markdown
        and "\u8fd8\u6ca1\u6709\u8dd1\u8fc7\u4efb\u4f55\u4e00\u6b21\u95ed\u73af" in markdown
    )
    return verdicts


def _report_undo_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    This reading goes back to the ledger rows and pairs the decisions
    with its own code - it never looks at the report's own ``undos``
    field, and it never looks at the rendered text.
    """
    verdicts: Dict[str, bool] = {}

    rows = facts.get("rows") or []
    approves = [r for r in rows if str(r.get("role") or "") == "draft_approve"]
    undos = [r for r in rows if str(r.get("role") or "") == "draft_undo"]
    paired = len(approves) == len(undos) == 1

    verdicts["a taken-back approval is counted in the headline"] = paired

    verdicts["the report has a section naming the taken-back approvals"] = paired

    verdicts["the section names the draft and the file"] = (
        paired
        and str(undos[0].get("draft_id") or "") == "D-selftestundo"
        and str(undos[0].get("path") or "") == "notes.txt"
    )

    totals = (facts.get("json") or {}).get("totals") or {}
    verdicts["the JSON payload carries the undo reconciliation"] = (
        paired
        and int(totals.get("approvals_taken_back") or 0) == 1
        and int(totals.get("lines_reverted") or 0) == 3
    )

    # Independently: the decision rows must not become a run.
    mixed = facts.get("mixed_json") or {}
    verdicts["a ledger of only decisions is not called empty"] = (
        paired
        and int(facts.get("mixed_run_count") or 0) == 1
        and int((mixed.get("totals") or {}).get("approvals_taken_back") or 0) == 1
    )
    return verdicts


def check_the_report_says_which_approvals_were_wrong(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The report has to say which approvals were wrong - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-undo-"))
        rounds += 1
        try:
            try:
                facts = _report_undo_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the report check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _report_undo_verdicts(facts)
            independent = _report_undo_verdicts_independent(facts)
            for label in REPORT_UNDO_OBSERVATIONS:
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

            # The gate proves it can fail: the report's undo counting
            # is broken on purpose - every approval is reported as
            # still standing - and the gate has to notice.
            import mas.report as report_module

            real_state = report_module.undone_approvals

            def _no_undos(rows, _real=real_state):
                outcome = _real(rows)
                outcome["records"] = []
                outcome["drafts"] = []
                outcome["draft_count"] = 0
                outcome["totals"] = {"undone": 0, "drafts": 0, "reverted_lines": 0}
                outcome["unmatched"] = []
                return outcome

            try:
                report_module.undone_approvals = _no_undos
                broken: Dict[str, bool] = {}
                try:
                    broken = _report_undo_verdicts(_report_undo_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.undone_approvals = real_state
            if broken.get("a taken-back approval is counted in the headline"):
                failures.append(
                    "round %d: the gate did not notice that the report's "
                    "undo counting is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "report_undo",
        "title": "\u590d\u76d8\u62a5\u544a\u8bf4\u51fa\u54ea\u4e9b\u6279\u51c6\u88ab\u64a4\u9500\u8fc7",
        "skipped": False,
        "invariant": (
            "\u590d\u76d8\u62a5\u544a\u8ba4\u5f97\u51fa\u88ab\u64a4\u9500\u7684\u6279\u51c6\uff1a"
            "\u9876\u90e8\u8ba1\u6570\u3001\u5355\u72ec\u4e00\u8282\u70b9\u540d\u8349\u7a3f\u4e0e\u6587\u4ef6\u3001"
            "JSON \u5e26\u6570\u5b57\uff1b\u53ea\u6709\u4eba\u5de5\u51b3\u5b9a\u7684\u8d26\u672c\u4e0d\u88ab\u8bf4\u6210\u7a7a"
        ),
        "detail": "\u590d\u76d8\u62a5\u544a\u662f\u5426\u8bf4\u51fa\u54ea\u4e9b\u6279\u51c6\u88ab\u64a4\u9500\u8fc7\uff1amas report",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_says_which_approvals_were_wrong.CHECK_NAME = "report_undo"  # type: ignore[attr-defined]

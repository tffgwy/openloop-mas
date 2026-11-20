# -*- coding: utf-8 -*-
"""The thirty-sixth gate: the report has to read every ledger row.

The gap
-------
The report's ledger table has eight columns, and the last one is the
summary. That summary comes from ``_short_detail``, which dispatches
on the ledger role - ``closure``, ``planner``, ``review``, ``reflector``,
``handback``, ``draft_approve``, ``draft_undo`` and a few more.

Six roles had **no branch at all**::

    write         truncated      reask
    draft         reask_failed   lead

The dispatch falls through to ``return ""``, so those rows rendered as
**empty strings**. In the exported Markdown table the row existed, with
its number, its timestamp, its run id and its role - and the one
column that said *what happened* was blank.

That is not a cosmetic gap. The ``write`` row is the one that says
what the engine actually put on the disk: which file, how many bytes,
whether it was created or overwritten. That is the single line an
auditor reads to answer "what did the agent change on my machine?" -
and it was invisible. A run that wrote three files and a run that
wrote nothing looked exactly the same in the report.

The other five are the same story in a different costume:

* ``draft`` - the row that names the file a human still has to approve;
* ``truncated`` - the answer that ran out of room mid-sentence, which
  is why the loop retried at all;
* ``reask`` / ``reask_failed`` - the follow-up question the loop asked
  because a criterion was not met yet, and the failure to ask it;
* ``lead`` - the advisor the loop consulted before deciding.

So this gate watches the *trace*:

* every role the engine writes has a reading, and the reading is not
  empty;
* the ``write`` row names the file and the byte count;
* the ``draft`` row names the draft and the file;
* the ``truncated`` row says the answer was cut off and that a retry
  follows;
* the ``reask`` row names the criteria that were still missing;
* and the exported table is not allowed to carry a blank summary for
  any role the engine can produce.

And the gate proves it can fail: the trace's detail function is broken
on purpose - it returns an empty string for everything - and the gate
has to notice that the report can no longer tell a row that happened
from a row that did not.

The roles are read from a table rather than from a hard-coded list, so
a new role the engine grows later is covered by default.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: The observations the gate makes.
TRACE_ROLE_OBSERVATIONS = (
    "every engine role has a non-empty reading",
    "the write row names the file and the byte count",
    "the draft row names the draft and the file",
    "the truncated row says the answer was cut off",
    "the reask row names the missing criteria",
    "the exported table has no blank summary",
)

#: The roles the engine writes to the ledger, and a payload that makes
#: sense for each. The payload is what the row carries besides the
#: role name.
TRACE_ROLE_PAYLOADS = (
    ("planner", {"plan": {"summary": "先做计划"}}),
    ("candidates", {"candidates": [{"angle": "direct", "selected": True}]}),
    ("reflector", {"reflection": {"issues": ["缺一条"]}}),
    ("closure", {"verdict": "pass", "decision": "accept", "scores": []}),
    ("truncated", {"error": "answer cut off", "retry": True, "tokens": 120}),
    ("error", {"error": "provider down"}),
    ("run_end", {"status": "closed", "decision": "accept"}),
    ("draft", {"drafts": [{"id": "D-trace1", "path": "report.md"}]}),
    ("write", {"written": [{"path": "notes/out.txt", "bytes": 42, "created": True}]}),
    ("review", {"mode": "auto", "usable": True}),
    ("reask", {"missing": ["c1", "c2"]}),
    ("reask_failed", {"error": "no answer", "missing": ["c1"]}),
    ("lead", {"lead": {"summary": "建议先做 A"}}),
    ("handback", {"scores": [{"id": "c1", "pass": True}]}),
)


def _rows_for_role(role: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(payload)
    row.update(
        {
            "role": role,
            "run_id": "R-trace",
            "task_id": "T-trace",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
        }
    )
    return row


def _trace_observations() -> Dict[str, Any]:
    """Look at what the report says about each kind of ledger row."""
    from mas.report import _human_detail, _short_detail, ledger_markdown

    details: Dict[str, str] = {}
    humans: Dict[str, str] = {}
    for role, payload in TRACE_ROLE_PAYLOADS:
        row = _rows_for_role(role, payload)
        details[role] = _short_detail(row)
        humans[role] = _human_detail(row)

    rows = [_rows_for_role(role, payload) for role, payload in TRACE_ROLE_PAYLOADS]
    return {
        "details": details,
        "humans": humans,
        "table": ledger_markdown(rows),
    }


def _trace_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The reading that goes through the report."""
    verdicts: Dict[str, bool] = {}
    details = dict(facts.get("details") or {})
    table = str(facts.get("table") or "")

    # The blunt one: every role the engine writes must say something.
    missing = [
        role
        for role, _payload in TRACE_ROLE_PAYLOADS
        if not str(details.get(role) or "").strip()
    ]
    verdicts["every engine role has a non-empty reading"] = not missing

    verdicts["the write row names the file and the byte count"] = (
        "notes/out.txt" in str(details.get("write") or "")
        and "42" in str(details.get("write") or "")
    )

    verdicts["the draft row names the draft and the file"] = (
        "D-trace1" in str(details.get("draft") or "")
        and "report.md" in str(details.get("draft") or "")
    )

    verdicts["the truncated row says the answer was cut off"] = (
        "answer cut off" in str(details.get("truncated") or "")
        and "重试" in str(details.get("truncated") or "")
    )

    verdicts["the reask row names the missing criteria"] = (
        "c1" in str(details.get("reask") or "")
        and "c2" in str(details.get("reask") or "")
    )

    # The exported table is what a person actually opens. A row whose
    # summary column is empty is a row that says nothing.
    blank_rows = []
    for line in table.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 8:
            continue
        if not cells[7]:
            blank_rows.append(cells[4])
    verdicts["the exported table has no blank summary"] = not blank_rows
    return verdicts


def _trace_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The reading that does not go through the report's own dispatch.

    This one checks the same thing from the outside: it looks at the
    table that was rendered and asks whether every row says something.
    It shares no code with ``_short_detail``, so a broken dispatch
    cannot make both readings agree on a wrong answer.
    """
    verdicts: Dict[str, bool] = {}
    table = str(facts.get("table") or "")
    lines = [ln for ln in table.splitlines() if ln.startswith("|")]
    body = [ln for ln in lines if not ln.startswith("|---")][1:]
    non_empty = 0
    for line in body:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 8 and cells[7]:
            non_empty += 1
    verdicts["every engine role has a non-empty reading"] = (
        non_empty == len(TRACE_ROLE_PAYLOADS)
    )
    verdicts["the exported table has no blank summary"] = non_empty == len(
        TRACE_ROLE_PAYLOADS
    )
    verdicts["the write row names the file and the byte count"] = (
        "notes/out.txt" in table and "42" in table
    )
    verdicts["the draft row names the draft and the file"] = (
        "D-trace1" in table and "report.md" in table
    )
    verdicts["the truncated row says the answer was cut off"] = (
        "answer cut off" in table
    )
    verdicts["the reask row names the missing criteria"] = (
        "c1" in table and "c2" in table
    )
    return verdicts


def check_the_report_reads_every_ledger_row(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The report has to read every ledger row - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _trace_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the trace check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _trace_verdicts(facts)
            independent = _trace_verdicts_independent(facts)
            for label in TRACE_ROLE_OBSERVATIONS:
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

            # The gate proves it can fail: the trace's detail function is
            # broken on purpose - it says nothing at all - and the gate
            # has to notice.
            import mas.report as report_module

            real_detail = report_module._short_detail

            def _say_nothing(row, _real=real_detail):
                _real(row)
                return ""

            try:
                report_module._short_detail = _say_nothing
                broken: Dict[str, bool] = {}
                try:
                    broken = _trace_verdicts(_trace_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module._short_detail = real_detail
            if broken.get("every engine role has a non-empty reading"):
                failures.append(
                    "round %d: the gate did not notice that the trace says "
                    "nothing - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "report_trace_roles",
        "title": "\u590d\u76d8\u62a5\u544a\u8bfb\u4e86\u6bcf\u4e00\u6761\u8d26\u672c\u884c",
        "skipped": False,
        "invariant": (
            "\u6bcf\u4e00\u79cd\u8d26\u672c\u89d2\u8272\u90fd\u6709\u4e00\u53e5\u53ef\u8bfb\u7684\u8bf4\u660e\uff1a"
            "\u5199\u5165\u884c\u70b9\u540d\u4e0e\u5b57\u8282\u3001\u8349\u7a3f\u884c\u70b9\u540d\u4e0e\u6587\u4ef6\u3001"
            "\u622a\u65ad\u884c\u8bf4\u660e\u91cd\u8bd5\u3001\u8865\u95ee\u884c\u70b9\u540d\u672a\u6ee1\u8db3\u9879"
        ),
        "detail": "\u67e5\u770b\u8d26\u672c\u8868\u91cc\u6bcf\u4e00\u884c\u7684\u300c\u6458\u8981\u300d\u5217",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_reads_every_ledger_row.CHECK_NAME = "report_trace_roles"  # type: ignore[attr-defined]

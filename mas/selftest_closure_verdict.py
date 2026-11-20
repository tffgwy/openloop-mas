# -*- coding: utf-8 -*-
"""The thirty-eighth gate: somebody has to say whether the work is closed.

The gap
-------
This project is called a loop-engineering tool. The whole promise is
that the work *closes* - and yet the thing that says so did not exist
as a thing you could ask.

A report shows every number: the runs, the threads, the tickets, the
drafts, the seal, the audit chains. But a reader holding that report
still had to work out the verdict themselves. The rules were never
written down anywhere the reader could reach them.

So "closed" was a thing you could *show* and not a thing you could
*ask*. An agent holding the MCP tools - which could run a loop, read
the report, replay the ledger, audit the decisions, seal them, list
the deliverables and the files - could not answer the one question
the project exists to answer: **is this closed?**

And the rules were in two heads, not one. The CLI and the MCP server
each grew their own way of asking, which means they were free to
disagree, and a user who got a different answer depending on which
surface they used would have had no way to tell which one was right.

So this gate watches the *verdict*:

* the tool is advertised in the MCP catalogue;
* a run that closed with nothing waiting is **closed**;
* a run that left work for a person is **not closed**, and the verdict
  says **which** part is open - not just "no";
* an empty ledger is **not** a closed loop. It is a loop that never
  ran, and the two are never spelled the same way;
* the MCP answer and the CLI answer are **the same answer**;
* and the caller gets the reasons as **data**, not only as prose.

And the gate proves it can fail: the shared verdict is broken on
purpose - it always says "closed" - and the gate has to notice that
a machine with work still waiting now reports itself as finished.
"""

from __future__ import annotations

import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes.
CLOSURE_OBSERVATIONS = (
    "the tool is advertised in the MCP catalogue",
    "a finished run with nothing waiting is closed",
    "work left for a person is not closed, and the reason is named",
    "an empty ledger is not a closed loop",
    "the MCP answer and the CLI answer are the same answer",
    "the caller gets the reasons as data",
)


def _closed_rows() -> List[Dict[str, Any]]:
    """Ledger rows of a run that closed and left nothing behind."""
    return [
        {
            "role": "closure",
            "run_id": "R-selftestclo",
            "task_id": "T-selftestclo",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
            "verdict": "pass",
            "decision": "accept",
            "scores": [{"id": "c1", "pass": True}],
        },
        {
            "role": "run_end",
            "run_id": "R-selftestclo",
            "task_id": "T-selftestclo",
            "iteration": 1,
            "ts": "2026-01-01T00:00:01+00:00",
            "seq": 2,
            "status": "closed",
            "closed": True,
            "deliverable_ok": True,
            "decision": "accept",
            "error": "",
            "ticket_id": "",
            "goal": "写一份结论",
            "title": "写一份结论",
            "iterations": 1,
            "tokens": 120,
            "artifact_path": "",
            "files": [],
        },
    ]


def _open_rows() -> List[Dict[str, Any]]:
    """The same run, but it escalated: a person has to step in."""
    rows = _closed_rows()
    for row in rows:
        if row.get("role") == "closure":
            row["verdict"] = "fail"
            row["decision"] = "escalate"
            row["scores"] = [{"id": "c1", "pass": False}]
        if row.get("role") == "run_end":
            row["status"] = "escalated"
            row["closed"] = False
            row["deliverable_ok"] = False
            row["ticket_id"] = "T-selftestclo"
    return rows


def _waiting_ticket() -> Dict[str, Any]:
    return {
        "id": "T-selftestclo",
        "kind": "closure_failed",
        "state": "waiting_human",
        "created_at": "2026-01-01T00:00:02+00:00",
        "resolved_at": None,
        "decision": None,
        "note": "",
        "payload": {"run_id": "R-selftestclo"},
    }


class _FakeLedger:
    """A ledger that holds exactly the rows the gate wrote."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = list(rows)

    def entries(self) -> List[Dict[str, Any]]:
        return list(self._rows)


class _FakeTickets:
    """The ticket queue the gate wants the verdict to read."""

    def __init__(self, tickets: List[Dict[str, Any]]) -> None:
        self._tickets = list(tickets)

    def list(self) -> List[Dict[str, Any]]:
        return list(self._tickets)


class _FakeEngine:
    """The smallest thing the MCP tool needs from an engine."""

    def __init__(
        self, rows: List[Dict[str, Any]], tickets: List[Dict[str, Any]]
    ) -> None:
        self.ledger = _FakeLedger(rows)
        self.tickets = _FakeTickets(tickets)


def _call_mcp(
    rows: List[Dict[str, Any]], tickets: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Call the MCP tool the way a caller would.

    The tool reads the ledger and the queue of the machine it runs
    on. The gate points that machine at the records it wrote, so the
    call goes through the real code path - the tool really reads the
    rows, rather than being handed the answer.
    """
    import mas.mcp_server as mcp_module

    real_engine = mcp_module._engine

    def _fake(_config, team=None):
        return _FakeEngine(rows, tickets)

    try:
        mcp_module._engine = _fake
        return mcp_module.handle_tool("mas_closure", {"as_markdown": False})
    finally:
        mcp_module._engine = real_engine


def _closure_observations() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.mcp_server import TOOLS
    from mas.report import closure_verdict_for

    closed_rows = _closed_rows()
    open_rows = _open_rows()
    tickets = [_waiting_ticket()]

    names = [str(tool.get("name") or "") for tool in TOOLS]

    closed_payload = _call_mcp(closed_rows, [])
    open_payload = _call_mcp(open_rows, tickets)
    empty_payload = _call_mcp([], [])

    # The CLI answer, from the same implementation.
    shared_closed = closure_verdict_for(closed_rows, [], [])
    shared_open = closure_verdict_for(open_rows, tickets, [])

    return {
        "tool_names": names,
        "closed_payload": closed_payload,
        "open_payload": open_payload,
        "empty_payload": empty_payload,
        "shared_closed": shared_closed,
        "shared_open": shared_open,
    }


def _closure_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}
    names = list(facts.get("tool_names") or [])
    closed = facts.get("closed_payload") or {}
    opened = facts.get("open_payload") or {}
    empty = facts.get("empty_payload") or {}

    verdicts["the tool is advertised in the MCP catalogue"] = (
        "mas_closure" in names
    )

    verdicts["a finished run with nothing waiting is closed"] = bool(
        closed.get("ok") is True and closed.get("empty") is False
    )

    reasons = opened.get("reasons") if isinstance(opened, dict) else None
    if not isinstance(reasons, list):
        reasons = []
    reason_keys = {
        str(item.get("reason") or "") for item in reasons if isinstance(item, dict)
    }
    verdicts["work left for a person is not closed, and the reason is named"] = bool(
        opened.get("ok") is False
        and opened.get("empty") is False
        and reason_keys
        and ("tickets_waiting" in reason_keys or "runs_not_closed" in reason_keys)
    )

    # The empty ledger is the one case where claiming closure would be
    # a lie: nothing ran, so nothing closed.
    verdicts["an empty ledger is not a closed loop"] = bool(
        empty.get("ok") is False and empty.get("empty") is True
    )

    shared_closed = facts.get("shared_closed") or {}
    shared_open = facts.get("shared_open") or {}
    verdicts["the MCP answer and the CLI answer are the same answer"] = bool(
        shared_closed.get("ok") == closed.get("ok")
        and shared_closed.get("empty") == closed.get("empty")
        and shared_open.get("ok") == opened.get("ok")
        and [r.get("reason") for r in (shared_open.get("reasons") or [])]
        == [r.get("reason") for r in reasons]
    )

    verdicts["the caller gets the reasons as data"] = bool(
        isinstance(reasons, list)
        and reasons
        and all(
            isinstance(item, dict) and "reason" in item and "count" in item
            for item in reasons
        )
    )
    return verdicts


def _closure_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same verdict, read a second way, sharing no code with the first.

    This pass does not call the shared verdict at all. It asks the
    tool what it returns and reads the answer as a reader would - so a
    broken verdict that always says "closed" cannot make both passes
    agree on a wrong answer.
    """
    verdicts: Dict[str, bool] = {}
    closed = facts.get("closed_payload") or {}
    opened = facts.get("open_payload") or {}
    empty = facts.get("empty_payload") or {}

    verdicts["the tool is advertised in the MCP catalogue"] = bool(
        "mas_closure" in list(facts.get("tool_names") or [])
    )

    verdicts["a finished run with nothing waiting is closed"] = bool(
        closed.get("ok") is True
    )

    reasons = opened.get("reasons") if isinstance(opened, dict) else None
    if not isinstance(reasons, list):
        reasons = []
    verdicts["work left for a person is not closed, and the reason is named"] = bool(
        opened.get("ok") is False and len(reasons) >= 1
    )

    verdicts["an empty ledger is not a closed loop"] = bool(
        empty.get("ok") is False and empty.get("empty") is True
    )

    # Read the agreement differently: the tool's own answer and the
    # shared answer have to hold the same verdict and the same
    # reasons - compared field by field, not by identity.
    shared_closed = facts.get("shared_closed") or {}
    shared_open = facts.get("shared_open") or {}
    verdicts["the MCP answer and the CLI answer are the same answer"] = bool(
        shared_closed.get("ok") == closed.get("ok")
        and shared_open.get("ok") == opened.get("ok")
        and str(shared_open.get("headline") or "") == str(opened.get("headline") or "")
    )

    verdicts["the caller gets the reasons as data"] = bool(
        reasons and all(isinstance(item, dict) for item in reasons)
    )
    return verdicts


def check_the_closure_verdict_reaches_both_surfaces(
    iterations: int = 3,
) -> Dict[str, Any]:
    """Somebody has to say whether the work is closed - and it can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        tmp = tempfile.mkdtemp(prefix="mas-selftest-closure-")
        try:
            try:
                facts = _closure_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the closure check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _closure_verdicts(facts)
            independent = _closure_verdicts_independent(facts)
            for label in CLOSURE_OBSERVATIONS:
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

            # The gate proves it can fail: the shared verdict is broken
            # on purpose - it always says "closed" - and the gate has
            # to notice that a machine with work still waiting now
            # reports itself as finished.
            import mas.mcp_server as mcp_module
            import mas.report as report_module

            real_for = report_module.closure_verdict_for
            real_imported = mcp_module.closure_verdict_for

            def _always_closed(_rows, _tickets=None, _drafts=None):
                return {
                    "ok": True,
                    "empty": False,
                    "headline": "闭合：没有留下任何待办。",
                    "reasons": [],
                    "counts": {},
                    "closure_rate": 1.0,
                    "open_threads": [],
                }

            try:
                report_module.closure_verdict_for = _always_closed
                mcp_module.closure_verdict_for = _always_closed
                broken: Dict[str, bool] = {}
                try:
                    broken = _closure_verdicts(_closure_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.closure_verdict_for = real_for
                mcp_module.closure_verdict_for = real_imported
            if broken.get(
                "work left for a person is not closed, and the reason is named"
            ):
                failures.append(
                    "round %d: the gate did not notice that the verdict always "
                    "says closed - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
            if broken.get("an empty ledger is not a closed loop"):
                failures.append(
                    "round %d: the gate did not notice that an empty ledger is "
                    "reported as closed" % (round_index + 1)
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return {
        "name": "closure_verdict",
        "title": "\u95ed\u5408\u7684\u5224\u5b9a\u540c\u65f6\u5230\u8fbe\u547d\u4ee4\u884c\u548c MCP",
        "skipped": False,
        "invariant": (
            "\u80fd\u56de\u7b54\u201c\u8fd9\u4efb\u52a1\u95ed\u5408\u4e86\u5417\u201d\uff0c"
            "\u4e14\u672a\u95ed\u5408\u65f6\u6307\u51fa\u662f\u54ea\u4e00\u683c\u65ad\u4e86"
        ),
        "detail": "\u5224\u5b9a\u53ea\u8bfb\u8d26\u672c\u3001\u5de5\u5355\u548c\u8349\u7a3f\uff0c\u4e0d\u8dd1\u65b0\u7684\u95ed\u73af",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_closure_verdict_reaches_both_surfaces.CHECK_NAME = "closure_verdict"  # type: ignore[attr-defined]

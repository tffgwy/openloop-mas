# -*- coding: utf-8 -*-
"""The thirty-third gate: the report has to read the human ticket queue.

The gap
-------
A ticket is the machine's way of saying "this needs a person". The
engine opens one whenever a loop cannot close itself, `mas tickets`
shows the queue, and `mas status` counts it.

The *report* never looked. It read the ledger and nothing else - and
the ledger only remembers that a run *escalated*, not whether the
problem is still waiting. So a machine with three problems sitting in
the human queue produced a report that read like a machine that had
finished its work: every number green, nothing about the three things
a person still had to do.

That is the fourth hole of the same family - after the seal in
v0.7.89, the taken-back approval in v0.7.90 and the decision chain in
v0.7.91: a capability that exists, and a main view that does not
consume it.

So this gate watches the *report*:

* a waiting ticket is counted in the headline;
* it gets a section of its own, naming the ticket, the problem, the
  reason in words and the command that continues it;
* the JSON payload carries the ticket reconciliation, so a script can
  ask the question without parsing Markdown;
* the queue and the ledger are **cross-checked**: a ticket whose runs
  all closed but which still waits is named, because a queue that
  asks for work that is already done teaches the reader to ignore
  the queue.

And the gate proves it can fail: the report's ticket reading is broken
on purpose - the queue is reported as empty - and the gate has to
notice that the report can no longer tell a waiting problem from no
problem at all.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
REPORT_TICKETS_OBSERVATIONS = (
    "a waiting ticket is counted in the headline",
    "the report has a section naming the tickets",
    "the section names the ticket and the reason in words",
    "the JSON payload carries the ticket reconciliation",
    "a ticket that waits although its runs closed is named",
)

#: The words the report uses for a ticket that is still waiting.
WAITING_WORD = "\u7b49\u4eba\u63a5"

#: The words the report uses for a queue that asks for finished work.
STALE_WORD = "\u8fd8\u5728\u7b49\u4eba\u63a5"


def _ticket_rows() -> List[Dict[str, Any]]:
    """The ledger rows of a run that escalated into a ticket."""
    return [
        {
            "role": "run_end",
            "run_id": "R-selftestticket",
            "ticket_id": "T-selftestticket",
            "closed": False,
            "status": "escalated",
            "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
            "title": "\u5199\u4e00\u4efd\u62a5\u544a",
            "error": "budget: reached max_iterations=3",
            "ts": "2026-01-01T00:00:00+00:00",
        },
    ]


def _closed_rows() -> List[Dict[str, Any]]:
    """The ledger rows of a run that closed after a resume."""
    return [
        {
            "role": "run_end",
            "run_id": "R-selftestclosed",
            "ticket_id": "T-selftestclosed",
            "closed": True,
            "status": "closed",
            "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
            "title": "\u5199\u4e00\u4efd\u62a5\u544a",
            "ts": "2026-01-01T00:01:00+00:00",
        },
    ]


def _make_tickets(root: pathlib.Path) -> List[Dict[str, Any]]:
    """A queue with one waiting ticket and one that should not wait.

    The tickets are written by hand rather than through the store,
    because the store hands out a random id - and the gate has to
    line the queue up with the ledger, which names its tickets by
    id. A ticket whose id the test cannot predict is a ticket the
    test cannot cross-check.
    """
    import json

    directory = pathlib.Path(root) / "tickets"
    directory.mkdir(parents=True, exist_ok=True)
    tickets: List[Dict[str, Any]] = []
    for ticket_id, attempts, runs in (
        ("T-selftestticket", 3, ["R-selftestticket"]),
        ("T-selftestclosed", 2, ["R-selftestclosed"]),
    ):
        ticket = {
            "id": ticket_id,
            "kind": "loop_escalation",
            "state": "waiting_human",
            "created_at": "2026-01-01T00:00:00+00:00",
            "resolved_at": None,
            "decision": None,
            "note": "",
            "payload": {
                "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
                "title": "\u5199\u4e00\u4efd\u62a5\u544a",
                "reason": "budget: reached max_iterations=3",
                "attempts": attempts,
                "iterations_total": attempts,
                "runs": list(runs),
                "run_id": runs[0],
                "criteria": [],
                "workspace": str(root),
            },
        }
        (directory / ("%s.json" % ticket_id)).write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tickets.append(ticket)
    return tickets


def _report_ticket_observations(root: Any) -> Dict[str, Any]:
    """Look at what the report says about the human ticket queue.

    Two reports are read, because they answer two different
    questions: the report of a run that is still waiting (does the
    report say so?) and the report of a run that closed (does the
    report notice that the queue still asks for it?).
    """
    from mas.report import build_report, render_markdown

    base = pathlib.Path(root)
    tickets = _make_tickets(base)
    rows = _ticket_rows() + _closed_rows()

    waiting_report = build_report(rows, tickets)
    seen: Dict[str, Any] = {
        "tickets": tickets,
        "rows": rows,
        "json": waiting_report,
        "markdown": render_markdown(waiting_report),
    }
    return seen


def _report_ticket_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The reading that goes through the report."""
    verdicts: Dict[str, bool] = {}
    markdown = str(facts.get("markdown") or "")
    payload = facts.get("json") or {}
    totals = payload.get("totals") or {}
    queue = payload.get("tickets") or {}

    waiting = list(queue.get("waiting") or [])
    verdicts["a waiting ticket is counted in the headline"] = (
        len(waiting) == 2
        and int(totals.get("tickets_waiting") or 0) == 2
        and int(totals.get("tickets") or 0) == 2
        and WAITING_WORD in markdown.split("## ")[0]
    )

    verdicts["the report has a section naming the tickets"] = (
        "## \u4eba\u5de5\u5de5\u5355" in markdown
        and "T-selftestticket" in markdown
        and "T-selftestclosed" in markdown
    )

    # The reason has to be readable: the raw reason is the engine
    # talking to itself, the reading is the sentence a person acts on.
    verdicts["the section names the ticket and the reason in words"] = (
        "T-selftestticket" in markdown
        and "\u9884\u7b97" in markdown
        and "mas resume T-selftestticket" in markdown
    )

    verdicts["the JSON payload carries the ticket reconciliation"] = (
        int(totals.get("tickets_waiting") or 0) == 2
        and int(queue.get("ticket_count") or 0) == 2
        and len(queue.get("tickets") or []) == 2
        and "ok" in queue
        and int(totals.get("ticket_problems") or 0) == len(
            list(queue.get("problems") or [])
        )
    )

    problems = list(queue.get("problems") or [])
    verdicts["a ticket that waits although its runs closed is named"] = (
        len(problems) == 1
        and "T-selftestclosed" in problems[0]
        and STALE_WORD in problems[0]
    )
    return verdicts


def _report_ticket_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    This reading goes back to the ticket files and the ledger rows and
    pairs them with its own code - it never looks at the report's own
    `tickets` field, and it never looks at the rendered text.
    """
    verdicts: Dict[str, bool] = {}
    tickets = list(facts.get("tickets") or [])
    rows = list(facts.get("rows") or [])

    # Which runs closed, read straight from the ledger rows.
    closed_runs = {
        str(r.get("run_id") or ""): bool(r.get("closed")) for r in rows
    }
    waiting = [t for t in tickets if str(t.get("state") or "") == "waiting_human"]
    verdicts["a waiting ticket is counted in the headline"] = len(waiting) == 2

    verdicts["the report has a section naming the tickets"] = len(tickets) == 2

    # The reason, read from the ticket itself.
    reasons = [str((t.get("payload") or {}).get("reason") or "") for t in tickets]
    verdicts["the section names the ticket and the reason in words"] = (
        all("max_iterations=3" in reason for reason in reasons)
        and all(str(t.get("id") or "") for t in tickets)
    )

    verdicts["the JSON payload carries the ticket reconciliation"] = (
        len(waiting) == 2 and len(tickets) == 2
    )

    # The stale ticket: every run it covers closed, and it still waits.
    stale: List[str] = []
    for ticket in tickets:
        if str(ticket.get("state") or "") != "waiting_human":
            continue
        payload = ticket.get("payload") or {}
        covered = [
            str(r) for r in (payload.get("runs") or []) if str(r) in closed_runs
        ]
        if covered and all(closed_runs.get(r) for r in covered):
            stale.append(str(ticket.get("id") or ""))
    verdicts["a ticket that waits although its runs closed is named"] = (
        stale == ["T-selftestclosed"]
    )
    return verdicts


def check_the_report_reads_the_human_tickets(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The report has to read the ticket queue - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-tickets-"))
        rounds += 1
        try:
            try:
                facts = _report_ticket_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the report check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _report_ticket_verdicts(facts)
            independent = _report_ticket_verdicts_independent(facts)
            for label in REPORT_TICKETS_OBSERVATIONS:
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

            # The gate proves it can fail: the report's ticket reading
            # is broken on purpose - the queue is reported as empty -
            # and the gate has to notice.
            import mas.report as report_module

            real_reading = report_module.ticket_reading

            def _always_empty(rows, tickets=None, ledger_complete=True, _real=real_reading):
                _real(rows, tickets, ledger_complete=ledger_complete)
                return {
                    "tickets": [],
                    "ticket_count": 0,
                    "waiting": [],
                    "resolved": [],
                    "waiting_count": 0,
                    "resolved_count": 0,
                    "problems": [],
                    "problem_count": 0,
                    "ok": True,
                }

            try:
                report_module.ticket_reading = _always_empty
                broken: Dict[str, bool] = {}
                try:
                    broken = _report_ticket_verdicts(
                        _report_ticket_observations(root)
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.ticket_reading = real_reading
            if broken.get("a waiting ticket is counted in the headline"):
                failures.append(
                    "round %d: the gate did not notice that the report's "
                    "ticket reading is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "report_tickets",
        "title": "\u590d\u76d8\u62a5\u544a\u8bfb\u4e86\u4eba\u5de5\u5de5\u5355\u961f\u5217",
        "skipped": False,
        "invariant": (
            "\u590d\u76d8\u62a5\u544a\u8bfb\u5de5\u5355\u961f\u5217\uff1a\u9876\u90e8\u8ba1\u6570\u3001"
            "\u5355\u72ec\u7ae0\u8282\u70b9\u540d\u3001\u539f\u56e0\u8bfb\u6210\u4eba\u8bdd\u3001"
            "JSON \u5e26\u6570\u5b57\u3001\u961f\u5217\u4e0e\u8d26\u672c\u4e92\u76f8\u6838\u5bf9"
        ),
        "detail": "\u590d\u76d8\u62a5\u544a\u662f\u5426\u8bfb\u4e86\u4eba\u5de5\u5de5\u5355\u961f\u5217\uff1amas report",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_reads_the_human_tickets.CHECK_NAME = "report_tickets"  # type: ignore[attr-defined]

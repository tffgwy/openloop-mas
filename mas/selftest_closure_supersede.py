# -*- coding: utf-8 -*-
"""The thirty-ninth gate: one report must not hold two answers.

The gap
-------
v0.7.97 put the closure verdict in one place, so the CLI and the MCP
server could no longer disagree with each other. But the verdict was
still counted at the *wrong grain*, and it disagreed with the report
it was reading.

The report groups runs into **threads** - one thread per problem. A
problem is closed when its **last** attempt closed it, because a later
attempt supersedes an earlier one. That rule was already there, and
it was right.

The verdict, though, counted ``totals["not_closed"]`` - the number of
runs that did not close. Every failed attempt, including the ones a
later attempt had replaced. So one report could say both of these at
the same time:

    threads:    [('T-1', closed=True)]     <- the problem is closed
    VERDICT:    ok=False ['runs_not_closed'] <- something is still waiting

Two answers to one question, from one report. And the consequence was
worse than a wording problem: **a problem that failed and then
succeeded could never be reported as closed**. Fail, resume, succeed -
the core workflow of this project - could not reach ``ok=True``. The
queue of open problems could only ever grow, and a queue that never
empties teaches the reader to ignore it.

The fix is not to delete the failed attempt - the history stays - but
to count the failed attempts that belong to a problem that is *still
open*. The two grains are kept apart on purpose:

* ``not_closed``            - every failed attempt. History.
* ``not_closed_open``       - failed attempts still waiting. Work in progress.
* ``not_closed_superseded`` - failed attempts a later attempt replaced.
* ``problem_closure_rate``  - closed problems / all problems.

So this gate watches the *grain*:

* a problem that failed and then succeeded is **closed**;
* a problem whose last attempt failed is **not closed**, and says why;
* a failed attempt that was superseded is counted as history, not as
  work in progress;
* the report and the verdict agree - they cannot answer the same
  question differently;
* the two rates name their grain, so a reader is never left holding
  a "closed" verdict next to an unexplained 50%;

And the gate proves it can fail: the verdict is broken on purpose - it
is made to count every failure as waiting again - and the gate has to
notice that a resumed problem now reports itself as unfinished.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: The observations the gate makes.
SUPERSEDE_OBSERVATIONS = (
    "a failure that was later superseded does not hold the loop open",
    "a problem whose last attempt failed is still open",
    "the report and the verdict agree about what is open",
    "the superseded failure is still counted as history",
    "the two closure rates name their grain",
    "the CLI and the MCP server give the same answer",
)


def _rows(spec: List[Any]) -> List[Dict[str, Any]]:
    """Ledger rows for a list of ``(run_id, closed, ticket_id)``."""
    out: List[Dict[str, Any]] = []
    seq = 0
    for run_id, closed, ticket_id in spec:
        seq += 1
        out.append(
            {
                "role": "closure",
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "iteration": 1,
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": seq,
                "verdict": "pass" if closed else "fail",
                "decision": "accept" if closed else "escalate",
                "scores": [{"id": "c1", "pass": closed}],
            }
        )
        seq += 1
        out.append(
            {
                "role": "run_end",
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "iteration": 1,
                "ts": "2026-01-01T00:00:01+00:00",
                "seq": seq,
                "status": "closed" if closed else "escalated",
                "closed": closed,
                "deliverable_ok": closed,
                "decision": "accept" if closed else "escalate",
                "error": "",
                "ticket_id": ticket_id,
                "goal": "把事情做完",
                "title": "把事情做完",
                "iterations": 1,
                "tokens": 10,
                "artifact_path": "",
                "files": [],
            }
        )
    return out


#: The failure-then-success case: one problem, two attempts, closed.
SUPERSEDED_CASE = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]

#: The still-open case: one problem, two attempts, the last one failed.
STILL_OPEN_CASE = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


def _closure_observations() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.report import build_report, closure_verdict, closure_verdict_for

    superseded_rows = _rows(SUPERSEDED_CASE)
    open_rows = _rows(STILL_OPEN_CASE)

    superseded_report = build_report(superseded_rows, [], [])
    open_report = build_report(open_rows, [], [])

    return {
        "superseded_report": superseded_report,
        "open_report": open_report,
        "superseded_verdict": closure_verdict(superseded_report),
        "open_verdict": closure_verdict(open_report),
        "shared_superseded": closure_verdict_for(superseded_rows, [], []),
        "shared_open": closure_verdict_for(open_rows, [], []),
    }


def _supersede_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}

    superseded = facts.get("superseded_verdict") or {}
    opened = facts.get("open_verdict") or {}
    superseded_report = facts.get("superseded_report") or {}
    open_report = facts.get("open_report") or {}

    superseded_threads = superseded_report.get("threads") or []
    open_threads = open_report.get("threads") or []

    # The core of the gate: the report says the problem is closed and
    # the verdict has to say the same thing.
    report_says_closed = bool(
        superseded_threads
        and all(bool(t.get("closed")) for t in superseded_threads)
    )
    verdict_says_closed = bool(superseded.get("ok") is True)
    verdicts["a failure that was later superseded does not hold the loop open"] = bool(
        report_says_closed and verdict_says_closed
    )

    # And the other direction: an open problem is still open.
    report_says_open = bool(
        open_threads and all(not bool(t.get("closed")) for t in open_threads)
    )
    verdicts["a problem whose last attempt failed is still open"] = bool(
        report_says_open and opened.get("ok") is False
    )

    # The report and the verdict cannot answer the same question
    # differently. Both grains are compared: the threads and the
    # reason list.
    open_reason_keys = {
        str(item.get("reason") or "")
        for item in (opened.get("reasons") or [])
        if isinstance(item, dict)
    }
    verdicts["the report and the verdict agree about what is open"] = bool(
        report_says_open
        and opened.get("ok") is False
        and open_reason_keys
        and "problems_open" in open_reason_keys
        and not superseded.get("reasons")
    )

    # The failure is not erased. It is still counted, as history.
    counts = superseded.get("counts") or {}
    totals = superseded_report.get("totals") or {}
    verdicts["the superseded failure is still counted as history"] = bool(
        int(counts.get("not_closed") or 0) == 1
        and int(counts.get("not_closed_superseded") or 0) == 1
        and int(counts.get("not_closed_open") or 0) == 0
        and int(totals.get("not_closed") or 0) == 1
    )

    # Two rates, two grains, both named.
    verdicts["the two closure rates name their grain"] = bool(
        superseded.get("closure_rate") is not None
        and superseded.get("problem_closure_rate") is not None
        and abs(float(superseded.get("closure_rate") or 0.0) - 0.5) < 1e-6
        and abs(float(superseded.get("problem_closure_rate") or 0.0) - 1.0) < 1e-6
    )

    # The two surfaces still agree - the v0.7.97 promise has to survive
    # the change of grain.
    shared_superseded = facts.get("shared_superseded") or {}
    shared_open = facts.get("shared_open") or {}
    verdicts["the CLI and the MCP server give the same answer"] = bool(
        shared_superseded.get("ok") == superseded.get("ok")
        and shared_open.get("ok") == opened.get("ok")
        and str(shared_superseded.get("headline") or "")
        == str(superseded.get("headline") or "")
        and str(shared_open.get("headline") or "")
        == str(opened.get("headline") or "")
    )
    return verdicts


def _supersede_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same verdict, read a second way, sharing no code with the first.

    This pass does not call the shared verdict at all. It reads the
    report's threads and the counts, and works the answer out by
    hand - so a verdict that always says "closed" cannot make both
    passes agree on a wrong answer.
    """
    verdicts: Dict[str, bool] = {}

    superseded_report = facts.get("superseded_report") or {}
    open_report = facts.get("open_report") or {}
    superseded = facts.get("superseded_verdict") or {}
    opened = facts.get("open_verdict") or {}

    sup_threads = superseded_report.get("threads") or []
    open_threads = open_report.get("threads") or []

    # Read the grain out of the threads by hand: a problem is closed
    # when its last attempt closed it.
    def _thread_closed(threads: List[Dict[str, Any]]) -> bool:
        return bool(threads) and all(bool(t.get("closed")) for t in threads)

    sup_closed = _thread_closed(sup_threads)
    open_closed = _thread_closed(open_threads)

    verdicts["a failure that was later superseded does not hold the loop open"] = bool(
        sup_closed is True
        and superseded.get("ok") is True
        and superseded.get("empty") is False
    )
    verdicts["a problem whose last attempt failed is still open"] = bool(
        open_closed is False
        and opened.get("ok") is False
        and opened.get("empty") is False
    )

    # The open case has to name the open problem.
    reasons = opened.get("reasons") or []
    keys = {str(r.get("reason") or "") for r in reasons if isinstance(r, dict)}
    verdicts["the report and the verdict agree about what is open"] = bool(
        open_closed is False
        and opened.get("ok") is False
        and "problems_open" in keys
        and not (superseded.get("reasons") or [])
    )

    # The counts, read out of the report's totals rather than out of
    # the verdict.
    totals = superseded_report.get("totals") or {}
    verdicts["the superseded failure is still counted as history"] = bool(
        int(totals.get("not_closed") or 0) == 1
        and int(totals.get("not_closed_superseded") or 0) == 1
        and int(totals.get("not_closed_open") or 0) == 0
        and int(totals.get("problems_open") or 0) == 0
    )

    verdicts["the two closure rates name their grain"] = bool(
        totals.get("closure_rate") is not None
        and totals.get("problem_closure_rate") is not None
        and int(totals.get("runs") or 0) == 2
        and int(totals.get("closed") or 0) == 1
    )

    shared_superseded = facts.get("shared_superseded") or {}
    shared_open = facts.get("shared_open") or {}
    verdicts["the CLI and the MCP server give the same answer"] = bool(
        shared_superseded.get("ok") == superseded.get("ok")
        and shared_open.get("ok") == opened.get("ok")
        and shared_superseded.get("empty") == superseded.get("empty")
        and shared_open.get("empty") == opened.get("empty")
    )
    return verdicts


def check_the_report_and_the_verdict_agree_on_the_grain(
    iterations: int = 5,
) -> Dict[str, Any]:
    """One report must not hold two answers - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _closure_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the closure check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _supersede_verdicts(facts)
            independent = _supersede_verdicts_independent(facts)
            for label in SUPERSEDE_OBSERVATIONS:
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

            # The gate proves it can fail: the verdict is broken on
            # purpose - every failed attempt counts as waiting again -
            # and the gate has to notice that a resumed problem now
            # reports itself as unfinished.
            import mas.report as report_module

            real_verdict = report_module.closure_verdict

            def _count_every_failure_as_waiting(
                report: Dict[str, Any], _real=real_verdict
            ) -> Dict[str, Any]:
                verdict = _real(report)
                totals = report.get("totals") or {}
                # The old grain: every failure counts, superseded or
                # not.
                verdict["ok"] = False
                verdict["reasons"] = [
                    {
                        "reason": "runs_not_closed",
                        "count": int(totals.get("not_closed") or 0),
                        "detail": "有 %d 次运行没有闭合"
                        % int(totals.get("not_closed") or 0),
                        "level": "fail",
                    }
                ]
                verdict["headline"] = "未闭合：有 %d 次运行没有闭合。" % int(
                    totals.get("not_closed") or 0
                )
                return verdict

            try:
                report_module.closure_verdict = _count_every_failure_as_waiting
                broken: Dict[str, bool] = {}
                try:
                    broken = _supersede_verdicts(_closure_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.closure_verdict = real_verdict

            if broken.get(
                "a failure that was later superseded does not hold the loop open"
            ):
                failures.append(
                    "round %d: the gate did not notice that a superseded "
                    "failure holds the loop open - it cannot fail, so it does "
                    "not watch" % (round_index + 1)
                )
            if broken.get("the report and the verdict agree about what is open"):
                failures.append(
                    "round %d: the gate did not notice that the report and the "
                    "verdict now answer differently" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "closure_supersede",
        "title": "报告和判定用同一个粒度说话",
        "skipped": False,
        "invariant": (
            "闭合的粒度是问题不是尝试：被成功尝试取代的失败不占着队列，"
            "报告和判定不能给出相反的答案"
        ),
        "detail": "查报告里的问题粒度和闭合判定是不是同一件事",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_and_the_verdict_agree_on_the_grain.CHECK_NAME = "closure_supersede"  # type: ignore[attr-defined]

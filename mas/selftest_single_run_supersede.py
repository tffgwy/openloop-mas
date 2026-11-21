# -*- coding: utf-8 -*-
"""The forty-fifth gate: the queue decides, not the ledger alone.

The gap
-------
The report, the status page and the closure verdict all agree on the
grain - a failed attempt that a later attempt replaced is *history*,
not work in progress. v0.8.3 made the run detail say the same thing.

But the rule had a blind spot, and it was the most common way to look
at a report: **one run at a time**.

    mas report --run-id R1

That report holds one run in its ledger - the failed one. The attempt
that actually closed the problem, R2, happened *later*, in another
run, and it is not in this ledger. So the report could not see it.

And so the same page said three different things at once:

    尝试总数：1　已闭环：0　仍在等：1　尝试闭环率：0%
    | 把事情做完 | 待处理 |          <- the problem is waiting
    | T1 | 已解决 |                 <- the queue says it is done
    - 人工工单：`T1`（已不需要续跑）  <- and the detail says so too

The headline counted the failure as work in progress, the overview
table called the problem "待处理", the ticket table called it
"已解决", and the detail of the run said it needed no resuming. Four
lines, three answers - and the reader of a single-run report has no
way to reconcile them, because the evidence that would settle it is
in a report they never opened.

The root cause is a question asked of the wrong authority. "Is anyone
still holding this problem?" is a question about the **human queue**,
and the queue answered it. The verdict asked the **ledger**, and the
ledger - for one run - holds nothing but the failure.

The fix
-------
The queue is the authority, and it already carries the evidence. When
an attempt closes a problem, the engine records **which** attempt
closed it on the ticket (``closed_run_id``). A resolved ticket that
names that run is proof the problem was finished - so the failed
attempt it covers is history, and the report says so, with the
replacing attempt named.

The proof has to be a *successful attempt*, and that is the part that
matters. A ticket a human closed by hand (``mas_ticket_resolve``)
carries no ``closed_run_id`` - nobody recorded a successful attempt -
so the failure it covers stays **open**. The human marked the ticket
handled; the machine still has nothing to show for it. Reporting that
as finished would be the tool lying in the direction that hides work.

And "the queue was not read at all" is still not "the queue is
empty": a caller that hands in no queue has not looked, so it cannot
retire a ticket, and the failure keeps its resume pointer.

This gate watches:

* a single-run report says the problem is finished when the queue
  says a later attempt closed it;
* the headline, the overview table, the ticket table and the run
  detail all say the same thing about that run;
* the detail names the attempt that replaced it;
* a failure whose ticket was closed **by hand** stays open;
* a failure whose ticket was never read keeps its resume pointer;
* and the gate proves it can fail: the queue's word is ignored on
  purpose, and the gate has to notice the page contradicts itself
  again.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: The observations the gate makes.
SINGLE_RUN_SUPERSEDE_OBSERVATIONS = (
    "a single-run report says the problem is finished when the queue says so",
    "the four lines of the page agree about that run",
    "the detail names the attempt that replaced it",
    "a failure whose ticket was closed by hand stays open",
    "a failure whose ticket was never read keeps its resume pointer",
    "the closure verdict reaches the same answer",
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
                "error": ""
                if closed
                else "stagnation: the deliverable did not change for 3 consecutive attempts (stagnation_limit=3)",
                "ticket_id": ticket_id,
                "goal": "\u628a\u4e8b\u60c5\u505a\u5b8c",
                "title": "\u628a\u4e8b\u60c5\u505a\u5b8c",
                "iterations": 1,
                "tokens": 10,
                "artifact_path": "",
                "files": [],
            }
        )
    return out


#: One failed attempt, on a ticket a later attempt closed. Only the
#: failed attempt is in this ledger - that is the whole point.
SINGLE_RUN_CASE = [("R-fail", False, "T-one")]


def _ticket(
    ticket_id: str,
    state: str,
    closed_run_id: str = "",
    runs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """One ticket in the queue.

    ``closed_run_id`` is the evidence that an **attempt** closed the
    problem. A ticket a person closed by hand has none.
    """
    payload: Dict[str, Any] = {
        "goal": "\u628a\u4e8b\u60c5\u505a\u5b8c",
        "title": "\u628a\u4e8b\u60c5\u505a\u5b8c",
        "reason": "stagnation: the deliverable did not change",
        "runs": list(runs or []),
    }
    if closed_run_id:
        payload["closed_run_id"] = closed_run_id
    entry: Dict[str, Any] = {
        "id": ticket_id,
        "kind": "loop",
        "state": state,
        "created_at": "2026-01-01T00:00:00+00:00",
        "payload": payload,
    }
    if state == "resolved":
        entry["resolved_at"] = "2026-01-01T02:00:00+00:00"
        entry["decision"] = "accept"
        entry["note"] = "done"
    return entry


def _surfaces() -> Dict[str, Any]:
    """The report of one run, in the states a reader can meet it."""
    from mas.report import build_report, render_markdown

    rows = _rows(SINGLE_RUN_CASE)

    # The queue says a later attempt closed the problem.
    closed_by_attempt = build_report(
        rows, [_ticket("T-one", "resolved", closed_run_id="R-later", runs=["R-later"])]
    )
    closed_md = render_markdown(closed_by_attempt)

    # A person closed the ticket by hand: no successful attempt, so
    # the failure is still work in progress.
    closed_by_hand = build_report(rows, [_ticket("T-one", "resolved", runs=["R-later"])])
    hand_md = render_markdown(closed_by_hand)

    # The queue was never read: the state of the ticket is unknown.
    unread = build_report(rows, None)
    unread_md = render_markdown(unread)

    # The ticket is still waiting: an ordinary pending problem.
    waiting = build_report(rows, [_ticket("T-one", "waiting_human", runs=["R-fail"])])
    waiting_md = render_markdown(waiting)

    return {
        "rows": rows,
        "closed_by_attempt": closed_by_attempt,
        "closed_md": closed_md,
        "closed_by_hand": closed_by_hand,
        "hand_md": hand_md,
        "unread": unread,
        "unread_md": unread_md,
        "waiting": waiting,
        "waiting_md": waiting_md,
    }


def _detail_block(markdown: str, run_id: str) -> str:
    """The detail section of one run."""
    marker = "## \u8fd0\u884c `%s`" % run_id
    if marker not in markdown:
        return ""
    tail = markdown.split(marker, 1)[1]
    for stop in ("\n## ", "\n# "):
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return tail


def _overview_row(markdown: str, run_id: str) -> str:
    """The overview table's row for one run."""
    for line in markdown.splitlines():
        if line.startswith("| `%s`" % run_id):
            return line
    return ""


def _problem_row(markdown: str) -> str:
    """The problem overview table's row."""
    for line in markdown.splitlines():
        if line.startswith("|") and "\u628a\u4e8b\u60c5\u505a\u5b8c" in line:
            return line
    return ""


def _supersede_verdicts(surfaces: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate sees on the page, read one way."""
    md = str(surfaces.get("closed_md") or "")
    report = surfaces.get("closed_by_attempt") or {}
    totals = report.get("totals") or {}
    detail = _detail_block(md, "R-fail")
    row = _overview_row(md, "R-fail")
    problem = _problem_row(md)

    hand_md = str(surfaces.get("hand_md") or "")
    hand_detail = _detail_block(hand_md, "R-fail")

    unread_md = str(surfaces.get("unread_md") or "")
    unread_detail = _detail_block(unread_md, "R-fail")

    return {
        "a single-run report says the problem is finished when the queue says so": (
            int(totals.get("not_closed_open") or 0) == 0
            and int(totals.get("not_closed_superseded") or 0) == 1
            and int(totals.get("problems_open") or 0) == 0
            and int(totals.get("problems_closed") or 0) == 1
        ),
        "the four lines of the page agree about that run": (
            "\u4ecd\u5728\u7b49\uff1a0" in md
            and "\u5f85\u5904\u7406" not in problem
            and "\u5df2\u89e3\u51b3" in problem
            and "HIS" in row
            and "ESC" not in row
            and "\u5386\u53f2\uff1a\u8fd9\u6b21\u5931\u8d25\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in detail
        ),
        "the detail names the attempt that replaced it": (
            "R-later" in detail
            and "\u53d6\u4ee3\u5b83\u7684\u90a3\u6b21\u6210\u529f\u5c1d\u8bd5" in detail
        ),
        "a failure whose ticket was closed by hand stays open": (
            "\u672a\u95ed\u5408\u539f\u56e0" in hand_detail
            and "\u5386\u53f2\uff1a\u8fd9\u6b21\u5931\u8d25\u5df2\u88ab" not in hand_detail
        ),
        "a failure whose ticket was never read keeps its resume pointer": (
            "mas resume" in unread_detail
            and "\u5df2\u4e0d\u9700\u8981\u7eed\u8dd1" not in unread_detail
        ),
        "the closure verdict reaches the same answer": (
            int(((surfaces.get("waiting") or {}).get("totals") or {}).get("problems_open") or 0) == 1
            and int(((surfaces.get("waiting") or {}).get("totals") or {}).get("not_closed_open") or 0) == 1
        ),
    }


def _supersede_verdicts_independent(surfaces: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw facts, not from the page.

    This reading does not look at the rendered text at all. It reads
    the report's own counts and the queue, and works out what the page
    *should* say. If the two readings disagree, the gate says so -
    and the gate cannot be satisfied by a page that merely looks
    right.
    """
    report = surfaces.get("closed_by_attempt") or {}
    totals = report.get("totals") or {}
    runs = report.get("runs") or []
    threads = report.get("threads") or []

    replaced = [
        run
        for run in runs
        if not run.get("closed") and str(run.get("failure_grain") or "") == "superseded"
    ]

    hand = surfaces.get("closed_by_hand") or {}
    hand_totals = hand.get("totals") or {}
    hand_runs = hand.get("runs") or []

    unread = surfaces.get("unread") or {}
    unread_runs = unread.get("runs") or []

    waiting = surfaces.get("waiting") or {}
    waiting_totals = waiting.get("totals") or {}

    return {
        "a single-run report says the problem is finished when the queue says so": (
            len(replaced) == 1
            and int(totals.get("not_closed_open") or 0) == 0
            and int(totals.get("not_closed_superseded") or 0) == 1
            and int(totals.get("problems_open") or 0) == 0
            and len(threads) == 1
            and bool(threads[0].get("closed"))
        ),
        "the four lines of the page agree about that run": (
            len(replaced) == 1
            and int(totals.get("not_closed_open") or 0) == len(
                [r for r in runs if not r.get("closed") and r.get("failure_grain") == "open"]
            )
            and int(totals.get("problems_open") or 0) == 0
        ),
        "the detail names the attempt that replaced it": (
            len(replaced) == 1
            and str(replaced[0].get("superseded_by") or "") == "R-later"
        ),
        "a failure whose ticket was closed by hand stays open": (
            int(hand_totals.get("not_closed_open") or 0) == 1
            and int(hand_totals.get("not_closed_superseded") or 0) == 0
            and len(
                [
                    r
                    for r in hand_runs
                    if not r.get("closed") and r.get("failure_grain") == "open"
                ]
            )
            == 1
        ),
        "a failure whose ticket was never read keeps its resume pointer": (
            int((unread.get("totals") or {}).get("not_closed_open") or 0) == 1
            and all(
                (not r.get("ticket_known")) and (not r.get("ticket_lost"))
                for r in unread_runs
            )
        ),
        "the closure verdict reaches the same answer": (
            int(waiting_totals.get("problems_open") or 0) == 1
            and int(waiting_totals.get("not_closed_open") or 0) == 1
        ),
    }


def check_the_queue_decides_and_not_the_ledger_alone(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The forty-fifth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surfaces()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the single-run check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _supersede_verdicts(facts)
            independent = _supersede_verdicts_independent(facts)
            for label in SINGLE_RUN_SUPERSEDE_OBSERVATIONS:
                if label not in verdicts:
                    failures.append(
                        "round %d: the gate did not observe %s" % (round_index + 1, label)
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

            # The gate proves it can fail: the queue's word is
            # ignored on purpose - the threads are built from the
            # ledger alone again - and the gate has to notice that
            # the page contradicts itself.
            import mas.report as report_module

            real_group_threads = report_module.group_threads

            def _ignore_the_queue(runs, closed_by_ticket=None, _real=real_group_threads):
                return _real(runs)

            try:
                report_module.group_threads = _ignore_the_queue
                broken: Dict[str, bool] = {}
                try:
                    broken = _supersede_verdicts(_surfaces())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.group_threads = real_group_threads

            if broken.get(
                "a single-run report says the problem is finished when the queue says so"
            ):
                failures.append(
                    "round %d: the gate did not notice that the queue's word is "
                    "ignored - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
            if broken.get("the four lines of the page agree about that run"):
                failures.append(
                    "round %d: the gate did not notice that the page contradicts "
                    "itself again" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "single_run_supersede",
        "title": "\u5355\u6b21\u8fd0\u884c\u7684\u62a5\u544a\u4e5f\u542c\u5de5\u5355\u961f\u5217\u7684",
        "skipped": False,
        "invariant": (
            "\u5de5\u5355\u961f\u5217\u624d\u662f\u201c\u8fd8\u5728\u7b49\u201d\u7684\u6743\u5a01\uff1a"
            "\u5355\u6b21\u8fd0\u884c\u62a5\u544a\u91cc\u5931\u8d25\u88ab\u540e\u6765\u6210\u529f\u7684\u5c1d\u8bd5\u53d6\u4ee3\u65f6\u5f52\u5165\u5386\u53f2"
        ),
        "detail": "\u67e5\u5355\u6b21\u8fd0\u884c\u7684\u62a5\u544a\u662f\u5426\u542c\u4e86\u5de5\u5355\u961f\u5217\u7684\u8bdd",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_queue_decides_and_not_the_ledger_alone.CHECK_NAME = "single_run_supersede"  # type: ignore[attr-defined]

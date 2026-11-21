# -*- coding: utf-8 -*-
"""The forty-fourth gate: the run detail says the same grain as the headline.

The gap
-------
v0.7.100 made the **headline** of the report say the grain out loud:
"still waiting 0 / history 1". v0.8.2 made the **verdict page** say
it. One surface was still left behind, and it is the surface a reader
goes to when they want to know *what to do next*: the **per-run
detail**.

A report of a failed-then-succeeded problem said, at the top:

    尝试总数：2　已闭环：1　仍在等：0　尝试闭环率：50%
    历史失败：1 次（其中 1 次已被后来的成功尝试取代，不算待办）

and then, in the detail of the one failed run:

    - 未闭合原因：stagnation: the deliverable did not change for 3 attempts
    - 人工工单：`T1`（`mas tickets` 查看，`mas resume <工单号>` 从这里继续）

The headline said "nothing is waiting". The detail handed the reader a
**resume command** for the run that was already replaced. One machine,
two answers - and the answer you got depended on which part of the
page you scrolled to. The detail is the part a beginner reads when
they want to know what to do, so the detail is the part that must not
say "there is work" when there is none.

The same hole had a second mouth: the overview table marked that run
``ESC`` (handed to a human), which reads as "still open" next to a
problem the overview table says is "已解决".

The fix
-------
Every run now carries the grain it belongs to, decided by the same
rule the headline uses:

* ``superseded`` - a later attempt replaced it. History.
* ``open``        - it belongs to a problem that is still open.

And the resume pointer is given only to a run whose ticket is **still
waiting in the queue**. A run keeps the id of the ticket it closed, so
"has a ticket id" is not "has work waiting" - the queue is the
authority on that.

This gate watches:

* the detail of a replaced attempt is filed as history, not as work;
* a replaced attempt does not hand out a resume command;
* the overview table does not mark a replaced attempt as open;
* a run whose ticket is still waiting **keeps** its reason, its
  next-step hint and its resume command - the fix must not throw away
  the guidance a real pending problem needs;
* the detail and the headline agree about what is waiting.

And the gate proves it can fail: the detail is broken on purpose - it
is made to hand out the resume command again - and the gate has to
notice that the page tells the reader to go and resume work that is
already done.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: The observations the gate makes.
RUN_DETAIL_GRAIN_OBSERVATIONS = (
    "the detail of a replaced attempt is filed as history, not as work",
    "a replaced attempt does not hand out a resume command",
    "the overview table does not mark a replaced attempt as open",
    "a run whose ticket is still waiting keeps its reason and its resume command",
    "the detail and the headline agree about what is waiting",
    "a ticket the queue has lost is not mistaken for finished work",
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
                "scores": [{"id": "c1", "pass": closed}]},
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


#: One problem, two attempts: the first failed, the second closed it.
SUPERSEDED_CASE = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]

#: One problem, two attempts, the last one failed: still waiting.
STILL_OPEN_CASE = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


def _ticket(ticket_id: str, state: str, run_id: str = "") -> Dict[str, Any]:
    """One ticket in the queue."""
    payload: Dict[str, Any] = {
        "goal": "\u628a\u4e8b\u60c5\u505a\u5b8c",
        "title": "\u628a\u4e8b\u60c5\u505a\u5b8c",
        "reason": "stagnation: the deliverable did not change",
        "runs": [run_id] if run_id else [],
    }
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
    """The report, in the three states the reader can meet it."""
    from mas.report import build_report, render_markdown

    # The replaced attempt, on a ticket that was resolved.
    superseded_rows = _rows(SUPERSEDED_CASE)
    superseded_tickets = [_ticket("T-sup1", "resolved", "R-sup2")]
    superseded_report = build_report(superseded_rows, superseded_tickets)
    superseded_md = render_markdown(superseded_report)

    # The same shape, but the ticket is still waiting: a real problem.
    open_rows = _rows(STILL_OPEN_CASE)
    open_tickets = [_ticket("T-open1", "waiting_human", "R-open2")]
    open_report = build_report(open_rows, open_tickets)
    open_md = render_markdown(open_report)

    # The ticket the queue has lost: the ledger records it, the
    # queue does not hold it. "Lost" is not "resolved", and the
    # report must not say the work is finished.
    lost_rows = _rows([("R-lost", False, "T-lost")])
    lost_report = build_report(lost_rows, [])
    lost_md = render_markdown(lost_report)

    return {
        "superseded_md": superseded_md,
        "superseded_report": superseded_report,
        "open_md": open_md,
        "open_report": open_report,
        "lost_md": lost_md,
        "lost_report": lost_report,
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


def _grain_text_verdicts(surfaces: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate sees on the page, read one way."""
    md = str(surfaces.get("superseded_md") or "")
    detail = _detail_block(md, "R-sup1")
    row = _overview_row(md, "R-sup1")
    report = surfaces.get("superseded_report") or {}
    totals = report.get("totals") or {}

    waiting_here = int(totals.get("not_closed_open") or 0)
    open_md = str(surfaces.get("open_md") or "")
    open_detail = _detail_block(open_md, "R-open2")

    return {
        "the detail of a replaced attempt is filed as history, not as work": (
            "\u5386\u53f2\uff1a\u8fd9\u6b21\u5931\u8d25\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in detail
            and "\u672a\u95ed\u5408\u539f\u56e0" not in detail
        ),
        "a replaced attempt does not hand out a resume command": (
            "mas resume" not in detail
            and "mas resume" not in _detail_block(md, "R-sup2")
        ),
        "the overview table does not mark a replaced attempt as open": (
            "HIS" in row
            and "ESC" not in row
        ),
        "a run whose ticket is still waiting keeps its reason and its resume command": (
            "\u672a\u95ed\u5408\u539f\u56e0" in open_detail
            and "mas resume" in open_detail
        ),
        "the detail and the headline agree about what is waiting": (
            waiting_here == 0
            and "\u4ecd\u5728\u7b49\uff1a0" in md
            and "\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21" in md
        ),
        "a ticket the queue has lost is not mistaken for finished work": (
            "\u5df2\u4e0d\u9700\u8981\u7eed\u8dd1"
            not in _detail_block(str(surfaces.get("lost_md") or ""), "R-lost")
            and "mas resume"
            not in _detail_block(str(surfaces.get("lost_md") or ""), "R-lost")
        ),
    }


def _grain_text_verdicts_independent(surfaces: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw facts, not from the page.

    This second reading does not look at the rendered text at all. It
    reads the report's own counts and the queue, and works out what
    the page *should* say. If the two readings disagree, the gate says
    so - and the gate cannot be satisfied by a page that merely looks
    right.
    """
    report = surfaces.get("superseded_report") or {}
    totals = report.get("totals") or {}
    runs = report.get("runs") or []

    replaced = [
        run
        for run in runs
        if not run.get("closed") and str(run.get("failure_grain") or "") == "superseded"
    ]
    still_open = [
        run
        for run in runs
        if not run.get("closed") and str(run.get("failure_grain") or "") == "open"
    ]
    waiting = int(totals.get("not_closed_open") or 0)
    history = int(totals.get("not_closed") or 0)

    open_report = surfaces.get("open_report") or {}
    open_runs = open_report.get("runs") or []
    open_waiting = [
        run
        for run in open_runs
        if not run.get("closed") and run.get("ticket_waiting")
    ]

    return {
        "the detail of a replaced attempt is filed as history, not as work": (
            len(replaced) == 1
            and len(still_open) == 0
            and waiting == 0
            and history == 1
        ),
        "a replaced attempt does not hand out a resume command": (
            all(not run.get("ticket_waiting") for run in replaced)
        ),
        "the overview table does not mark a replaced attempt as open": (
            len(replaced) == 1
            and str(replaced[0].get("run_id") or "") == "R-sup1"
        ),
        "a run whose ticket is still waiting keeps its reason and its resume command": (
            len(open_waiting) == 1
            and str(open_waiting[0].get("run_id") or "") == "R-open2"
        ),
        "the detail and the headline agree about what is waiting": (
            waiting == len(still_open)
        ),
        "a ticket the queue has lost is not mistaken for finished work": (
            all(
                not run.get("ticket_known")
                for run in (surfaces.get("lost_report") or {}).get("runs") or []
            )
            and all(
                run.get("ticket_lost")
                for run in (surfaces.get("lost_report") or {}).get("runs") or []
            )
        ),
    }


def check_the_run_detail_speaks_the_same_grain(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The forty-fourth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surfaces()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the run detail check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _grain_text_verdicts(facts)
            independent = _grain_text_verdicts_independent(facts)
            for label in RUN_DETAIL_GRAIN_OBSERVATIONS:
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

            # The gate proves it can fail: the detail is broken on
            # purpose - it is made to hand out the resume command
            # again - and the gate has to notice that the page sends
            # the reader to resume work that is already done.
            import mas.report as report_module

            real_render = report_module.render_markdown

            def _old_detail(report, _real=real_render):
                # The old shape: every failed attempt reads as work in
                # progress, and every run with a ticket id hands out a
                # resume command.
                text = _real(report)
                out: List[str] = []
                for line in text.splitlines():
                    if line.startswith("- \u5386\u53f2\uff1a\u8fd9\u6b21\u5931\u8d25"):
                        continue
                    if line.startswith("- \u5f53\u65f6\u7684\u539f\u56e0\uff1a"):
                        line = line.replace(
                            "- \u5f53\u65f6\u7684\u539f\u56e0\uff1a", "- \u672a\u95ed\u5408\u539f\u56e0\uff1a"
                        )
                    if line.startswith(
                        (
                            "- \u5f53\u65f6\u7684\u4eba\u5de5\u5de5\u5355\uff1a",
                            "- \u4eba\u5de5\u5de5\u5355\uff1a",
                        )
                    ):
                        line = (
                            "- \u4eba\u5de5\u5de5\u5355\uff1a`T-sup1`"
                            "\uff08`mas tickets` \u67e5\u770b\uff0c`mas resume <\u5de5\u5355\u53f7>` \u4ece\u8fd9\u91cc\u7ee7\u7eed\uff09"
                        )
                    if line.startswith("| `R-sup1`"):
                        line = line.replace("HIS \u5df2\u88ab\u53d6\u4ee3", "ESC \u5df2\u4ea4\u7ed9\u4eba\u5de5")
                    out.append(line)
                return "\n".join(out)

            try:
                report_module.render_markdown = _old_detail
                broken: Dict[str, bool] = {}
                try:
                    broken = _grain_text_verdicts(_surfaces())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.render_markdown = real_render

            if broken.get(
                "a replaced attempt does not hand out a resume command"
            ):
                failures.append(
                    "round %d: the gate did not notice that the detail went back to "
                    "handing out a resume command for replaced work - it cannot fail, "
                    "so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "run_detail_grain",
        "title": "\u8fd0\u884c\u660e\u7ec6\u4e5f\u8bf4\u540c\u4e00\u5957\u8bdd\uff1a\u88ab\u53d6\u4ee3\u7684\u5931\u8d25\u5f52\u5165\u5386\u53f2",
        "skipped": False,
        "invariant": (
            "\u62a5\u544a\u7684\u9010\u8fd0\u884c\u660e\u7ec6\u4e0e\u5176\u5934\u90e8\u8bf4\u540c\u4e00\u4ef6\u4e8b\uff1a"
            "\u88ab\u540e\u6765\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3\u7684\u5931\u8d25\u5f52\u5165\u5386\u53f2\uff0c\u4e0d\u518d\u53d1\u51fa\u7eed\u8dd1\u6307\u5f15"
        ),
        "detail": "\u67e5\u62a5\u544a\u7684\u9010\u8fd0\u884c\u660e\u7ec6\u662f\u5426\u8fd8\u5728\u628a\u88ab\u53d6\u4ee3\u7684\u5931\u8d25\u5f53\u4f5c\u5f85\u529e",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_run_detail_speaks_the_same_grain.CHECK_NAME = "run_detail_grain"  # type: ignore[attr-defined]

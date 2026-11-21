# -*- coding: utf-8 -*-
"""The fortieth gate: every surface that shows the state says it in one grain.

The gap
-------
v0.7.98 fixed the *grain* of the closure verdict: a problem that
failed and then succeeded is closed, because the later attempt
replaced the earlier one. v0.7.97 put the verdict in one place so the
command line and the MCP server could not disagree about it.

But the verdict was the only surface that used the new rule. The
status page - ``mas status``, the page a beginner opens first, and
the only page that answers "where does this machine stand?" -
still counted every failed attempt. So one machine could say:

    账本：尝试 2 次  已闭环 1  未闭合 1
    待解决问题：0 个（共 1 个问题）
    闭环判定：闭合：没有留下任何待办。

Three lines, two grains, and nothing on the screen saying which is
which. The reader is left to guess which number means "there is work
for me" - and the answer changes depending on which line they read
first. Two answers to one question, from one machine.

The same drift reached the MCP surface. ``mas_status`` reported the
ledger and the ticket queue and said **nothing at all** about closure.
An agent asking "is this done?" - the first question anyone asks a
status tool - got silence from the very tool that looks like the
place to ask it, and had to go and call a different tool to find out
whether the work was finished.

The fix is not a third copy of the rule. It is **one** function -
``closure_grain()`` - that computes the counts, and every surface
that shows or reports them reads that one function.

So this gate watches the *surfaces*:

* the status page's waiting count agrees with the closure verdict;
* the status page says what is waiting and says what is history, and
  does not print the two as the same number;
* the report headline names the grain of every number on it;
* the MCP status tool reports the closure, in the same grain, with
  the reasons as data;
* and the three surfaces cannot drift apart, because they read one
  function.

And the gate proves it can fail: the status page is broken on purpose
- it is made to count every failed attempt again - and the gate has
to notice that a resumed problem now holds the page open while the
verdict says it is closed.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes.
STATUS_GRAIN_OBSERVATIONS = (
    "the status page and the closure verdict count the same thing",
    "the status page says what is waiting and what is history apart",
    "the report headline names the grain of every number on it",
    "the MCP status tool reports the closure in the same grain",
    "the three surfaces read one rule, not three copies",
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


#: One problem, two attempts: the first failed, the second closed it.
SUPERSEDED_CASE = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]

#: One problem, two attempts, the last one failed: still waiting.
STILL_OPEN_CASE = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


def _write_ledger(path: str, rows: List[Dict[str, Any]]) -> None:
    """Write the rows where the engine reads them from.

    The engine keeps its ledger under ``<state_dir>/ledgers/runs.jsonl``.
    A gate that writes anywhere else would be reading an empty machine
    and would never notice.
    """
    import json
    import os as _os

    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _status_payload(state_dir: str) -> Dict[str, Any]:
    """The status page's JSON payload, read the way a reader reads it."""
    import json

    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["status", "--json"])
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return json.loads(buffer.getvalue())


def _status_text(state_dir: str) -> str:
    """The status page as a person reads it."""
    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["status"])
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return buffer.getvalue()


def _mcp_status_payload(state_dir: str) -> Dict[str, Any]:
    """The MCP status tool's payload."""
    from mas.mcp_server import handle_tool

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    try:
        return handle_tool("mas_status", {})
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved


def _surface_observations() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.report import (
        build_report,
        closure_grain,
        closure_verdict,
        group_runs,
        group_threads,
        render_markdown,
        run_summary,
    )

    superseded_rows = _rows(SUPERSEDED_CASE)
    open_rows = _rows(STILL_OPEN_CASE)

    sup_runs = [run_summary(g["run_id"], g["rows"]) for g in group_runs(superseded_rows)]
    open_runs = [run_summary(g["run_id"], g["rows"]) for g in group_runs(open_rows)]

    sup_report = build_report(superseded_rows, [], [])
    open_report = build_report(open_rows, [], [])

    facts: Dict[str, Any] = {
        "superseded_report": sup_report,
        "open_report": open_report,
        "superseded_verdict": closure_verdict(sup_report),
        "open_verdict": closure_verdict(open_report),
        "superseded_grain": closure_grain(sup_runs, group_threads(sup_runs)),
        "open_grain": closure_grain(open_runs, group_threads(open_runs)),
        "superseded_markdown": render_markdown(sup_report),
        "open_markdown": render_markdown(open_report),
    }

    # The live surfaces. The status page and the MCP tool are read
    # from a real state directory, because the point of the gate is
    # what a reader actually sees.
    sup_dir = tempfile.mkdtemp(prefix="mas-selftest-grain-sup-")
    open_dir = tempfile.mkdtemp(prefix="mas-selftest-grain-open-")
    try:
        _write_ledger(os.path.join(sup_dir, "ledgers", "runs.jsonl"), superseded_rows)
        _write_ledger(os.path.join(open_dir, "ledgers", "runs.jsonl"), open_rows)
        facts["superseded_status"] = _status_payload(sup_dir)
        facts["open_status"] = _status_payload(open_dir)
        facts["superseded_status_text"] = _status_text(sup_dir)
        facts["open_status_text"] = _status_text(open_dir)
        facts["superseded_mcp"] = _mcp_status_payload(sup_dir)
        facts["open_mcp"] = _mcp_status_payload(open_dir)
    finally:
        shutil.rmtree(sup_dir, ignore_errors=True)
        shutil.rmtree(open_dir, ignore_errors=True)
    return facts


def _grain_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}

    sup_verdict = facts.get("superseded_verdict") or {}
    open_verdict = facts.get("open_verdict") or {}
    sup_status = facts.get("superseded_status") or {}
    open_status = facts.get("open_status") or {}
    sup_mcp = facts.get("superseded_mcp") or {}
    open_mcp = facts.get("open_mcp") or {}

    # The core of the gate: the status page and the verdict have to
    # count the same thing. The page's "still waiting" is the number
    # of failed attempts whose problem is still open; the verdict's
    # "runs_not_closed" is built on exactly that count.
    sup_runs = sup_status.get("runs") or {}
    open_runs = open_status.get("runs") or {}
    sup_counts = sup_verdict.get("counts") or {}
    open_counts = open_verdict.get("counts") or {}
    verdicts["the status page and the closure verdict count the same thing"] = bool(
        int(sup_runs.get("open") or 0) == int(sup_counts.get("not_closed_open") or 0)
        and int(open_runs.get("open") or 0) == int(open_counts.get("not_closed_open") or 0)
        # The resumed problem is closed, so nothing is waiting - and
        # the verdict does not say it is still waiting either.
        and int(sup_runs.get("open") or 0) == 0
        and sup_verdict.get("ok") is True
    )

    # The page has to tell the two apart. A reader who sees a
    # "waiting" number and a "history" number knows which one is a
    # task; a reader who sees one number cannot tell.
    sup_text = str(facts.get("superseded_status_text") or "")
    open_text = str(facts.get("open_status_text") or "")
    verdicts["the status page says what is waiting and what is history apart"] = bool(
        "仍在等" in sup_text
        and "历史失败" in sup_text
        # The resumed problem: nothing waits, and the history is
        # named as history - with the number of the superseded
        # failures spelled out.
        and "仍在等 0" in sup_text
        and "1 次已被后来的成功尝试取代" in sup_text
        # The open problem: one attempt still waits.
        and "仍在等 1" in open_text
    )

    # The report headline has to name the grain of every number on
    # it. "未闭环：1" next to "问题闭环率 100%" reads like the tool
    # contradicting itself.
    sup_md = str(facts.get("superseded_markdown") or "")
    verdicts["the report headline names the grain of every number on it"] = bool(
        "仍在等：0" in sup_md
        and "问题闭环率 100%" in sup_md
        and "历史失败：1 次" in sup_md
        and "已被后来的成功尝试取代" in sup_md
    )

    # The MCP tool has to answer the question it exists to answer.
    sup_closure = sup_mcp.get("closure") or {}
    open_closure = open_mcp.get("closure") or {}
    verdicts["the MCP status tool reports the closure in the same grain"] = bool(
        sup_closure
        and "attempts_open" in sup_closure
        and "problems_open" in sup_closure
        and int(sup_closure.get("attempts_open") or 0) == int(sup_counts.get("not_closed_open") or 0)
        and int(sup_closure.get("problems_open") or 0) == int(sup_counts.get("problems_open") or 0)
        and int(sup_closure.get("attempts_superseded") or 0) == int(sup_counts.get("not_closed_superseded") or 0)
        and sup_closure.get("ok") is True
        and open_closure
        and open_closure.get("ok") is False
        and isinstance(sup_closure.get("reasons"), list)
    )

    # The three surfaces read one rule, not three copies. The counts
    # on the page, in the report and over MCP have to be the same
    # numbers, because they come from one function.
    sup_grain = facts.get("superseded_grain") or {}
    sup_totals = (facts.get("superseded_report") or {}).get("totals") or {}
    verdicts["the three surfaces read one rule, not three copies"] = bool(
        int(sup_grain.get("attempts_open") or 0) == int(sup_totals.get("not_closed_open") or 0)
        and int(sup_grain.get("attempts_open") or 0) == int(sup_runs.get("open") or 0)
        and int(sup_grain.get("attempts_open") or 0) == int(sup_closure.get("attempts_open") or 0)
        and int(sup_grain.get("problems_open") or 0) == int(sup_totals.get("problems_open") or 0)
        and int(sup_grain.get("problems_open") or 0) == int((sup_status.get("problems") or {}).get("open") or 0)
        and int(sup_grain.get("problems_open") or 0) == int(sup_closure.get("problems_open") or 0)
    )
    return verdicts


def _grain_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same verdict, read a second way, sharing no code with the first.

    This pass does not call the shared grain function. It works the
    answers out by hand from the threads and the ledger rows, so a
    grain that always says "waiting" cannot make both passes agree
    on a wrong answer.
    """
    verdicts: Dict[str, bool] = {}

    sup_verdict = facts.get("superseded_verdict") or {}
    open_verdict = facts.get("open_verdict") or {}
    sup_status = facts.get("superseded_status") or {}
    open_status = facts.get("open_status") or {}
    sup_mcp = facts.get("superseded_mcp") or {}
    open_mcp = facts.get("open_mcp") or {}

    # Read the grain by hand: a problem is closed when its last
    # attempt closed it; a failed attempt whose problem is closed was
    # superseded and does not wait.
    def _waiting(rows: List[Any]) -> int:
        """Failed attempts whose problem is still open, counted by hand."""
        failures = [row for row in rows if not row[1]]
        if not failures:
            return 0
        # The last attempt of each ticket decides the problem.
        last_closed: Dict[str, bool] = {}
        for run_id, closed, ticket_id in rows:
            key = ticket_id or run_id
            last_closed[key] = closed
        waiting = 0
        for run_id, closed, ticket_id in rows:
            if closed:
                continue
            key = ticket_id or run_id
            if not last_closed.get(key, False):
                waiting += 1
        return waiting

    sup_waiting = _waiting(SUPERSEDED_CASE)
    open_waiting = _waiting(STILL_OPEN_CASE)
    sup_counts = sup_verdict.get("counts") or {}
    open_counts = open_verdict.get("counts") or {}
    sup_runs = sup_status.get("runs") or {}
    open_runs = open_status.get("runs") or {}

    verdicts["the status page and the closure verdict count the same thing"] = bool(
        sup_waiting == 0
        and int(sup_counts.get("not_closed_open") or 0) == 0
        and int(sup_runs.get("open") or 0) == 0
        and sup_verdict.get("ok") is True
        and open_waiting == int(open_counts.get("not_closed_open") or 0)
        and int(open_runs.get("open") or 0) == open_waiting
    )

    sup_text = str(facts.get("superseded_status_text") or "")
    open_text = str(facts.get("open_status_text") or "")
    verdicts["the status page says what is waiting and what is history apart"] = bool(
        "仍在等 0" in sup_text
        and "历史失败" in sup_text
        and "已被后来的成功尝试取代" in sup_text
        and "仍在等 1" in open_text
    )

    sup_md = str(facts.get("superseded_markdown") or "")
    verdicts["the report headline names the grain of every number on it"] = bool(
        "仍在等：0" in sup_md
        and "问题闭环率 100%" in sup_md
        and "已被后来的成功尝试取代" in sup_md
    )

    sup_closure = sup_mcp.get("closure") or {}
    open_closure = open_mcp.get("closure") or {}
    verdicts["the MCP status tool reports the closure in the same grain"] = bool(
        isinstance(sup_closure, dict)
        and bool(sup_closure)
        and int(sup_closure.get("attempts_open") or 0) == sup_waiting
        and int(sup_closure.get("problems_open") or 0) == 0
        and sup_closure.get("ok") is True
        and open_closure.get("ok") is False
    )

    sup_totals = (facts.get("superseded_report") or {}).get("totals") or {}
    verdicts["the three surfaces read one rule, not three copies"] = bool(
        int(sup_totals.get("not_closed_open") or 0) == sup_waiting
        and int(sup_runs.get("open") or 0) == sup_waiting
        and int(sup_closure.get("attempts_open") or 0) == sup_waiting
        and int(sup_totals.get("problems_open") or 0) == 0
        and int((sup_status.get("problems") or {}).get("open") or 0) == 0
        and int(sup_closure.get("problems_open") or 0) == 0
    )
    return verdicts


def check_the_status_page_speaks_the_same_grain(
    iterations: int = 5,
) -> Dict[str, Any]:
    """Every surface says the state in one grain - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surface_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the grain check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _grain_verdicts(facts)
            independent = _grain_verdicts_independent(facts)
            for label in STATUS_GRAIN_OBSERVATIONS:
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

            # The gate proves it can fail: the status page is broken
            # on purpose - it is made to count every failed attempt
            # again - and the gate has to notice that a resumed
            # problem now holds the page open while the verdict says
            # it is closed.
            import mas.cli as cli_module

            real_grain = cli_module.closure_grain

            def _count_every_failure_as_waiting(runs, threads=None, _real=real_grain):
                grain = _real(runs, threads)
                grain = dict(grain)
                # The old grain: every failure counts, superseded or
                # not.
                grain["attempts_open"] = int(grain.get("attempts_not_closed") or 0)
                return grain

            try:
                cli_module.closure_grain = _count_every_failure_as_waiting
                broken: Dict[str, bool] = {}
                try:
                    broken = _grain_verdicts(_surface_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                cli_module.closure_grain = real_grain

            if broken.get("the status page and the closure verdict count the same thing"):
                failures.append(
                    "round %d: the gate did not notice that the status page "
                    "holds a resumed problem open - it cannot fail, so it does "
                    "not watch" % (round_index + 1)
                )
            if broken.get("the three surfaces read one rule, not three copies"):
                failures.append(
                    "round %d: the gate did not notice that the three surfaces "
                    "now answer differently" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "status_grain",
        "title": "状态页、报告和 MCP 用同一个粒度说话",
        "skipped": False,
        "invariant": (
            "还在等的是问题不是尝试：状态页、报告和 MCP 报同一个数，"
            "被成功尝试取代的失败只算历史，不占着队列"
        ),
        "detail": "查状态页、报告头部和 MCP 报的未闭合数是不是同一件事",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_status_page_speaks_the_same_grain.CHECK_NAME = "status_grain"  # type: ignore[attr-defined]

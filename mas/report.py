"""Reporting and replay: turn a run ledger into a human readable report.

The ledger is the raw truth. A report is a projection of it: the same run
rendered for a human reviewer, a Markdown file or a replay trace. Nothing
here invents data - every number comes from the ledger.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

from mas.reason_text import reason_hint, reason_text
from mas.util import now_iso
from mas.verdict_text import (
    decision_reading,
    issue_reading,
    note_reading,
    role_reading,
    source_reading,
    status_reading,
)

# The roles that represent a *charge*: an entry in these roles books
# the tokens of one call the endpoint billed. The sum over these
# entries is the spend of the run, and it must equal the number the
# engine reported. ``closure`` is deliberately not in this list: it is
# a checkpoint of the running total, not a charge of its own - the
# money is booked on the call entries.
CHARGE_ROLES = (
    "planner",
    "candidates",
    "reflector",
    "review",
    "reask",
    "reask_failed",
    # The lead is a paid call too. Leaving it out of this list made
    # the ledger under-report the spend of every run the lead was
    # consulted in - the book said 550 tokens while the endpoint
    # billed 565. A ledger that under-reports is not a ledger.
    "lead",
    "truncated",
    "error",
)


def ledger_spend(rows: List[Dict[str, Any]]) -> int:
    """Total tokens booked by the ledger rows.

    Every billed call is booked exactly once, so this number is what
    the run really cost - no matter that the engine kept the figure in
    memory only.
    """
    total = 0
    for row in rows:
        if str(row.get("role", "")) not in CHARGE_ROLES:
            continue
        try:
            total += int(row.get("tokens", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


STATUS_ICON = {
    "closed": "OK",
    "escalated": "ESC",
    "budget_exhausted": "BUD",
    "error": "ERR",
    "not_closed": "OPEN",
    "incomplete": "OPEN",
    # The deliverable passed but the run did not close - the artifact is
    # good, the budget or the deadline was not. Marked apart from the
    # plain OPEN so the overview table says which half failed.
    "deliverable_ok_but_not_closed": "OK!",
}


def _iteration_count(
    closure: Dict[str, Any] | None,
    end: Dict[str, Any] | None,
    rows: List[Dict[str, Any]],
) -> int:
    """How many iterations a run took, as a number.

    The ledger is the only source of truth here, and every row that
    carries an iteration number is a candidate. The engine's own end
    entry counts first (it knows the real number), then the closure
    row, then the highest iteration number any row mentions.

    A run whose rows carry no iteration at all reports 0 - not
    ``None``. A report field that is sometimes a number and sometimes
    ``null`` cannot be read by a script.
    """
    for source in (end or {}, closure or {}):
        value = source.get("iterations", source.get("iteration"))
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    best = 0
    for row in rows or []:
        value = row.get("iteration")
        if value is None:
            continue
        try:
            best = max(best, int(value))
        except (TypeError, ValueError):
            continue
    return best


def _iteration_of(row: Dict[str, Any], fallback: int) -> int:
    """The iteration a ledger row belongs to, with a safe fallback."""
    try:
        return int(row.get("iteration") or fallback)
    except (TypeError, ValueError):
        return fallback


def _reflection_reading(
    entry: Dict[str, Any], criteria: Optional[List[Dict[str, Any]]] = None
) -> str:
    """A round's reflection, with its issues read in words.

    The reflection text was assembled when the report was built, and
    at that point the run's standards were not known. The raw issues
    are kept next to it, so the standards can be used here.
    """
    text = str(entry.get("reflection") or "")
    raw_issues = entry.get("reflection_issues") or []
    if not raw_issues:
        return text
    read = [issue_reading(issue, criteria) for issue in raw_issues]
    # The issues are the second part of the reflection text, after
    # the summary. They are replaced where they are, so the sentence
    # keeps its shape.
    for issue, reading in zip(raw_issues, read, strict=False):
        if str(issue) in text:
            text = text.replace(str(issue), reading, 1)
    return text


def _criteria_from_scores(scores: Any) -> List[Dict[str, Any]]:
    """The standards of a run, rebuilt from its scores.

    The report keeps the scores rather than the criteria, and each
    score carries the reading of its standard - the sentence that
    names it. That is what the issue reader needs, so the criteria
    are rebuilt from it.
    """
    criteria: List[Dict[str, Any]] = []
    for score in scores or []:
        if not isinstance(score, dict):
            continue
        score_id = str(score.get("id") or "")
        if not score_id:
            continue
        criteria.append(
            {
                "id": score_id,
                "desc": str(score.get("reading") or ""),
                "check": {},
            }
        )
    return criteria


def _reflection_payload(row: Dict[str, Any] | None) -> Dict[str, Any]:
    """The reflection body of a reflector row, whatever shape it has."""
    if not row:
        return {}
    payload = row.get("reflection")
    if isinstance(payload, dict):
        return payload
    return {}


def _reflection_summary(row: Dict[str, Any] | None) -> str:
    """One line saying what the loop thought about its own failure."""
    payload = _reflection_payload(row)
    for key in ("summary", "reason", "analysis", "next_action"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _reflection_text(
    row: Dict[str, Any] | None,
    criteria: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """The reflection of a round, ready to be printed.

    The reflection names the failed criteria in the engine's issue
    format. Those are read in words too - a verdict in words next to a
    raw note is a line that contradicts itself.
    """
    payload = _reflection_payload(row)
    if not payload:
        return ""
    parts: List[str] = []
    summary = _reflection_summary(row)
    if summary:
        parts.append(summary)
    issues = payload.get("issues") or []
    if isinstance(issues, list) and issues:
        parts.append(
            "；".join(issue_reading(i, criteria) for i in issues)
        )
    next_action = payload.get("next_action")
    if isinstance(next_action, str) and next_action.strip():
        parts.append(f"下一步：{next_action.strip()}")
    return "　".join(parts)


def _reflection_for(
    reflections: List[Dict[str, Any]], iteration: int
) -> Dict[str, Any] | None:
    """The reflection that belongs to a given iteration, if any."""
    for index, row in enumerate(reflections, start=1):
        if _iteration_of(row, index) == iteration:
            return row
    return None


def group_runs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group ledger rows into runs, keeping the order of first appearance."""
    runs: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for row in rows:
        run_id = str(row.get("run_id") or "unknown")
        if run_id not in runs:
            runs[run_id] = []
            order.append(run_id)
        runs[run_id].append(row)
    return [{"run_id": run_id, "rows": runs[run_id]} for run_id in order]


def run_summary(run_id: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise one run from its ledger rows."""
    closure = None
    planner = None
    candidates = None
    review = None
    run_end = None
    errors: List[str] = []
    roles: Dict[str, int] = {}
    # The row a human-written deliverable was submitted on. It is kept
    # apart from the closure row, because it is a different kind of
    # step: not a round of the loop, but the human bringing the
    # answer in.
    handback = None
    # Every round of the loop, in order. The engine writes one closure
    # row per iteration; the report used to keep only the last one, so
    # a run that revised itself three times showed a single verdict -
    # the last one. The reader saw "PASS" and learned nothing about
    # the rounds that came before it. A report of a *loop* that shows
    # only its last turn is a report of a single shot.
    rounds: List[Dict[str, Any]] = []
    # The reflections, in order. The reflector says why the next
    # attempt must change; it is the one role whose answer explains
    # the loop's own behaviour, and the report never mentioned it.
    reflections: List[Dict[str, Any]] = []
    for row in rows:
        role = str(row.get("role", "unknown"))
        roles[role] = roles.get(role, 0) + 1
        if role == "closure":
            closure = row
            rounds.append(row)
        elif role == "planner":
            planner = row
        elif role == "candidates":
            candidates = row
        elif role == "review":
            review = row
        elif role == "run_end":
            run_end = row
        elif role == "error":
            errors.append(str(row.get("error", "")))
        elif role == "reflector":
            reflections.append(row)
        elif role == "handback":
            handback = row

    closure = closure or {}
    scores = closure.get("scores") or []
    # A human-written deliverable never goes through a closure row: it
    # is not judged by a round of the loop, it is submitted to the
    # standards the loop was held to. So the scores of a handback live
    # on the handback row. The report looked at the closure row only,
    # and a handback run therefore showed *no* judgement at all - the
    # reader saw a closed run with an empty verdict, and had no way to
    # tell whether anybody had actually checked the deliverable.
    handback_scores = list((handback or {}).get("scores") or [])
    if not scores and handback_scores:
        scores = handback_scores
    # A criterion is blocking unless it explicitly says otherwise. Optional
    # criteria may fail without keeping the loop open, so they are reported
    # separately instead of being mixed into the failure list.
    # A criterion has three states, not two: it passed, it failed, or
    # nobody could judge it. The report used ``not s.get("pass")``, which
    # is True for False *and* for None - so every criterion nobody could
    # judge was filed under "failed" and rendered as ``[FAIL]``. The
    # engine is careful about exactly this distinction (an unjudged
    # criterion never passes, and it never gets to close the loop), and
    # the report threw the distinction away at the last step. Three
    # states have to stay three states all the way to the reader.
    failed = [
        s for s in scores
        if s.get("pass") is False and s.get("blocking", True)
    ]
    optional_failed = [
        s for s in scores
        if s.get("pass") is False and not s.get("blocking", True)
    ]
    # The criteria that were never judged at all. They are the reason a
    # run may not close, and they are *not* the same thing as a failure:
    # a failed criterion needs a fix, an unjudged one needs a decision.
    unjudged = [
        s for s in scores
        if s.get("pass") is None
    ]
    artifacts = [str(r.get("artifact_sha") or "") for r in rows if r.get("artifact_sha")]

    # The closure entry records the verdict of the *last* iteration and the
    # loop status. The status is what the user cares about, the verdict
    # stays available separately.
    last_verdict = str(closure.get("verdict") or "") if closure else ""
    decision = str(closure.get("decision") or "")
    # The ledger states how the run ended. The report used to *guess*
    # the status from the closure rows alone, and the guess was wrong
    # for every run that did not end on a decision: a run that stopped
    # on the budget ceiling was reported as "not closed" - as if it were
    # merely unfinished - and a run that died on a provider error was
    # reported as "incomplete". A replay that cannot tell "stopped",
    # "failed" and "unfinished" apart cannot explain a run.
    end = run_end or {}
    if end:
        status = str(end.get("status") or "")
        decision = str(end.get("decision") or "") or decision
    elif not closure:
        status = "incomplete"
    elif closure.get("closed"):
        status = "closed"
    elif decision == "escalate":
        status = "escalated"
    else:
        status = "not_closed"

    # Did the deliverable itself pass every check? This is a different
    # question from "did the run close", and the engine already answers
    # it - it books the answer on the run's end entry. The report threw
    # that answer away, so a run whose artifact was perfect but whose
    # wall-clock budget ran out looked exactly like a run whose artifact
    # was wrong. The user had to guess which half of the system failed.
    #
    # A run that did not close but whose deliverable passed is now
    # reported as ``deliverable_ok_but_not_closed`` - a distinguishable
    # state, not a third word for "unfinished".
    deliverable_ok = bool(end.get("deliverable_ok")) if end else False
    if status != "closed" and deliverable_ok and scores:
        status = "deliverable_ok_but_not_closed"

    # How many consecutive attempts produced the *same* deliverable. The
    # engine counts this - it is what tells "the worker is stuck" apart
    # from "the worker needs another round" - and the report threw it
    # away. A run that escalated because nothing changed looked exactly
    # like a run that escalated for any other reason.
    stagnation = 0
    for row in rows or []:
        if row.get("role") != "closure":
            continue
        try:
            stagnation = max(stagnation, int(row.get("stagnation") or 0))
        except (TypeError, ValueError):
            continue

    # The round-by-round view of the loop. One entry per closure row,
    # carrying what that round judged, what it cost and what the
    # deliverable looked like - so the reader can watch the loop work
    # instead of only reading its last verdict.
    timeline: List[Dict[str, Any]] = []
    for index, row in enumerate(rounds, start=1):
        try:
            iteration = int(row.get("iteration") or index)
        except (TypeError, ValueError):
            iteration = index
        round_scores = row.get("scores") or []
        timeline.append(
            {
                "iteration": iteration,
                "verdict": str(row.get("verdict") or ""),
                "decision": str(row.get("decision") or ""),
                "artifact_sha": str(row.get("artifact_sha") or ""),
                "artifact_short": str(row.get("artifact_sha") or "")[:12],
                "passed": sum(1 for s in round_scores if s.get("pass") is True),
                "total": len(round_scores),
                "unjudged": sum(1 for s in round_scores if s.get("pass") is None),
                "issues": list(row.get("issues") or []),
                "stagnation": int(row.get("stagnation") or 0),
                "closed": bool(row.get("closed")),
                # The reflection that led into this round, if any. It
                # is attached to the round it produced, not to a round
                # of its own, so the reader sees cause and effect next
                # to each other.
                "reflection": _reflection_text(_reflection_for(reflections, iteration)),
                # The raw issues of that reflection. The reflection
                # text is assembled here, but the standards of the
                # run are not known yet - so the issues are kept
                # and read at the display layer.
                "reflection_issues": list(
                    (_reflection_payload(_reflection_for(reflections, iteration)) or {}).get("issues") or []
                ),
                "reflection_summary": _reflection_summary(
                    _reflection_for(reflections, iteration)
                ),
            }
        )

    return {
        "run_id": run_id,
        "status": status,
        # The whole loop, round by round. ``rounds`` is the number of
        # rounds that were actually judged.
        "timeline": timeline,
        "rounds": len(timeline),
        # What the loop said about its own failures, in order.
        "reflections": [
            {
                "iteration": _iteration_of(row, index),
                "summary": _reflection_summary(row),
                "issues": list((row.get("reflection") or {}).get("issues") or []),
                "next_action": str((row.get("reflection") or {}).get("next_action") or ""),
            }
            for index, row in enumerate(reflections, start=1)
        ],
        # Whether the deliverable passed every check, independently of
        # whether the run closed. ``None`` means the run never said -
        # which is not the same as "the deliverable was bad".
        "deliverable_ok": deliverable_ok if end else None,
        # The number of consecutive attempts that produced no change.
        # Zero means the run never stagnated.
        "stagnation": stagnation,
        # The ticket this run escalated into. An escalated run without
        # a ticket number tells the human that something went wrong but
        # not where to act on it.
        "ticket_id": str(end.get("ticket_id") or "") if end else "",
        # Where the deliverable was written. The report used to print
        # the hash and stop there - a hash is a fingerprint, not
        # something a human can open. Now the report names the file.
        "artifact_path": str(end.get("artifact_path") or "") if end else "",
        "artifact_chars": int(end.get("artifact_chars") or 0) if end else 0,
        # The folder the run worked in, and the files it wrote there.
        # The deliverable alone was not the whole output - a run that
        # wrote three notes produced three notes, and the report used
        # to name only the deliverable.
        "workspace": str(end.get("workspace") or "") if end else "",
        "files": list(end.get("files") or []) if end else [],
        # What this run was about. Without it a reader sees a run id
        # and a hash and has to guess which task they are looking at.
        "goal": str(end.get("goal") or "") if end else "",
        "title": str(end.get("title") or "") if end else "",
        "verdict": last_verdict,
        "decision": decision,
        # The end entry is the engine's own account of the run, so it
        # wins over the closure rows wherever both exist. The closure
        # row describes the last *scored* iteration; a run that died on
        # a provider error after producing an artifact still has one,
        # and a report that hides it cannot show what was actually made.
        "closed": bool(end.get("closed")) if end else bool(closure.get("closed")),
        # The iteration count has to be a number. The old expression
        # was ``a or b if rows else 0``, which parses as ``(a or b) if
        # rows else 0`` - so a run whose rows carry no iteration field
        # reported ``null`` instead of a count, and a report that says
        # "null iterations" cannot be read by anything.
        "iterations": _iteration_count(closure, end, rows),
        "artifact_sha": str(
            (end.get("artifact_sha") if end else "") or closure.get("artifact_sha") or ""
        ),
        "scores": scores,
        "failed": failed,
        "optional_failed": optional_failed,
        # The criteria nobody could judge. Kept apart from "failed" so
        # a reader can tell "checked and rejected" from "never checked".
        "unjudged": unjudged,
        "issues": list((end.get("issues") if end else None) or closure.get("issues") or []),
        "roles": roles,
        "plan": (planner or {}).get("plan") or {},
        "candidates": (candidates or {}).get("candidates") or [],
        "review": {
            "mode": (review or {}).get("mode"),
            "personas": (review or {}).get("personas") or [],
            "usable": (review or {}).get("usable"),
            "skipped": (review or {}).get("skipped"),
            # A reviewer that never answered is not an abstention. The
            # number is shown so the report cannot claim a unanimous pass
            # that the panel never reached.
            "missing": (review or {}).get("missing"),
        },
        "errors": errors,
        # Did a human write this deliverable? A run that a person
        # finished by hand has to be distinguishable from a run the
        # machine closed on its own - the reader is looking at the
        # difference between "the system did it" and "a person did it,
        # and the system checked it".
        "handback": bool(end.get("handback")) if end else False,
        # What the human said when they handed it in.
        "human_note": str(end.get("human_note") or "") if end else "",
        # Why the run ended the way it did - the engine's own account.
        "error": str(end.get("error") or ""),
        "ended_at": str(end.get("ts") or ""),
        # What the run cost. The report could not answer this before:
        # the figure only existed in the engine's memory, so a replay
        # of the ledger could not say how much a run spent.
        "tokens": ledger_spend(rows),
        "started_at": str(rows[0].get("ts")) if rows else "",
        "finished_at": str(rows[-1].get("ts")) if rows else "",
        "artifacts": artifacts,
        "entries": len(rows),
    }


def thread_of(run: Dict[str, Any]) -> str:
    """The problem a run belongs to.

    A run that never escalated has no problem: it is its own thread.
    A run that escalated - or that was resumed from an escalation -
    belongs to the ticket that holds it. Counting runs instead of
    threads is how a report ends up saying "three unfinished runs"
    for one single unfinished problem.
    """
    ticket = str(run.get("ticket_id") or "")
    if ticket:
        return ticket
    return str(run.get("run_id") or "")


def group_threads(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group runs into threads, keeping the order of first appearance.

    A thread is one problem the human has to deal with. The runs in
    it are the attempts that were made on it.
    """
    order: List[str] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        key = thread_of(run)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(run)
    threads: List[Dict[str, Any]] = []
    for key in order:
        members = grouped[key]
        # A problem is closed when the *last* attempt closed it.
        #
        # It used to be "every attempt closed", which is a rule that
        # cannot ever be satisfied in practice: the first attempt of
        # a hard problem fails, so the problem is "open" forever - and
        # a human who later finished the job by hand, or a resumed run
        # that finally closed, still saw their problem sitting in the
        # queue as "???". The report said the opposite of what
        # happened, and a queue that never empties teaches the reader
        # to ignore it.
        #
        # The last attempt is the one that counts: a later attempt
        # supersedes an earlier one. The attempts are kept, so the
        # history is not lost - only the verdict changes.
        last = members[-1] if members else {}
        closed = bool(last.get("closed"))
        closed_by = str(last.get("run_id") or "") if closed else ""
        # A human deliverable that closed the problem is worth naming:
        # it is the answer, and it came from a person.
        handback = any(bool(m.get("handback")) for m in members)
        threads.append(
            {
                "thread_id": key,
                "runs": [m.get("run_id") for m in members],
                "run_count": len(members),
                "closed": closed,
                # Which attempt closed the problem. Empty while it is
                # still open.
                "closed_by": closed_by,
                # Whether a human wrote the deliverable that closed it.
                "handback": handback,
                "iterations": sum(int(m.get("iterations") or 0) for m in members),
                "tokens": sum(int(m.get("tokens") or 0) for m in members),
                "goal": next((m.get("goal") for m in members if m.get("goal")), ""),
                "title": next((m.get("title") for m in members if m.get("title")), ""),
                "ticket_id": next(
                    (str(m.get("ticket_id") or "") for m in members if m.get("ticket_id")),
                    "",
                ),
            }
        )
    return threads


def _run_source(run: Dict[str, Any]) -> str:
    """Where the answer of one run came from, in a word.

    A run the loop closed by itself and a run a person closed by hand
    are both "closed" - but they are not the same achievement, and a
    report that cannot tell them apart quietly lets the machine take
    credit for a person's work. The word is short enough to sit in a
    table cell and honest enough to be worth sitting there.
    """
    if run.get("handback"):
        return "🤝 人工交付物"
    if run.get("closed"):
        return "🤖 机器闭环"
    return "—"


def _thread_source(thread: Dict[str, Any]) -> str:
    """Where the answer of one problem came from, in a word."""
    if thread.get("handback"):
        return "🤝 人工完成"
    if thread.get("closed"):
        return "🤖 机器闭环"
    return "—"


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the full report payload for a set of ledger rows."""
    runs = [run_summary(run["run_id"], run["rows"]) for run in group_runs(rows)]
    closed = [r for r in runs if r["closed"]]
    failed_runs = [r for r in runs if not r["closed"]]
    threads = group_threads(runs)
    open_threads = [t for t in threads if not t["closed"]]
    return {
        "generated_at": now_iso(),
        "runs": runs,
        # The problems, as opposed to the attempts. One unfinished
        # problem that was resumed three times is one open problem and
        # four runs; a report that only counts runs makes the human
        # queue look four times as long as it is.
        "threads": threads,
        "totals": {
            "runs": len(runs),
            "closed": len(closed),
            "not_closed": len(failed_runs),
            # How many distinct problems the ledger holds.
            "problems": len(threads),
            "problems_open": len(open_threads),
            "problems_closed": len(threads) - len(open_threads),
            "closure_rate": round(len(closed) / len(runs), 4) if runs else 0.0,
            "entries": len(rows),
            # The spend of every run in this report.
            "tokens": sum(int(run.get("tokens") or 0) for run in runs),
        },
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report as Markdown."""
    totals = report.get("totals") or {}
    lines: List[str] = ["# OpenLoop MAS 闭环复盘报告", ""]
    lines.append(f"生成时间：{report.get('generated_at', '')}")
    lines.append(
        f"运行总数：{totals.get('runs', 0)}　已闭环：{totals.get('closed', 0)}　"
        f"未闭环：{totals.get('not_closed', 0)}　闭环率：{totals.get('closure_rate', 0):.0%}"
    )
    # Runs are attempts; problems are what the human actually has to
    # deal with. Saying both stops the report from looking like three
    # open problems when there is one.
    lines.append(
        f"待解决问题：{totals.get('problems_open', 0)} 个"
        f"（共 {totals.get('problems', 0)} 个问题，"
        f"{totals.get('problems_closed', 0)} 个已解决）"
    )
    lines.append(f"总花费：{int(totals.get('tokens', 0) or 0)} token")
    lines.append("")

    runs = report.get("runs") or []
    if not runs:
        # "账本为空，暂无运行记录。" is true but leaves the reader stuck:
        # it does not say whether the tool is broken or simply has not
        # been used yet, and it names no next step. The report is the
        # thing a beginner reads *after* running something, so it has to
        # point at the thing to run.
        lines.append("账本为空，暂无运行记录。")
        lines.append("")
        lines.append("这不是故障 —— 这台机器上还没跑过任何一次闭环。")
        lines.append("  先看它怎么跑：mas demo")
        lines.append("  再跑一个真任务：mas run --goal \"你的任务\" --criteria \"contains:关键词\"")
        return "\n".join(lines) + "\n"

    # The problems, first. The run table below lists every attempt,
    # so a problem that was attempted twice appears there twice - and
    # a reader who stops at that table concludes that two things need
    # doing. The thread table is the one that answers "how much is
    # left".
    threads = report.get("threads") or []
    if threads:
        lines.append("## 问题概览")
        lines.append("")
        lines.append(
            "| 问题 | 状态 | 尝试 | 轮数 | 工单 | token | 来源 |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for thread in threads:
            label = str(thread.get("title") or thread.get("goal") or thread.get("thread_id"))
            label = label.replace("|", "\\|")[:40]
            state = "已解决" if thread.get("closed") else "待处理"
            ticket = str(thread.get("ticket_id") or "-")
            # Where the answer came from. A problem closed by a
            # human is not the same as a problem the loop closed
            # by itself, and the reader has to be able to tell
            # them apart at a glance - otherwise "the loop closed
            # it" quietly takes credit for work a person did.
            source = _thread_source(thread)
            lines.append(
                f"| {label or '-'} | {state} | {thread.get('run_count', 0)} | "
                f"{thread.get('iterations', 0)} | `{ticket}` | "
                f"{int(thread.get('tokens') or 0)} | {source} |"
            )
        lines.append("")

    lines.append("## 运行概览")
    lines.append("")
    lines.append(
        "| 运行 ID | 任务 | 状态 | 决策 | 迭代 | 停滞 | "
        "产物 SHA | 条目 | token | 来源 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        status = str(run.get("status") or "-")
        icon = STATUS_ICON.get(status, "-")
        # The task the run was about. A table of run ids and hashes is
        # an audit log; a table that also says what was being done is
        # a report a human can read.
        goal = str(run.get("goal") or run.get("title") or "").replace("|", "\\|")
        goal = goal[:40] + ("…" if len(goal) > 40 else "")
        # The status and the decision in words. The raw value stays in
        # parentheses: the words are for the reader, the raw value is
        # what a reader greps for.
        status_text = status_reading(status)
        status_cell = status_text if status_text == status else (
            f"{status_text}（{status}）" if status_text else status
        )
        decision = str(run.get("decision") or "-")
        decision_text = decision_reading(decision)
        decision_cell = decision_text if decision_text == decision else (
            f"{decision_text}（{decision}）" if decision_text else decision
        )
        lines.append(
            f"| `{run.get('run_id')}` | {goal or '-'} | {icon} {status_cell} | "
            f"{decision_cell} | "
            f"{run.get('iterations', 0)} | {int(run.get('stagnation') or 0)} | "
            f"`{(run.get('artifact_sha') or '')[:12]}` | "
            f"{run.get('entries', 0)} | {int(run.get('tokens') or 0)} | "
            f"{_run_source(run)} |"
        )
    lines.append("")

    for run in runs:
        lines.append(f"## 运行 `{run.get('run_id')}`")
        lines.append("")
        # The standards of this run, so the issue lines below can
        # name the standard that failed and not just its serial
        # number.
        criteria = _criteria_from_scores(run.get("scores"))
        # Say what this run was about, before the dates. A reader who
        # opens a report of forty runs needs the task before the
        # timestamps.
        if run.get("goal"):
            lines.append(f"- 任务：{run.get('goal')}")
        # The human step, said out loud. A run that a person finished
        # by hand is not a run of the loop - it is the loop's own
        # standard applied to somebody else's work. The report used to
        # render it like any other run, so the one place where a human
        # was in the loop was the one place the reader could not see
        # a human.
        if run.get("handback"):
            note = str(run.get("human_note") or "")
            if run.get("closed"):
                lines.append(
                    "- 🤝 人工交付物：**通过验收**"
                    "（人工交回并通过验收）"
                )
            else:
                lines.append(
                    "- 🤝 人工交付物：**未通过验收**"
                    "（人工交回，但没过标准）"
                )
            if note:
                lines.append(f"  - 人工意见：{note}")
        if run.get("title") and run.get("title") != run.get("goal"):
            lines.append(f"- 标题：{run.get('title')}")
        lines.append(f"- 开始：{run.get('started_at') or '-'}")
        lines.append(f"- 结束：{run.get('finished_at') or '-'}")
        lines.append(f"- 花费：{int(run.get('tokens') or 0)} token")
        # Say which half failed. "Not closed" alone hides the difference
        # between a wrong deliverable and a good deliverable that ran
        # out of budget.
        if run.get("status") == "deliverable_ok_but_not_closed":
            lines.append(
                "- 交付物：✅ 通过了全部验收标准，但本次运行**没有闭合**"
                "（预算或死线到了）——交付物可用，直接取用即可"
            )
        elif run.get("deliverable_ok") is False and run.get("scores"):
            unjudged_ids = [str(s.get("id")) for s in run.get("unjudged") or []]
            if unjudged_ids:
                # Some criterion was never judged. Calling that a failed
                # deliverable is a false claim: the deliverable was not
                # rejected, it was never fully checked.
                lines.append(
                    "- 交付物：⚠ 未通过验收标准，且有标准**从未被判定**"
                    f"（`{', '.join(unjudged_ids)}`）——见下方判定明细"
                )
            else:
                lines.append(
                    "- 交付物：❌ 未通过验收标准（见下方未通过项）"
                )
        # Why the run ended the way it did. The engine writes the reason
        # on the run's end entry - including the specific cause of a
        # stagnation escalation ("the deliverable did not change for N
        # consecutive attempts") - and the report used to drop it
        # entirely. An escalated run whose reason is invisible sends the
        # human looking for the wrong thing.
        if run.get("error"):
            # In words. The report is the screen a beginner reads
            # after the run, and the reason is the one line that
            # says what to do next - in the engine's vocabulary it
            # says nothing a newcomer can use.
            lines.append(f"- 未闭合原因：{reason_text(run.get('error'))}")
            hint = reason_hint(run.get("error"))
            if hint:
                lines.append(f"  - 下一步：{hint}")
        elif run.get("status") == "escalated":
            lines.append("- 未闭合原因：运行升级人工处理（账本未记录具体原因）")
        if run.get("stagnation"):
            lines.append(
                f"- ⚠ 停滞：交付物连续 **{int(run.get('stagnation'))}** 次没有变化"
                "（worker 反复产出相同内容）"
            )
        if run.get("ticket_id"):
            lines.append(
                f"- 人工工单：`{run.get('ticket_id')}`"
                "（`mas tickets` 查看，`mas resume <工单号>` 从这里继续）"
            )
        # Where the work landed. The deliverable used to live only in
        # the engine's memory: the report could print its hash and
        # nothing else, so a closed run pointed at nothing the reader
        # could open.
        if run.get("artifact_path"):
            chars = int(run.get("artifact_chars") or 0)
            lines.append(
                f"- 交付物已保存：`{run.get('artifact_path')}`"
                f"（{chars} 字，可直接打开取用）"
            )
        # The files the run produced. A closed run that wrote three
        # notes produced three notes - the report named only the
        # deliverable, so the rest of the work was invisible.
        files = run.get("files") or []
        if files:
            names = "、".join(
                str(row.get("path") or "") for row in files if row.get("path")
            )
            lines.append(f"- 本次生成的文件：{names}")
        # The loop, round by round. The verdict of the last round was
        # the only one the report showed, so a run that revised itself
        # three times looked like a run that got it right first time.
        # The reader could not see the failure that drove the work.
        timeline = run.get("timeline") or []
        if len(timeline) > 1:
            lines.append(f"- 循环过程（共 {len(timeline)} 轮）：")
            for entry in timeline:
                verdict = str(entry.get("verdict") or "-")
                mark = "PASS" if verdict == "pass" else "FAIL"
                total = int(entry.get("total") or 0)
                passed = int(entry.get("passed") or 0)
                unjudged = int(entry.get("unjudged") or 0)
                detail = f"通过 {passed}/{total}" if total else "无判定"
                if unjudged:
                    detail += f"（{unjudged} 条无人判定）"
                lines.append(
                    f"  - 第 {entry.get('iteration')} 轮 [{mark}] {detail}"
                    f"　决策 {entry.get('decision') or '-'}"
                    f"　产物 `{(entry.get('artifact_short') or '')[:12]}`"
                )
                issues = entry.get("issues") or []
                for issue in issues:
                    # In words, next to the verdict that is
                    # already in words. The raw issue stays in
                    # the report payload - the ledger never
                    # changes.
                    lines.append(
                        f"    - 未过：{issue_reading(issue, criteria)}"
                    )
                reflection = entry.get("reflection") or ""
                if reflection:
                    lines.append(
                        f"    - 💭 反思："
                        f"{_reflection_reading(entry, criteria)}"
                    )
        elif timeline:
            # A single round: the loop did not iterate, so say that
            # instead of pretending there was a process.
            entry = timeline[0]
            verdict = str(entry.get("verdict") or "-")
            mark = "PASS" if verdict == "pass" else "FAIL"
            lines.append(
                f"- 循环过程：1 轮即定 [{mark}]"
                f"（未发生迭代）"
            )
            reflection = entry.get("reflection") or ""
            if reflection:
                lines.append(f"  - 💭 反思：{reflection}")
        if run.get("plan"):
            summary = (run.get("plan") or {}).get("summary") or ""
            lines.append(f"- 规划：{summary}")
            for step in (run.get("plan") or {}).get("plan") or []:
                lines.append(f"  - {step}")
        candidates = run.get("candidates") or []
        if candidates:
            lines.append(f"- 并行候选（{len(candidates)} 个）：")
            for candidate in candidates:
                mark = "★" if candidate.get("selected") else " "
                passed = candidate.get("passed")
                detail = "机检未配置" if passed is None else f"机检通过 {passed} 项"
                lines.append(f"  - {mark} `{candidate.get('angle')}` - {detail}")
        review = run.get("review") or {}
        if review.get("mode"):
            lines.append(
                f"- 对抗评审：模式 {review.get('mode')}，可用评审 {review.get('usable')}，"
                f"跳过 {review.get('skipped')}"
            )
            missing = review.get("missing")
            if missing:
                lines.append(
                    f"  - ⚠ 有 {missing} 位评审没有给出判定（缺席不等于弃权通过）"
                )
            personas = review.get("personas") or []
            if personas:
                lines.append(f"  - 评审视角：{', '.join(personas)}")
        scores = run.get("scores") or []
        if scores:
            if run.get("handback"):
                lines.append("- 人工交付物的验收判定：")
            else:
                lines.append("- 验收判定：")
            for score in scores:
                # Three states, three marks. ``PASS``/``FAIL``/``UNDECIDED``
                # - the middle state used to be rendered as FAIL, which
                # told the reader that a criterion nobody checked had
                # been checked and rejected.
                state = score.get("pass")
                if state is True:
                    flag = "PASS"
                elif state is False:
                    flag = "FAIL"
                else:
                    flag = "UNDECIDED"
                optional = not score.get("blocking", True)
                mark = "（可选，不阻塞闭合）" if optional else ""
                if flag == "UNDECIDED":
                    mark = "（无人判定，不能算通过）"
                # Same rule as the CLI: the ledger keeps the English
                # note, the report shows the Chinese one.
                reading_note = note_reading(score.get("note"))
                detail = reading_note or str(score.get("note") or "")
                lines.append(
                    f"  - [{flag}] `{score.get('id')}` {detail}{mark}"
                )
                # Quote the standard back: the user wrote several of
                # them, and "c2 FAIL" does not say which one failed.
                reading = str(score.get("reading") or "")
                if reading:
                    lines.append(f"    要求：{reading}")
        if run.get("issues"):
            lines.append("- 未通过原因：")
            for issue in run["issues"]:
                lines.append(f"  - {issue_reading(issue, criteria)}")
        unjudged = run.get("unjudged") or []
        if unjudged:
            lines.append("- 无人判定的标准（不等于失败，但也不能算通过）：")
            for score in unjudged:
                # A variable of its own: the verdict list above reuses
                # the same note, so a shared name would make a broken
                # translation here invisible - the loop would carry the
                # previous iteration's words into this line.
                unjudged_reading = note_reading(score.get("note"))
                detail = unjudged_reading or str(score.get("note") or "")
                raw_source = str(score.get("source") or "-")
                source = source_reading(raw_source)
                # The words are for the reader; the raw value stays
                # visible so a reader can grep for it.
                if source and source != raw_source:
                    source = f"{source}（{raw_source}）"
                elif not source:
                    source = raw_source
                lines.append(
                    f"  - `{score.get('id')}` {detail}"
                    f"（判定来源：{source}）"
                )
        if run.get("errors"):
            lines.append("- 错误：")
            for error in run["errors"]:
                lines.append(f"  - {error}")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_report(rows: List[Dict[str, Any]], out_dir: str | pathlib.Path) -> Dict[str, Any]:
    """Write the Markdown report next to the ledger and return its path."""
    target_dir = pathlib.Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(rows)
    stamp = report["generated_at"].replace(":", "").replace("-", "")
    path = target_dir / f"report-{stamp}.md"
    path.write_text(render_markdown(report), encoding="utf-8")
    return {"path": str(path), "report": report}


def replay_trace(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a deterministic replay trace of a run.

    The trace lists every ledger entry in order with the role, the decision
    and the artifact hash, so a reviewer can step through what the loop did.

    The trace also says *where a run belongs*. A ledger that holds two
    attempts on one problem prints them as one flat list, and the reader
    cannot tell "the loop tried twice" from "the loop handled two
    problems" - so the trace groups the steps by run, and the runs by
    the problem they belong to.
    """
    steps: List[Dict[str, Any]] = []
    for row in rows:
        steps.append(
            {
                "seq": row.get("seq"),
                "ts": row.get("ts"),
                "run_id": row.get("run_id"),
                "iteration": row.get("iteration"),
                "role": row.get("role"),
                "decision": row.get("decision"),
                "artifact_sha": row.get("artifact_sha"),
                "verdict": row.get("verdict"),
                "closed": row.get("closed"),
                # The mark that says a person wrote this step. Without
                # it the replay shows the human's work as one more
                # machine role, and the reader cannot see where in the
                # loop the human stepped in.
                "handback": bool(row.get("handback")),
                # Two details: the machine one for the exported
                # table, the human one for the replay screen.
                "detail": _short_detail(row),
                "detail_reading": _human_detail(row),
            }
        )
    # The runs in the trace, each with the outcome the ledger booked.
    run_blocks: List[Dict[str, Any]] = []
    for group in group_runs(rows):
        summary = run_summary(group["run_id"], group["rows"])
        run_blocks.append(
            {
                "run_id": summary["run_id"],
                "steps": len(group["rows"]),
                "status": summary["status"],
                "closed": summary["closed"],
                "iterations": summary["iterations"],
                "goal": summary["goal"],
                "ticket_id": summary["ticket_id"],
                "artifact_path": summary["artifact_path"],
                "tokens": summary["tokens"],
                # Did a person close this run? The replay says so
                # next to the run header, so the reader learns it
                # before reading the steps.
                "handback": bool(summary.get("handback")),
                "human_note": str(summary.get("human_note") or ""),
            }
        )
    # The problems behind the runs, so a reader sees "one problem, two
    # attempts" instead of two flat runs.
    threads = group_threads([run_summary(g["run_id"], g["rows"]) for g in group_runs(rows)])
    return {
        "steps": steps,
        "steps_count": len(steps),
        "artifact_chain": [s["artifact_sha"] for s in steps if s.get("artifact_sha")],
        "terminal": steps[-1] if steps else None,
        # The attempts and the problems they belong to.
        "runs": run_blocks,
        "threads": threads,
        "problems_open": len([t for t in threads if not t.get("closed")]),
    }


#: How a round's verdict reads.
VERDICT_WORD = {
    "pass": "通过",
    "fail": "没通过",
}


def verdict_word(value: str) -> str:
    """The reading of a round verdict, or the value itself.

    Public because the ledger listing needs the same word the report
    uses: two screens that describe the same round must not use two
    different vocabularies.
    """
    if not value:
        return "-"
    return VERDICT_WORD.get(value, value)


#: The old private name, kept so nothing that imported it breaks.
_verdict_word = verdict_word


def _human_detail(row: Dict[str, Any]) -> str:
    """A ledger row's detail, in words.

    The exported table keeps the machine's wording - it is read by
    scripts. The replay is read by a person, so its detail says what
    happened in words.
    """
    detail = _short_detail(row)
    role = str(row.get("role", ""))
    if role == "handback":
        scores = row.get("scores") or []
        passed = [s for s in scores if s.get("pass") is True]
        parts = ["人工交回交付物"]
        if scores:
            parts.append(f"通过 {len(passed)}/{len(scores)}")
            for item in scores:
                if item.get("pass") is True:
                    continue
                note = str(item.get("note") or "")
                reading = note_reading(note) or note
                if reading:
                    parts.append(f"未过 {item.get('id')}: {reading}")
        if parts:
            return "; ".join(parts)[:300]
    if role == "closure":
        verdict = str(row.get("verdict") or "").strip()
        decision = str(row.get("decision") or "").strip()
        parts: List[str] = []
        if verdict:
            parts.append("判定=" + _verdict_word(verdict))
        if decision:
            parts.append("决策=" + decision_reading(decision))
        scores = row.get("scores") or []
        if scores:
            passed = [s for s in scores if s.get("pass")]
            parts.append(f"通过 {len(passed)}/{len(scores)}")
            for item in scores:
                if item.get("pass"):
                    continue
                note = str(item.get("note") or "")
                reading = note_reading(note) or note
                if reading:
                    parts.append(f"未过 {item.get('id')}: {reading}")
        issues = row.get("issues") or []
        if issues:
            parts.append(
                "; ".join(issue_reading(i) for i in issues)
            )
        if parts:
            return "; ".join(parts)[:300]
    if role == "run_end":
        status = str(row.get("status") or "").strip()
        decision = str(row.get("decision") or "").strip()
        parts = []
        if status:
            parts.append("状态=" + status_reading(status))
        if decision:
            parts.append("决策=" + decision_reading(decision))
        reason = str(row.get("error") or "")
        if reason:
            parts.append(reason_text(reason))
        if parts:
            return "; ".join(parts)[:300]
    if role == "error":
        reason = str(row.get("error") or "")
        if reason:
            return reason_text(reason)[:300]
    if role == "reflector":
        payload = _reflection_payload(row)
        issues = payload.get("issues") or []
        if isinstance(issues, list) and issues:
            return (
                "\uff1b".join(issue_reading(i) for i in issues)
            )[:300]
    return detail


def _short_detail(row: Dict[str, Any]) -> str:
    role = str(row.get("role", ""))
    if role == "plan":
        return ""
    if role == "handback":
        # The handback row is the one line of a ledger that carries the
        # human's own work. It used to render as an empty string -
        # the trace had no branch for it - so a replay of a run that a
        # person finished showed a blank line exactly where the person
        # was. The per-criterion result is what makes the line worth
        # reading.
        scores = row.get("scores") or []
        passed = [s for s in scores if s.get("pass") is True]
        parts = ["人工交回"]
        if scores:
            parts.append(f"通过 {len(passed)}/{len(scores)}")
            for item in scores:
                if item.get("pass") is True:
                    continue
                note = str(item.get("note") or "")
                parts.append(f"未过 {item.get('id')}{': ' + note if note else ''}")
        issues = row.get("issues") or []
        if issues:
            parts.append("; ".join(str(i) for i in issues)[:200])
        return "; ".join(parts)[:300]
    if role == "closure":
        # The closure row is the most important line of a replay - it is
        # the one that says what was judged and how it came out. Its
        # summary used to show the issues and nothing else, so a run
        # that *closed* - the successful case, the case anyone replays
        # to see what a good run looks like - showed an empty line. The
        # verdict, the decision and the per-criterion result are the
        # actual answer, and the issues are only the failure side of it.
        parts = [f"判定={row.get('verdict') or '-'}", f"决策={row.get('decision') or '-'}"]
        scores = row.get("scores") or []
        passed = [s for s in scores if s.get("pass")]
        failed = [s for s in scores if not s.get("pass")]
        if scores:
            parts.append(f"通过 {len(passed)}/{len(scores)}")
            for item in failed[:3]:
                note = str(item.get("note") or "")
                parts.append(f"未过 {item.get('id')}{': ' + note if note else ''}")
        issues = row.get("issues") or []
        if issues:
            parts.append("; ".join(str(i) for i in issues)[:200])
        return "; ".join(parts)[:300]
    if role == "candidates":
        candidates = row.get("candidates") or []
        return "; ".join(
            f"{c.get('angle')}:{'选中' if c.get('selected') else '淘汰'}" for c in candidates
        )[:300]
    if role == "review":
        return (
            f"模式={row.get('mode')} 可用={row.get('usable')} "
            f"跳过={row.get('skipped')} 缺席={row.get('missing')}"
        )
    if role == "reflector":
        # The reflector is the role that says why the next attempt has
        # to change. The trace had no branch for it, so the one line
        # that carries the loop's own reasoning was blank.
        payload = _reflection_payload(row)
        parts: List[str] = []
        summary = _reflection_summary(row)
        if summary:
            parts.append(f"反思={summary}")
        issues = payload.get("issues") or []
        if isinstance(issues, list) and issues:
            parts.append("；".join(str(i) for i in issues)[:200])
        next_action = payload.get("next_action")
        if isinstance(next_action, str) and next_action.strip():
            parts.append(f"下一步={next_action.strip()}")
        return "；".join(parts)[:300]
    if role == "error":
        return str(row.get("error", ""))[:300]
    if role == "run_end":
        # The ledger table must say how the run ended - the same
        # sentence the report gives, so the two never contradict.
        detail = f"status={row.get('status')} decision={row.get('decision') or '-'}"
        reason = str(row.get("error") or "")
        if reason:
            detail += f" | {reason}"
        return detail[:300]
    if role == "planner":
        plan = row.get("plan") or {}
        return str(plan.get("summary", ""))[:300]
    return ""


def _both(raw: Any, reading: str) -> str:
    """Show the word and the raw name, side by side.

    A table that only shows the raw name makes the reader look the
    name up. A table that only shows the word makes the reader unable
    to grep for the thing they saw in the log. The exported table is
    used for both - it is pasted into an issue as often as it is read
    - so it keeps both.
    """
    raw_text = str(raw or "").strip()
    if not raw_text:
        return ""
    if not reading or reading == raw_text:
        return raw_text
    return f"{reading} ({raw_text})"


def ledger_markdown(rows: List[Dict[str, Any]]) -> str:
    """Render raw ledger rows as a Markdown table.

    The table is what a person opens when they want to see what the
    loop actually did. It used to print the engine's own identifiers
    in the role column - "closure", "review", "reflector" - which is
    the vocabulary of the source code, not of the reader. The role now
    says what the agent did, and the verdict and the decision say what
    came out of it.
    """
    lines = [
        "| # | 时间 | 运行 | 迭代 | 角色 | 判定 | 决策 | 摘要 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        verdict = str(row.get("verdict") or "").strip()
        decision = str(row.get("decision") or "").strip()
        lines.append(
            f"| {row.get('seq', '')} | {row.get('ts', '')} | `{row.get('run_id', '')}` | "
            f"{row.get('iteration', '')} | {_both(row.get('role'), role_reading(row.get('role')))} | "
            f"{_both(verdict, verdict_word(verdict)) if verdict else ''} | "
            f"{_both(decision, decision_reading(decision)) if decision else ''} | "
            f"{_short_detail(row).replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"

"""Reporting and replay: turn a run ledger into a human readable report.

The ledger is the raw truth. A report is a projection of it: the same run
rendered for a human reviewer, a Markdown file or a replay trace. Nothing
here invents data - every number comes from the ledger.
"""

from __future__ import annotations

import contextlib
import pathlib
from typing import Any, Dict, List, Optional

from mas.drafts_reading import draft_reading
from mas.reason_text import continuation_command, reason_hint, reason_text
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
#: The ledger roles that record a human decision about a draft. They
#: belong to the draft layer, not to a run.
DRAFT_DECISION_ROLES = ("draft_approve", "draft_undo")

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
        if str(row.get("role") or "") in DRAFT_DECISION_ROLES:
            # A human decision about a draft is not a run. Folding it
            # into one would invent a run with no closure and no goal,
            # and the report would list it as an unfinished problem.
            continue
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

    # Does the conclusion still hold on the bytes that are on the
    # disk now? The seal was written at the moment of the decision,
    # and the report is read long after it. A closure that was edited
    # afterwards is not a closure any more - but the report used to
    # say "closed" anyway, because it never looked. The verdict was
    # sealed; now the report re-derives it.
    seal = _seal_of(end)
    seal_check = _seal_state(seal, str(end.get("artifact_path") or "") if end else "")

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
        # Whether the sealed conclusion still holds on the disk as it
        # stands now. ``None`` means the run carried no seal at all -
        # a run from before the seal existed, or one that never
        # decided anything. That is not the same as "the conclusion
        # drifted".
        "seal_state": seal_check.get("state") if seal else None,
        "seal_ok": seal_check.get("ok") if seal else None,
        "seal_problems": list(seal_check.get("problems") or []) if seal else [],
        "seal_drifted_files": list(seal_check.get("drifted_files") or []) if seal else [],
        "seal_missing_files": list(seal_check.get("missing_files") or []) if seal else [],
        "seal_recomputed": list(seal_check.get("recomputed") or []) if seal else [],
        "seal_model_judged": list(seal_check.get("model_judged") or []) if seal else [],
        # The seal itself, so a reader of the JSON payload can do
        # their own checking instead of trusting the summary.
        "seal": seal,
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
                # Whether the conclusion of the *last* attempt still
                # holds on the disk. A problem is closed by its last
                # attempt, so that is the attempt whose seal decides
                # whether the problem is really solved - or only was.
                "seal_state": last.get("seal_state"),
                "seal_ok": last.get("seal_ok"),
                "seal_drifted_files": list(last.get("seal_drifted_files") or []),
                "seal_missing_files": list(last.get("seal_missing_files") or []),
            }
        )
    return threads


#: The states a seal can be in, in words. A seal that does not
#: still hold is not a detail - it is the difference between "this
#: loop closed" and "this loop closed on bytes that are still the
#: ones it decided on".
SEAL_READING: Dict[str, str] = {
    "holds": "\u7ed3\u8bba\u4ecd\u7136\u6210\u7acb",
    "drifted": "\u7ed3\u8bba\u5df2\u6f02\u79fb",
    "tampered": "\u5c01\u5b58\u88ab\u6539\u8fc7",
    "missing": "\u6ca1\u6709\u5c01\u5b58",
    "unrecomputable": "\u7ed3\u8bba\u65e0\u6cd5\u91cd\u7b97",
    "unknown-version": "\u5c01\u5b58\u683c\u5f0f\u8fc7\u65e7",
    "unknown": "\u672a\u77e5",
}


def seal_reading(state: Any) -> str:
    """The plain reading of a seal state, or the raw value."""
    key = str(state or "").strip()
    if not key:
        return ""
    return SEAL_READING.get(key, key)


def _seal_of(row: Dict[str, Any] | None) -> Dict[str, Any]:
    """The seal of one ledger row, or an empty dict.

    A seal is a dict, and a dict that is not a dict is not a seal.
    """
    seal = (row or {}).get("seal")
    return seal if isinstance(seal, dict) else {}


def _seal_state(seal: Dict[str, Any], artifact_path: str) -> Dict[str, Any]:
    """Check a sealed closure against the disk as it stands now.

    The report is the screen a human reads *after* the run, so it has
    to answer the question the run answered at the time: does the
    verdict still hold on the bytes that are on the disk now? A run
    that closed and was then edited underneath is not a closed run any
    more, and a report that cannot see the difference reports a
    result that no longer exists.

    The artifact text is read back from the disk, because the seal
    was taken over the bytes that were written there - re-computing
    the verdict against the text in the ledger would check the wrong
    thing.
    """
    if not seal:
        return {"state": "missing", "ok": False, "problems": []}
    from mas.seal import verify_seal

    artifact = None
    if artifact_path:
        try:
            artifact = pathlib.Path(artifact_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            artifact = None
    outcome = verify_seal(seal, artifact)
    outcome["state"] = str(outcome.get("state") or "unknown")
    outcome["ok"] = bool(outcome.get("ok"))
    return outcome


def _run_source(run: Dict[str, Any]) -> str:
    """Where the answer of one run came from, in a word.

    A run the loop closed by itself and a run a person closed by hand
    are both "closed" - but they are not the same achievement, and a
    report that cannot tell them apart quietly lets the machine take
    credit for a person's work. The word is short enough to sit in a
    table cell and honest enough to be worth sitting there.
    """
    seal_state = str(run.get("seal_state") or "")
    if seal_state and seal_state != "holds":
        return "\u26a0 \u7ed3\u8bba\u5df2\u6f02\u79fb"
    if run.get("handback"):
        return "🤝 人工交付物"
    if run.get("closed"):
        return "🤖 机器闭环"
    return "—"


def _thread_source(thread: Dict[str, Any]) -> str:
    """Where the answer of one problem came from, in a word."""
    seal_state = str(thread.get("seal_state") or "")
    if seal_state and seal_state != "holds":
        return "\u26a0 \u7ed3\u8bba\u5df2\u6f02\u79fb"
    if thread.get("handback"):
        return "🤝 人工完成"
    if thread.get("closed"):
        return "🤖 机器闭环"
    return "—"


#: The states an undo can leave a draft in, in words. A draft whose
#: approval was taken back is not "approved" any more - the newest
#: fact about it is that it was wrong once.
UNDO_STATE_READING: Dict[str, str] = {
    "undone": "撤销过",
    "approved": "已批准",
    "pending": "待批准",
    "rejected": "已拒绝",
}


def undo_reading(state: Any) -> str:
    """The plain reading of a draft state, or the raw value."""
    key = str(state or "").strip()
    if not key:
        return ""
    return UNDO_STATE_READING.get(key, key)


def undone_approvals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The approvals that were taken back - and the drafts they hit.

    The gap this closes
    ------------------
    The audit chain and the ledger both remember that an approval was
    taken back. ``mas undos`` can list them. But the *report* - the
    screen a human reads after the fact - said nothing about them:
    ``run_summary`` never even looked at the ``draft_approve`` and
    ``draft_undo`` rows, because a human decision about a draft is
    not a run.

    So a wrong approval was invisible in the main view. The ledger
    kept the truth; the report did not show it. That is the same
    shape of hole as v0.7.89's: a capability that exists and is not
    consumed by the view people actually read.

    How the pairing works
    ---------------------
    The pairing itself is **not** re-implemented here. ``mas.undos``
    already does it correctly - positionally, off a stack, with an
    undo that has nothing standing to cancel reported as unmatched
    rather than paired with a neighbour. This module only *reads*
    that answer.

    The records live in the workspace, not in a run
    ----------------------------------------------
    A draft belongs to a workspace, not to a run: the same draft id
    can be approved, taken back and approved again without any run
    being involved. So these are reported at the **workspace**
    level, next to the seal review, rather than attached to a run
    that never saw them.
    """
    from mas.undos import pair_decisions, undo_totals

    records, unmatched = pair_decisions(rows)
    totals = undo_totals(records)
    drafts: Dict[str, Dict[str, Any]] = {}
    for record in records:
        draft_id = str(record.get("draft_id") or "")
        if not draft_id:
            continue
        entry = drafts.setdefault(
            draft_id,
            {
                "draft_id": draft_id,
                "path": str(record.get("path") or ""),
                "workspace": str(record.get("workspace") or ""),
                "taken_back": 0,
                "reverted_lines": 0,
                "forced": False,
                # The newest fact about the draft. A draft that was
                # taken back and then approved again is approved *now*
                # - and it was still wrong once.
                "state": "undone",
                "last_undo_ts": "",
            },
        )
        entry["taken_back"] = int(entry.get("taken_back") or 0) + 1
        reverted = record.get("reverted_line_count")
        if isinstance(reverted, (int, float)):
            entry["reverted_lines"] = int(entry.get("reverted_lines") or 0) + int(reverted)
        if record.get("forced"):
            entry["forced"] = True
        undo_ts = str(record.get("undo_ts") or "")
        if undo_ts >= str(entry.get("last_undo_ts") or ""):
            entry["last_undo_ts"] = undo_ts
    return {
        "records": records,
        "unmatched": unmatched,
        "totals": totals,
        "drafts": list(drafts.values()),
        "draft_count": len(drafts),
    }


def audit_reading(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The audit chains of the decisions in this ledger - and their state.

    The gap this closes
    ------------------
    ``mas audit`` can check a chain, and ``mas status`` shows the
    result. The *report* - the screen a human reads after the fact -
    never looked at the chains at all. So a decision record that had
    been edited underneath was invisible in the main view: the ledger
    row said "approved", the chain said something else, and nothing in
    the report said so.

    That is the third hole of the same family - after the seal in
    v0.7.89 and the taken-back approval in v0.7.90: a capability that
    exists, and a main view that does not consume it.

    Which chains are looked at
    --------------------------
    The chains are found through the *rows themselves*: every row that
    knows its workspace carries it, so the report looks at exactly the
    chains that wrote those decisions - no more and no fewer.
    ``mas status`` guesses a folder instead (``<state_dir>/artifacts``);
    a report that only covered the default folder would miss a run
    that was given a workspace of its own, and a report that covered
    every folder would read chains that belong to nobody.

    The chain that is *missing*
    --------------------------
    A chain that was deleted whole leaves nothing to read: no problem
    is found by reading it. The ledger still holds the decision, so
    the report compares the two - a decision row whose workspace has
    no chain at all is a hole in the record, and it is named as one.
    """
    from mas.audit import audit_report

    workspaces: List[str] = []
    decisions: Dict[str, int] = {}
    for row in rows or []:
        workspace = str(row.get("workspace") or "").strip()
        if not workspace:
            continue
        if workspace not in workspaces:
            workspaces.append(workspace)
        if str(row.get("role") or "") in DRAFT_DECISION_ROLES:
            decisions[workspace] = decisions.get(workspace, 0) + 1

    chains: List[Dict[str, Any]] = []
    problems: List[str] = []
    entries = 0
    for workspace in workspaces:
        try:
            outcome = audit_report(workspace, rows)
        except Exception as exc:  # noqa: BLE001 - a finding, not a crash
            problems.append(
                "\u5de5\u4f5c\u533a %s \u7684\u5ba1\u8ba1\u94fe\u8bfb\u4e0d\u51fa\u6765\uff1a%s: %s"
                % (workspace, type(exc).__name__, exc)
            )
            continue
        for chain in outcome.get("chains") or []:
            chains.append(chain)
            entries += int(chain.get("entries") or 0)
        problems.extend(str(item) for item in (outcome.get("problems") or []))
        held = int(decisions.get(workspace) or 0)
        if held and not int(outcome.get("chain_count") or 0):
            problems.append(
                "\u5de5\u4f5c\u533a %s \u7684\u8d26\u672c\u8bb0\u7740 %d \u6761\u4eba\u5de5\u51b3\u5b9a\uff0c\u4f46\u5ba1\u8ba1\u94fe\u4e0d\u89c1\u4e86"
                % (workspace, held)
            )
    return {
        "workspaces": workspaces,
        "chains": chains,
        "chain_count": len(chains),
        "entries": entries,
        "problems": problems,
        "problem_count": len(problems),
        "ok": not problems,
    }


def ticket_reading(
    rows: List[Dict[str, Any]],
    tickets: Optional[List[Dict[str, Any]]] = None,
    ledger_complete: bool = True,
) -> Dict[str, Any]:
    """The human ticket queue, reconciled against the ledger.

    The gap this closes
    ------------------
    `mas tickets` shows the queue and `mas status` counts it, but
    the *report* - the screen a human reads after the fact - never
    looked at it. A ticket is the machine's way of saying "this needs
    a person", so a report that does not read the queue reports a
    machine that looks busy while problems sit waiting for a human.

    That is the fourth hole of the same family - after the seal in
    v0.7.89, the taken-back approval in v0.7.90 and the decision chain
    in v0.7.91: a capability that exists, and a main view that does
    not consume it.

    What is reconciled
    ------------------
    The ledger says which problem a run belonged to (`ticket_id`);
    the queue says whether that problem is still waiting. The two are
    read independently and then compared, so three kinds of hole can
    be named:

    * a run points at a ticket that is **not in the queue** - the
      record of the escalation is gone;
    * a ticket in the queue points at **no run in the ledger** - the
      human is being asked about a problem the ledger never recorded;
    * a ticket is still **waiting** although every run it covers
      **closed** - the queue asks a human for work that is already
      done.

    The third one is the dangerous direction: it is the queue that
    keeps the reader from trusting the queue.
    """
    queue = [t for t in (tickets or []) if isinstance(t, dict)]
    runs = [run_summary(group["run_id"], group["rows"]) for group in group_runs(rows or [])]
    closed_runs = {
        str(run.get("run_id") or ""): bool(run.get("closed")) for run in runs
    }
    known = {str(t.get("id") or "") for t in queue if str(t.get("id") or "")}

    # Which tickets the ledger talks about, and which runs cover them.
    ledger_tickets: Dict[str, List[str]] = {}
    for row in rows or []:
        ticket_id = str(row.get("ticket_id") or "")
        if not ticket_id:
            continue
        run_id = str(row.get("run_id") or "")
        bucket = ledger_tickets.setdefault(ticket_id, [])
        if run_id and run_id not in bucket:
            bucket.append(run_id)

    problems: List[str] = []
    entries: List[Dict[str, Any]] = []
    waiting_ids: List[str] = []
    resolved_ids: List[str] = []

    for ticket in queue:
        ticket_id = str(ticket.get("id") or "")
        payload = ticket.get("payload") or {}
        state = str(ticket.get("state") or "")
        if state == "waiting_human":
            waiting_ids.append(ticket_id)
        elif state == "resolved":
            resolved_ids.append(ticket_id)
        # The runs this ticket covers: the ones the ledger records for
        # it, plus the ones the ticket itself remembers. A ticket that
        # was resumed lists every attempt.
        covered = list(ledger_tickets.get(ticket_id) or [])
        for run_id in payload.get("runs") or []:
            run_id = str(run_id or "")
            if run_id and run_id not in covered:
                covered.append(run_id)
        if payload.get("run_id"):
            run_id = str(payload.get("run_id") or "")
            if run_id and run_id not in covered:
                covered.append(run_id)
        # Whether the problem is actually done. Only the runs that are
        # in *this* ledger can answer that.
        seen = [r for r in covered if r in closed_runs]
        all_closed = bool(seen) and all(closed_runs.get(r) for r in seen)
        reason = str(payload.get("reason") or "")
        entries.append(
            {
                "id": ticket_id,
                "kind": str(ticket.get("kind") or ""),
                "state": state,
                "title": str(payload.get("title") or payload.get("goal") or ""),
                "goal": str(payload.get("goal") or ""),
                "reason": reason,
                "reason_text": reason_text(reason),
                "hint": reason_hint(reason),
                "attempts": int(payload.get("attempts") or 0),
                "iterations_total": int(payload.get("iterations_total") or 0),
                "runs": covered,
                "run_count": len(covered),
                "created_at": str(ticket.get("created_at") or ""),
                "resolved_at": str(ticket.get("resolved_at") or ""),
                "decision": str(ticket.get("decision") or ""),
                "note": str(ticket.get("note") or ""),
                "workspace": str(payload.get("workspace") or ""),
                "closed_run_id": str(payload.get("closed_run_id") or ""),
                # The command that continues the ticket. A ticket that
                # is waiting and does not say what to do next is a
                # ticket that waits for ever.
                "continuation": continuation_command(
                    ticket_id, reason, payload.get("budget")
                ),
                # Whether every attempt on this problem closed.
                "all_closed": all_closed,
                "in_ledger": bool(ledger_tickets.get(ticket_id)),
            }
        )

    # A run that escalated points at a ticket. If the ticket is not in
    # the queue, the record of the escalation is gone.
    for ticket_id in sorted(ledger_tickets):
        if ticket_id not in known:
            problems.append(
                "\u5de5\u5355 %s \u4e0d\u89c1\u4e86\uff1a\u8d26\u672c\u91cc\u8fd9\u6b21\u8fd0\u884c\u6307\u5411\u5b83\uff0c"
                "\u4f46\u5de5\u5355\u961f\u5217\u91cc\u5df2\u7ecf\u6ca1\u6709\u5b83\u4e86" % ticket_id
            )
    # A ticket in the queue that the ledger never mentions: the human
    # is being asked about a problem the ledger does not hold. The
    # ticket may name no run at all, or name runs the ledger has
    # never heard of - both mean the same thing: the queue is asking
    # about work the ledger cannot account for.
    for entry in entries:
        if entry["in_ledger"]:
            continue
        if not ledger_complete:
            continue
        if not entry["runs"]:
            problems.append(
                "\u5de5\u5355 %s \u4e0d\u5728\u8d26\u672c\u91cc\uff1a\u961f\u5217\u91cc\u6709\u5b83\uff0c"
                "\u4f46\u8d26\u672c\u91cc\u6ca1\u6709\u5b83\u5bf9\u5e94\u7684\u4efb\u4f55\u8fd0\u884c" % entry["id"]
            )
            continue
        # It names runs - but none of them is in this ledger.
        named = ", ".join(entry["runs"][:3])
        problems.append(
            "\u5de5\u5355 %s \u6307\u5411\u7684\u8fd0\u884c\u4e0d\u5728\u8d26\u672c\u91cc\uff1a%s\uff08\u961f\u5217\u5728\u95ee\u4e00\u4e2a"
            "\u8d26\u672c\u4e0d\u8bb0\u5f97\u7684\u95ee\u9898\uff09" % (entry["id"], named)
        )
    # A ticket that is still waiting although everything it covers
    # closed: the queue asks for work that is already done.
    for entry in entries:
        if entry["state"] != "waiting_human" or not entry["all_closed"]:
            continue
        problems.append(
            "\u5de5\u5355 %s \u8fd8\u5728\u7b49\u4eba\u63a5\uff0c\u4f46\u5b83\u8986\u76d6\u7684\u8fd0\u884c\u5df2\u7ecf\u95ed\u73af\u4e86"
            "\uff08\u961f\u5217\u5728\u8981\u4eba\u505a\u5df2\u7ecf\u505a\u5b8c\u7684\u4e8b\uff09" % entry["id"]
        )
    return {
        "tickets": entries,
        "ticket_count": len(entries),
        "waiting": waiting_ids,
        "resolved": resolved_ids,
        "waiting_count": len(waiting_ids),
        "resolved_count": len(resolved_ids),
        "problems": problems,
        "problem_count": len(problems),
        "ok": not problems,
    }


def build_report(
    rows: List[Dict[str, Any]],
    tickets: Optional[List[Dict[str, Any]]] = None,
    ledger_complete: bool = True,
    drafts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the full report payload for a set of ledger rows.

    The ticket queue is read as well when it is handed in. The ledger
    says what the machine did; the queue says what is still waiting for
    a person. A report that reads only the ledger cannot tell the
    reader that a problem is sitting in the human queue.

    The draft queue is read too, for the same reason and with a
    sharper edge: a run that wanted to *change* a file did not change
    it - it staged a draft - and the machine checks judge the draft,
    so the loop can close while the disk still holds the old bytes.
    A report that never reads the drafts reports that as a closed loop
    and says nothing about the change that is still waiting for a
    person.
    """
    runs = [run_summary(run["run_id"], run["rows"]) for run in group_runs(rows)]
    closed = [r for r in runs if r["closed"]]
    failed_runs = [r for r in runs if not r["closed"]]
    threads = group_threads(runs)
    open_threads = [t for t in threads if not t["closed"]]
    # The failed attempts that are still waiting, as opposed to the
    # failed attempts that were *superseded*.
    #
    # A problem that failed once and then succeeded is closed: the
    # later attempt replaced the earlier one. Counting the earlier
    # failure as "still waiting" is how a resumed problem stays in
    # the open queue forever - and the report would then say the
    # thread is closed while the verdict says it is not, which is
    # two answers to one question.
    #
    # So the two counts are kept apart on purpose:
    #
    # * ``not_closed`` - every attempt that did not close. History.
    # * ``not_closed_open`` - the failed attempts that belong to a
    #   problem that is *still open*. Work that is still waiting.
    #
    # The closure verdict is built on the second one, because the
    # question it answers is "is anything still waiting?", not "did
    # every try succeed?".
    open_thread_ids = {str(t.get("thread_id") or "") for t in open_threads}
    failed_open = [
        r for r in failed_runs if thread_of(r) in open_thread_ids
    ]
    failed_superseded = [r for r in failed_runs if r not in failed_open]
    # The sealed conclusions, checked against the disk as it stands
    # now. A report that says "3 closed" and does not say whether
    # those three conclusions still hold is a report about the past
    # pretending to be a report about the present.
    sealed_runs = [r for r in runs if r.get("seal_state")]
    drifted_runs = [
        r for r in sealed_runs if str(r.get("seal_state") or "") != "holds"
    ]
    # The approvals that were taken back. A wrong approval that the
    # report does not mention is a wrong approval the reader has to
    # find in the audit chain by hand.
    undos = undone_approvals(rows)
    # The audit chains of the decisions. The chain is the record that
    # says a decision was written once and cannot be changed quietly;
    # if the report does not read it, a decision that *was* changed
    # quietly looks exactly like one that was not.
    audit = audit_reading(rows)
    # The human ticket queue, and what it says that the ledger does
    # not. A ticket is the machine's way of saying "this needs a
    # person", so a report that never reads the queue reports a busy
    # machine while problems wait.
    queue = ticket_reading(rows, tickets, ledger_complete=ledger_complete)
    # The draft queue. A run that proposed a change instead of making
    # it leaves a draft behind, and the report has to say so: the run
    # may well be closed, but the *file* is still the old one.
    draft_queue = draft_reading(rows, drafts, runs=runs)
    return {
        "generated_at": now_iso(),
        "runs": runs,
        # The seal, reconciled against the disk. A JSON consumer
        # asks the report for the facts; the seal reconciliation is
        # a fact about the present, so it belongs here and not only
        # in the rendered Markdown.
        "seal": {
            "sealed": len(sealed_runs),
            "holds": len(sealed_runs) - len(drifted_runs),
            "drifted": len(drifted_runs),
            "ok": not drifted_runs,
            "states": {
                str(run.get("run_id") or ""): str(run.get("seal_state") or "")
                for run in sealed_runs
            },
        },
        # The approvals that were taken back. The ledger and the audit
        # chain both remember them; this is where the report says so.
        "undos": undos,
        # The audit chains, and whether they still verify. A chain that
        # was changed on the disk is a fact about the present, so it
        # belongs in the payload and not only in the rendered Markdown.
        "audit": audit,
        # The human ticket queue, reconciled against the ledger. The
        # queue is what is waiting for a person, so it belongs in the
        # payload and not only in the rendered Markdown.
        "tickets": queue,
        # The draft queue, reconciled against the ledger. A draft is a
        # change the loop proposed and did not make, so it is also
        # work that is waiting for a person - and a closed loop whose
        # draft is still pending is a loop whose file is still old.
        "drafts": draft_queue,
        # The problems, as opposed to the attempts. One unfinished
        # problem that was resumed three times is one open problem and
        # four runs; a report that only counts runs makes the human
        # queue look four times as long as it is.
        "threads": threads,
        "totals": {
            "runs": len(runs),
            "closed": len(closed),
            "not_closed": len(failed_runs),
            # The failed attempts that are still waiting, and the ones
            # a later attempt replaced. Two different facts, and the
            # closure verdict is built on the first.
            "not_closed_open": len(failed_open),
            "not_closed_superseded": len(failed_superseded),
            # How many distinct problems the ledger holds.
            "problems": len(threads),
            "problems_open": len(open_threads),
            "problems_closed": len(threads) - len(open_threads),
            # Two closure rates, because there are two grains.
            #
            # ``closure_rate`` counts attempts: of the runs that were
            # made, how many closed. ``problem_closure_rate`` counts
            # problems: of the things the human had to deal with, how
            # many are done. A problem that failed and then succeeded
            # is one problem, closed - but two attempts, one of which
            # failed. Printing the attempt rate next to a verdict of
            # "closed" reads like a contradiction, and it is one:
            # two grains, one number.
            "closure_rate": round(len(closed) / len(runs), 4) if runs else 0.0,
            "problem_closure_rate": (
                round((len(threads) - len(open_threads)) / len(threads), 4)
                if threads
                else 0.0
            ),
            "entries": len(rows),
            # The spend of every run in this report.
            "tokens": sum(int(run.get("tokens") or 0) for run in runs),
            # How many closures carry a seal, and how many of those
            # still hold on the disk as it stands now.
            "sealed": len(sealed_runs),
            "seal_holds": len(sealed_runs) - len(drifted_runs),
            "seal_drifted": len(drifted_runs),
            # How many human approvals were taken back, and how many
            # drafts that touched. These are counts of *wrong
            # decisions*, not of runs - a wrong approval does not
            # belong to any run, so it is counted here.
            "approvals_taken_back": int(undos.get("totals", {}).get("undone") or 0),
            "drafts_taken_back": int(undos.get("draft_count") or 0),
            "lines_reverted": int(undos.get("totals", {}).get("reverted_lines") or 0),
            # An undo with no standing approval cannot be paired.
            # That is a hole in the record, and it is counted rather
            # than hidden.
            "undos_unmatched": len(undos.get("unmatched") or []),
            # The decision chains, and what is wrong with them. A
            # record that was changed quietly is a fact the reader
            # needs, not a detail of the implementation.
            "audit_chains": int(audit.get("chain_count") or 0),
            "audit_entries": int(audit.get("entries") or 0),
            "audit_problems": int(audit.get("problem_count") or 0),
            # The human queue. A report that counts the runs but never
            # the tickets that are waiting for a person reports a
            # machine that is busier than it is.
            "tickets": int(queue.get("ticket_count") or 0),
            "tickets_waiting": int(queue.get("waiting_count") or 0),
            "tickets_resolved": int(queue.get("resolved_count") or 0),
            "ticket_problems": int(queue.get("problem_count") or 0),
            # The draft queue. A pending draft is a change the loop
            # proposed and a human has to approve - and a run that
            # closed with a pending draft did not actually write.
            "drafts": int(draft_queue.get("draft_count") or 0),
            "drafts_pending": int(draft_queue.get("pending_count") or 0),
            "drafts_approved": int(draft_queue.get("approved_count") or 0),
            "draft_problems": int(draft_queue.get("problem_count") or 0),
            # A run that closed while its change was still waiting.
            # This is the count of "closed but the disk says otherwise".
            "runs_closed_with_pending_drafts": int(
                draft_queue.get("closed_with_pending_count") or 0
            ),
        },
    }


def _draft_section_lines(report: Dict[str, Any]) -> List[str]:
    """The section that names the changes the loop proposed but did not make.

    It is a section of its own because it is a different kind of
    fact: not a run, not a problem, not a request - a *change that is
    waiting*. A draft says "the loop wanted to write this, and nobody
    has said yes yet". A report that only shows runs and problems
    leaves the reader with a closed loop and an unchanged file, and
    nothing in between to explain it.

    The sharpest line in the section is the one about a closed run:
    the loop reported success, the deliverable passed the machine
    checks, and the file on the disk is still the old one - because
    the change is still in front of a human.
    """
    lines: List[str] = []
    queue = report.get("drafts") or {}
    entries = list(queue.get("drafts") or [])
    problems = list(queue.get("problems") or [])
    waiting = int(queue.get("pending_count") or 0)
    approved = int(queue.get("approved_count") or 0)
    if not entries and not problems:
        return lines
    lines.append("## 待批草稿")
    lines.append("")
    if entries:
        lines.append(
            "%d 张草稿，其中 %d 张等人批、%d 张已批准。"
            "草稿是循环**提议**但没有落地的改动 —— "
            "文件在批准之前一个字节都不会变。"
            % (len(entries), waiting, approved)
        )
        lines.append("")
        lines.append("| 草稿 | 状态 | 文件 | 提议运行 | 原因 | 接着干 |")
        lines.append("|---|---|---|---|---|---|")
        for entry in entries:
            path = str(entry.get("path") or "").replace("|", "\\|")[:40]
            reason = str(entry.get("reason") or "").replace("|", "\\|")[:50]
            state_text = str(entry.get("state_reading") or entry.get("state") or "-")
            run_id = str(entry.get("run_id") or "")
            command = (
                "mas drafts approve %s" % entry.get("draft_id")
                if str(entry.get("state") or "") == "pending"
                else "-"
            )
            lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    entry.get("draft_id") or "-",
                    state_text,
                    path or "-",
                    run_id or "-",
                    reason or "-",
                    command,
                )
            )
        lines.append("")
    if problems:
        lines.append("草稿队列和账本对不上，下面这些问题需要人看一眼：")
        lines.append("")
        for problem in problems:
            lines.append("- %s" % problem)
        lines.append("")
    return lines


def _undo_section_lines(report: Dict[str, Any]) -> List[str]:
    """The section that names the approvals that were taken back.

    It is a section of its own because it is a different kind of
    fact: not a run, not a problem, but a *human decision that
    turned out to be wrong*. It belongs next to the problems, not
    buried in the run table.

    A ledger that holds nothing but decisions has no runs at all,
    and the report still has to say this - otherwise it would tell
    the reader "the ledger is empty" while the ledger is full of
    approvals that turned out to be wrong.
    """
    lines: List[str] = []
    undos = report.get("undos") or {}
    undone_drafts = undos.get("drafts") or []
    unmatched = undos.get("unmatched") or []
    # A section with only unmatched undos is still a section: the
    # record holds decisions, and saying nothing about it would be
    # saying nothing about a hole in the record.
    if not undone_drafts and not unmatched:
        return lines
    lines.append("## 被撤销的批准")
    lines.append("")
    if undone_drafts:
        lines.append(
            "这些是被人撤回去的批准 —— "
            "它们曾经错误，现在这一点被记下来了。"
        )
        lines.append("")
    if undone_drafts:
        lines.append(
            "| 草稿 | 文件 | 被撤销次数 | "
            "退回行数 | 强制 | 最后一次撤销 |"
        )
        lines.append("|---|---|---|---|---|---|")
    for entry in undone_drafts:
        draft_id = str(entry.get("draft_id") or "-")
        path = str(entry.get("path") or "-").replace("|", "\\|")
        reverted = int(entry.get("reverted_lines") or 0)
        lines.append(
            "| `%s` | %s | %d | %s | %s | %s |" % (
                draft_id,
                path,
                int(entry.get("taken_back") or 0),
                str(reverted) if reverted else "-",
                "是" if entry.get("forced") else "否",
                str(entry.get("last_undo_ts") or "-"),
            )
        )
    lines.append("")
    if unmatched:
        lines.append(
            "另有 %d 次撤销找不到对应的批准 "
            "—— 记录里有撤销，但没有能配对的批准，"
            "它们不被计入上表。" % len(unmatched)
        )
        lines.append("")
    return lines


def _audit_section_lines(report: Dict[str, Any]) -> List[str]:
    """The section that names what is wrong with the decision chains.

    It is a section of its own because it is about the *record* and
    not about a run: a chain that no longer verifies means a decision
    a person made can no longer be trusted to be the decision they
    made. That is worth its own heading, not a footnote.
    """
    lines: List[str] = []
    audit = report.get("audit") or {}
    problems = list(audit.get("problems") or [])
    chains = int(audit.get("chain_count") or 0)
    entries = int(audit.get("entries") or 0)
    if not chains and not problems:
        return lines
    lines.append("## 审计链复核")
    lines.append("")
    if problems:
        lines.append(
            "审计链是人工决定的不可篡改记录。下面这些问题说明"
            "记录与链已经对不上，或链本身被改过："
        )
        lines.append("")
        for problem in problems:
            lines.append("- %s" % problem)
        lines.append("")
    else:
        lines.append(
            "%d 条审计链、%d 条记录全部完好："
            "每一条人工决定都还是写下去时的那一条。"
            % (chains, entries)
        )
        lines.append("")
    return lines


def _ticket_section_lines(report: Dict[str, Any]) -> List[str]:
    """The section that names the tickets waiting for a human.

    It is a section of its own because it is a different kind of fact:
    not a run, not a problem, but a *request*. A ticket says "this
    needs a person" - and a report that only shows runs and problems
    leaves the reader with work that is waiting and no sign of it.
    """
    lines: List[str] = []
    queue = report.get("tickets") or {}
    entries = list(queue.get("tickets") or [])
    problems = list(queue.get("problems") or [])
    waiting = int(queue.get("waiting_count") or 0)
    resolved = int(queue.get("resolved_count") or 0)
    if not entries and not problems:
        return lines
    lines.append("## 人工工单")
    lines.append("")
    if entries:
        lines.append(
            "%d 张工单，其中 %d 张在等人接、%d 张已解决。"
            "工单是机器交给人的活 —— 它必须出现在复盘里，"
            "否则人看到的是一台忙着跑、却没人接活的机器。"
            % (len(entries), waiting, resolved)
        )
        lines.append("")
        lines.append("| 工单 | 状态 | 问题 | 原因 | 尝试 | 关联运行 | 接着干 |")
        lines.append("|---|---|---|---|---|---|---|")
        for entry in entries:
            label = str(entry.get("title") or entry.get("goal") or "")
            label = label.replace("|", "\\|")[:40]
            state = str(entry.get("state") or "")
            if state == "waiting_human":
                state_text = "等人接"
            elif state == "resolved":
                state_text = "已解决"
            else:
                state_text = state or "-"
            why = str(entry.get("reason_text") or entry.get("reason") or "")
            why = why.replace("|", "\\|")[:60]
            runs = entry.get("runs") or []
            runs_text = "%d 次" % len(runs) if runs else "-"
            command = (
                str(entry.get("continuation") or "")
                if state == "waiting_human"
                else "-"
            )
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s |"
                % (
                    entry.get("id") or "-",
                    state_text,
                    label or "-",
                    why or "-",
                    int(entry.get("attempts") or 0),
                    runs_text,
                    command or "-",
                )
            )
        lines.append("")
    if problems:
        lines.append("队列和账本对不上，下面这些问题需要人看一眼：")
        lines.append("")
        for problem in problems:
            lines.append("- %s" % problem)
        lines.append("")
    return lines


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report as Markdown."""
    totals = report.get("totals") or {}
    lines: List[str] = ["# OpenLoop MAS 闭环复盘报告", ""]
    lines.append(f"生成时间：{report.get('generated_at', '')}")
    lines.append(
        f"运行总数：{totals.get('runs', 0)}　已闭环：{totals.get('closed', 0)}　"
        f"未闭环：{totals.get('not_closed', 0)}　尝试闭环率：{totals.get('closure_rate', 0):.0%}"
    )
    # Runs are attempts; problems are what the human actually has to
    # deal with. Saying both stops the report from looking like three
    # open problems when there is one.
    lines.append(
        f"待解决问题：{totals.get('problems_open', 0)} 个"
        f"（共 {totals.get('problems', 0)} 个问题，"
        f"{totals.get('problems_closed', 0)} 个已解决，"
        f"问题闭环率 {totals.get('problem_closure_rate', 0):.0%}）"
    )
    lines.append(f"总花费：{int(totals.get('tokens', 0) or 0)} token")
    # The human queue, in the headline. The report says how much the
    # machine did; it has to say how much is still waiting for a
    # person, or the reader thinks the machine finished everything.
    waiting_tickets = int(totals.get("tickets_waiting") or 0)
    if waiting_tickets:
        lines.append(
            "人工工单：%d 张等人接（共 %d 张）"
            % (waiting_tickets, int(totals.get("tickets") or 0))
        )
    # The changes that were proposed and not made, in the headline. A
    # report that says "3 closed" and never says "2 of those changes
    # are still in front of a human" hands the reader a closed machine
    # and an unchanged disk.
    pending_drafts = int(totals.get("drafts_pending") or 0)
    if pending_drafts:
        lines.append(
            "待批草稿：%d 张等人批（共 %d 张）"
            % (pending_drafts, int(totals.get("drafts") or 0))
        )
    # A run that closed while its change was still waiting. This is
    # the sharpest thing the report can say, so it goes in the
    # headline and not only in a section further down.
    closed_pending = int(totals.get("runs_closed_with_pending_drafts") or 0)
    if closed_pending:
        lines.append(
            "闭环待写：%d 次运行报了闭环，但它的改动还排在人前面"
            "（文件没变）" % closed_pending
        )
    # The seal, in the headline. A report that says "1 closed" and
    # does not say whether that conclusion still holds leaves the
    # reader to find out the hard way.
    sealed = int(totals.get("sealed") or 0)
    if sealed:
        drifted = int(totals.get("seal_drifted") or 0)
        if drifted:
            lines.append(
                f"封存复核：{sealed} 条封存中，"
                f"{drifted} 条结论已漂移（判定之后有东西被改过）"
            )
        else:
            lines.append(
                f"封存复核：{sealed} 条封存全部仍然成立"
            )
    # The approvals that were taken back, in the headline. A report
    # that says "3 closed" and never says "2 of your approvals were
    # wrong" leaves the reader with a clean-looking machine and a
    # wrong history.
    taken_back = int(totals.get("approvals_taken_back") or 0)
    if taken_back:
        drafts_back = int(totals.get("drafts_taken_back") or 0)
        reverted = int(totals.get("lines_reverted") or 0)
        lines.append(
            f"撤销回收：{taken_back} 次批准被撤销"
            f"（{drafts_back} 个草稿"
            + (f"，退回 {reverted} 行" if reverted else "")
            + "）"
        )
    # An undo that could not be paired is a hole in the record. It is
    # reported next to the headline even when nothing could be paired
    # at all - a ledger of only unmatched undos must not read like a
    # ledger with nothing in it.
    unmatched = int(totals.get("undos_unmatched") or 0)
    if unmatched:
        lines.append(
            f"撤销配对：{unmatched} 次撤销找不到"
            f"对应的批准（记录有缺口）"
        )
    # The decision chains, in the headline. A report that says "3
    # closed" and never says "one of your decisions was changed on
    # disk" hands the reader a clean-looking history that is not the
    # one on the disk.
    chains = int(totals.get("audit_chains") or 0)
    audit_problems = int(totals.get("audit_problems") or 0)
    if chains or audit_problems:
        entries = int(totals.get("audit_entries") or 0)
        if audit_problems:
            lines.append(
                f"\u5ba1\u8ba1\u94fe\u590d\u6838\uff1a{chains} \u6761\u94fe\u3001"
                f"{entries} \u6761\u8bb0\u5f55\uff0c"
                f"{audit_problems} \u4e2a\u95ee\u9898\uff08\u4eba\u5de5\u51b3\u5b9a\u7684\u8bb0\u5f55\u4e0d\u53ef\u4fe1\uff09"
            )
        else:
            lines.append(
                f"\u5ba1\u8ba1\u94fe\u590d\u6838\uff1a{chains} \u6761\u94fe\u3001"
                f"{entries} \u6761\u8bb0\u5f55\u5168\u90e8\u5b8c\u597d"
            )
    lines.append("")

    runs = report.get("runs") or []
    if not runs:
        # A ledger that holds only human decisions has no runs in it.
        # The report must still say what it *does* hold, because
        # "the ledger is empty" would be a false statement: the ledger
        # is full of approvals that turned out to be wrong.
        undos_only = report.get("undos") or {}
        if (
            undos_only.get("drafts")
            or undos_only.get("unmatched")
            or int((report.get("totals") or {}).get("audit_chains") or 0)
            or int((report.get("totals") or {}).get("audit_problems") or 0)
            or int((report.get("totals") or {}).get("tickets") or 0)
            or int((report.get("totals") or {}).get("ticket_problems") or 0)
            or int((report.get("totals") or {}).get("drafts") or 0)
            or int((report.get("totals") or {}).get("draft_problems") or 0)
        ):
            lines.extend(_ticket_section_lines(report))
            lines.extend(_draft_section_lines(report))
            lines.extend(_undo_section_lines(report))
            lines.extend(_audit_section_lines(report))
            lines.append("这台机器上还没有跑过任何一次闭环，但账本里记着人工的决定。")
            lines.append("  看这些决定：mas undos")
            lines.append("  先跑一个真任务：mas run --goal \"你的任务\" --criteria \"contains:关键词\"")
            return "\n".join(lines) + "\n"
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
            # A problem whose conclusion was edited underneath is not
            # solved any more - it is "solved on paper". The overview
            # table is the first thing a reader looks at, so the drift
            # has to be visible *there*, not only in the detail of one
            # run.
            seal_state = str(thread.get("seal_state") or "")
            if seal_state and seal_state != "holds":
                state = "结论已漂移"
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

    lines.extend(_ticket_section_lines(report))
    lines.extend(_draft_section_lines(report))
    lines.extend(_undo_section_lines(report))
    lines.extend(_audit_section_lines(report))
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
        # A conclusion that no longer holds is not an "OK". The run
        # did close at the time - that is a fact about the past -
        # but the result on the disk today is not the one that was
        # judged, so the table must not put a green mark next to it.
        seal_state = str(run.get("seal_state") or "")
        drifted = bool(seal_state) and seal_state != "holds"
        if drifted:
            icon = "DRF"
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
        if drifted:
            status_cell = (
                f"结论已漂移（{status_cell}）"
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
        # Does the conclusion still hold? The seal was taken at the
        # moment of the decision; this is the check that says whether
        # the bytes it was decided on are still the bytes on the disk.
        # A closure that was edited afterwards has not closed - it has
        # only closed on paper - and the report has to say so here,
        # where the reader decides whether to trust the result.
        seal_state = str(run.get("seal_state") or "")
        if seal_state == "holds":
            lines.append(
                "- 封存：✓ 结论仍然成立"
                "（判定所依据的字节没有变过）"
            )
        elif seal_state:
            reading = seal_reading(seal_state)
            lines.append(f"- 封存：⚠ **{reading}**")
            for problem in run.get("seal_problems") or []:
                lines.append(f"  - {problem}")
            for path in run.get("seal_drifted_files") or []:
                lines.append(f"  - 文件被改过：`{path}`")
            for path in run.get("seal_missing_files") or []:
                lines.append(f"  - 文件不在原处：`{path}`")
            model_judged = run.get("seal_model_judged") or []
            if model_judged:
                lines.append(
                    "  - 以下标准只能由模型判定，无法重算："
                    + "、".join(f"`{cid}`" for cid in model_judged)
                )
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
    if role in ("draft_approve", "draft_undo"):
        parts = ["人工" + ("撤销了改动" if role == "draft_undo" else "批准了改动")]
        path = str(row.get("path") or "")
        if path:
            parts.append(path)
        note = str(row.get("note") or "")
        if note:
            parts.append(note)
        return "；".join(parts)[:300]
    if role == "reflector":
        payload = _reflection_payload(row)
        issues = payload.get("issues") or []
        if isinstance(issues, list) and issues:
            return (
                "\uff1b".join(issue_reading(i) for i in issues)
            )[:300]
    if role == "write":
        written = row.get("written") or []
        parts = []
        for item in written:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("absolute") or "").strip()
            if not path:
                continue
            verb = "新建了" if item.get("created") else "写了"
            size = item.get("bytes")
            size_text = ""
            if size is not None:
                with contextlib.suppress(TypeError, ValueError):
                    size_text = "%d 字节" % int(size)
            parts.append((verb + path + ("（" + size_text + "）" if size_text else "")))
        errors = row.get("errors") or []
        for item in errors:
            text = str(item).strip()
            if text:
                parts.append("写入出错：" + text)
        if parts:
            return "；".join(parts)[:300]
    if role == "draft":
        drafts = row.get("drafts") or []
        parts = []
        for item in drafts:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("absolute") or "").strip()
            ident = str(item.get("id") or "").strip()
            if not path:
                continue
            parts.append("提议改动 " + path + (("（" + ident + "）") if ident else ""))
        if parts:
            return "；".join(parts)[:300]
    if role == "truncated":
        parts = []
        error = str(row.get("error") or "").strip()
        if error:
            parts.append("回答被截断：" + error)
        if row.get("retry"):
            parts.append("将重试")
        return "；".join(parts)[:300]
    if role == "reask":
        missing = [str(m) for m in (row.get("missing") or []) if str(m).strip()]
        if missing:
            return "补问未满足的标准：" + "、".join(missing)[:280]
    if role == "reask_failed":
        error = str(row.get("error") or "").strip()
        if error:
            return "补问失败：" + error[:280]
    if role == "lead":
        lead = row.get("lead")
        if isinstance(lead, dict) and lead:
            for key in ("summary", "advice", "reason", "decision", "verdict"):
                value = str(lead.get(key) or "").strip()
                if value:
                    return value[:300]
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
    if role == "write":
        # The row that says what the engine actually put on the disk.
        # The trace had no branch for it, so the one line that proves
        # the deliverable reached a file rendered as an empty string -
        # and an audit of "what did the agent change on my disk" had
        # nothing to read.
        parts: List[str] = []
        written = row.get("written") or []
        for item in written:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("absolute") or "").strip()
            if not path:
                continue
            bits = [path]
            size = item.get("bytes")
            if size is not None:
                with contextlib.suppress(TypeError, ValueError):
                    bits.append("%d 字节" % int(size))
            if item.get("created"):
                bits.append("新建")
            elif item.get("written") is False:
                bits.append("未写入")
            parts.append(" ".join(bits))
        errors = row.get("errors") or []
        for item in errors:
            text = str(item).strip()
            if text:
                parts.append("写入出错：" + text)
        if parts:
            return "；".join(parts)[:300]
        return "没有文件被写入"
    if role == "draft":
        # The row that says the loop proposed a change instead of
        # making it. The trace had no branch for it, so the row that
        # names the file a human still has to approve rendered blank.
        drafts = row.get("drafts") or []
        parts = []
        for item in drafts:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("absolute") or "").strip()
            ident = str(item.get("id") or "").strip()
            bits = []
            if ident:
                bits.append(ident)
            if path:
                bits.append(path)
            if not bits:
                continue
            parts.append(" ".join(bits))
        if parts:
            return "；".join(parts)[:300]
        return "提议了改动"
    if role == "truncated":
        # The call ran out of room mid-answer. The row is charged to the
        # budget, and it used to render as an empty string - so a run
        # that had to retry showed nothing about why it retried.
        parts = []
        error = str(row.get("error") or "").strip()
        if error:
            parts.append(error)
        tokens = row.get("tokens")
        if tokens is not None:
            with contextlib.suppress(TypeError, ValueError):
                parts.append("已用 %d tokens" % int(tokens))
        if row.get("retry"):
            parts.append("将重试")
        return "；".join(parts)[:300]
    if role == "reask":
        # The follow-up question the loop asked because a criterion was
        # not met yet. It used to render blank, so the trace showed a
        # question going out with no question in it.
        missing = row.get("missing") or []
        names = [str(m) for m in missing if str(m).strip()]
        if names:
            return "补问：" + "、".join(names)[:280]
        return "补问"
    if role == "reask_failed":
        # The follow-up question failed to be answered. The row carries
        # the reason, and it used to render blank.
        parts = []
        error = str(row.get("error") or "").strip()
        if error:
            parts.append(error)
        missing = row.get("missing") or []
        names = [str(m) for m in missing if str(m).strip()]
        if names:
            parts.append("未满足：" + "、".join(names))
        return "；".join(parts)[:300]
    if role == "lead":
        # The advisor's answer. It used to render blank, so the trace
        # showed a consulted advisor saying nothing.
        lead = row.get("lead")
        if isinstance(lead, dict) and lead:
            for key in ("summary", "advice", "reason", "decision", "verdict"):
                value = str(lead.get(key) or "").strip()
                if value:
                    return value[:300]
            return "、".join(str(k) for k in lead)[:300]
        return "顾问已咨询"
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
    if role in ("draft_approve", "draft_undo"):
        # A human decision that reached the ledger. The detail says
        # which file, and - for an undo - what the undo put back, so a
        # reader of the table learns that a wrong approval was taken
        # back without having to open the chain.
        parts = [str(row.get("path") or "-")]
        sha = str(row.get("current_sha256") or row.get("approved_sha256") or "")
        if sha:
            parts.append("文件 " + sha[:10])
        note = str(row.get("note") or "")
        if note:
            parts.append("理由：" + note)
        if role == "draft_undo":
            lost = int(row.get("would_lose_count") or 0)
            parts.append("退回改动前" + ("（会丢 %d 行）" % lost if lost else ""))
        if row.get("forced"):
            parts.append("强制")
        return "；".join(parts)[:300]
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

def artifact_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The deliverables this machine has produced, one row each.

    The gap this closes
    ------------------
    ``mas artifacts`` could list what the loop produced, and nothing
    else could. That command answered the only question that matters
    a week later - "what did I actually get?" - and it answered it on
    the terminal only.

    MCP is the main road into this project: an agent driving Claude,
    GPT or Kimi holds the MCP tools, not the CLI. Before this function
    had a home, such an agent could run a loop, read the report, replay
    the ledger and audit the decisions - and still could not ask
    "where is the file I asked for?".

    So the listing was written twice in the wrong direction: once as a
    command that only a terminal could reach. This module is the one
    implementation, and both surfaces call it.

    What a row holds
    ----------------
    Not just a path. A path alone is not a list a person can act on -
    they would have to open each file to find out which problem it
    solved. So a row names the task, the status, the size, the folder
    the run worked in and the other files it produced.

    A row that is *missing* is said so. A deliverable that was deleted
    or moved afterwards still appears, flagged - because "the file is
    gone" is a fact the reader needs, and dropping the row would hide
    it.
    """
    items: List[Dict[str, Any]] = []
    for group in group_runs(rows):
        summary = run_summary(group["run_id"], group["rows"])
        path = str(summary.get("artifact_path") or "")
        if not path:
            # A run that produced no deliverable is not a deliverable.
            # Listing it would put a row in the list that has nothing
            # in it, and the reader would look for a file that was
            # never written.
            continue
        target = pathlib.Path(path)
        items.append(
            {
                "run_id": summary["run_id"],
                "title": summary.get("title") or "",
                "goal": summary.get("goal") or "",
                "status": summary["status"],
                "path": path,
                "exists": target.exists(),
                "chars": int(summary.get("artifact_chars") or 0),
                "tokens": int(summary.get("tokens") or 0),
                "iterations": int(summary.get("iterations") or 0),
                "finished_at": summary.get("finished_at") or "",
                "workspace": str(summary.get("workspace") or ""),
                "files": list(summary.get("files") or []),
            }
        )
    return items


def format_artifacts(items: List[Dict[str, Any]]) -> str:
    """Say what the machine produced, in words a person can act on."""
    if not items:
        return "还没有任何交付物。跑一次 `mas run` 就会生成。"

    lines: List[str] = []
    lines.append("共 %d 份交付物：" % len(items))
    for item in items:
        flag = "OK " if item.get("exists") else "缺失"
        # The task first: the path is long and the task is short, and
        # the task is what the reader is looking for.
        label = item.get("title") or item.get("goal") or "-"
        status = str(item.get("status") or "")
        lines.append(
            "  [%s] %s  %s  %6d 字  %d 轮"
            % (
                flag,
                label,
                _both(status, status_reading(status)),
                int(item.get("chars") or 0),
                int(item.get("iterations") or 0),
            )
        )
        lines.append("        %s  %s" % (item.get("run_id") or "", item.get("path") or ""))
        files = item.get("files") or []
        if files:
            names = "、".join(
                str(row.get("path") or "") for row in files if row.get("path")
            )
            if names:
                lines.append("        另外生成：%s" % names)
    missing = [item for item in items if not item.get("exists")]
    if missing:
        lines.append("")
        lines.append(
            "⚠ 有 %d 份交付物文件已不在原处（可能被人删了或换了目录）。" % len(missing)
        )
    return "\n".join(lines)


def closure_verdict(report: Dict[str, Any]) -> Dict[str, Any]:
    """Answer one question about a report: does this close, and if not, why.

    The gap this closes
    ------------------
    A report shows everything - the runs, the threads, the queue, the
    drafts, the seal, the audit chains. But a reader holding a report
    still has to *judge* it: is this closed? Which part is open? The
    reader has to know the rules to read the facts, and the rules
    lived only in the head of whoever wrote the report.

    So "closed" was a thing you could show and not a thing you could
    ask. An MCP caller - an agent holding the tools - could read every
    number in the report and still not be able to say whether the work
    is done, because the verdict was never computed anywhere.

    This function is that computation, in one place. Both surfaces call
    it, so the CLI and the MCP server cannot answer differently.

    What "closed" means here
    -----------------------
    Not "the runs succeeded". A loop is closed when *nothing is left
    waiting*: no problem thread is open, no ticket is waiting for a
    person, no draft is pending, no seal has drifted, and no decision
    chain is broken.

    The grain is the *problem*, not the attempt
    -------------------------------------------
    A problem that failed once and then succeeded is closed. The later
    attempt replaced the earlier one - that is what a resume is for.
    So the verdict counts the failed attempts that belong to a problem
    that is still open, and a failure that was superseded does not
    hold the loop open.

    This used to be counted two ways at once, and the two ways
    disagreed. The thread reading said "closed" (the last attempt
    closed the problem) while the verdict said "not closed" (an
    earlier attempt had failed) - one report, two answers to the same
    question. A resumed problem could then never be reported as
    closed, which made the core workflow of this project - fail,
    resume, succeed - impossible to finish.

    The history is not lost: ``counts["not_closed"]`` still holds
    every failed attempt, and ``counts["not_closed_superseded"]``
    holds the ones a later attempt replaced.

    Every one of those is reported separately, with its own count, so a
    reader who disagrees with the verdict can see which part they
    disagree with. The reasons are the point - a bare "not closed"
    sends the reader hunting; "3 drafts are pending a human" tells them
    exactly what to do.

    The verdict never hides the work it did not count. An empty ledger
    is not "closed" - it is "nothing has run yet", and that is said
    plainly, because an empty ledger is the one state where claiming
    closure would be a lie.
    """
    totals = report.get("totals") or {}
    threads = report.get("threads") or []
    drafts = report.get("drafts") or {}

    reasons: List[Dict[str, Any]] = []

    def reason(key: str, count: int, detail: str, level: str = "fail") -> None:
        if count:
            reasons.append({"reason": key, "count": int(count), "detail": detail, "level": level})

    runs = int(totals.get("runs") or 0)
    not_closed = int(totals.get("not_closed") or 0)
    # The failed attempts that are still waiting. A report built by
    # an older version of this file has no such number, so the
    # fallback is the count it does have - and a report without
    # threads at all falls back to "every failure is still waiting",
    # which is the safe direction to be wrong in.
    not_closed_open = int(
        totals.get("not_closed_open")
        if totals.get("not_closed_open") is not None
        else not_closed
    )
    superseded = int(totals.get("not_closed_superseded") or 0)
    open_problems = int(totals.get("problems_open") or 0)
    waiting = int(totals.get("tickets_waiting") or 0)
    pending_drafts = int(drafts.get("pending_count") or 0)
    drifted = int(totals.get("seal_drifted") or 0)
    audit_problems = int(totals.get("audit_problems") or 0)
    unmatched_undos = int(totals.get("undos_unmatched") or 0)

    reason(
        "runs_not_closed",
        not_closed_open,
        "还有 %d 次尝试没有闭合，这些尝试所在的问题还开着" % not_closed_open,
    )
    reason("problems_open", open_problems, "有 %d 条问题线索还开着" % open_problems)
    reason("tickets_waiting", waiting, "有 %d 张工单在等人" % waiting)
    reason("drafts_pending", pending_drafts, "有 %d 份草稿等人批准" % pending_drafts)
    reason("seal_drifted", drifted, "有 %d 份封存结论已经和磁盘对不上" % drifted)
    reason("audit_problems", audit_problems, "有 %d 条决策链验不过" % audit_problems)
    reason(
        "undos_unmatched",
        unmatched_undos,
        "有 %d 次撤销配不上对应的批准" % unmatched_undos,
        "warn",
    )

    # An empty ledger is not a closed loop. It is a loop that never ran,
    # and the two must never be spelled the same way.
    empty = runs == 0
    ok = not empty and not any(r["level"] == "fail" for r in reasons)

    if empty:
        headline = "还没有任何运行记录，无法判定闭合。"
    elif ok:
        headline = "闭合：没有留下任何待办。"
    else:
        headline = "未闭合：%s。" % "；".join(r["detail"] for r in reasons if r["level"] == "fail")

    return {
        "ok": ok,
        "empty": empty,
        "headline": headline,
        "reasons": reasons,
        "counts": {
            "runs": runs,
            "closed": int(totals.get("closed") or 0),
            "not_closed": not_closed,
            # The failed attempts that are still waiting - the number
            # the verdict is built on - and the ones a later attempt
            # replaced.
            "not_closed_open": not_closed_open,
            "not_closed_superseded": superseded,
            "problems_open": open_problems,
            "problems": int(totals.get("problems") or 0),
            "problems_closed": int(totals.get("problems_closed") or 0),
            "tickets_waiting": waiting,
            "drafts_pending": pending_drafts,
            "seal_drifted": drifted,
            "audit_problems": audit_problems,
            "undos_unmatched": unmatched_undos,
        },
        # The attempt-grain rate and the problem-grain rate. The
        # verdict is built on the second one, so it is the one that
        # has to be next to the headline.
        "closure_rate": float(totals.get("closure_rate") or 0.0),
        "problem_closure_rate": float(totals.get("problem_closure_rate") or 0.0),
        "open_threads": [
            {
                "thread_id": str(thread.get("thread_id") or thread.get("id") or ""),
                "title": str(thread.get("title") or thread.get("goal") or ""),
                "closed": bool(thread.get("closed")),
            }
            for thread in threads
            if not thread.get("closed")
        ],
    }


def format_closure(verdict: Dict[str, Any]) -> str:
    """Say whether the work is closed, in words a person can act on."""
    lines: List[str] = []
    counts = verdict.get("counts") or {}
    lines.append("闭环判定")
    lines.append("-" * 40)
    if verdict.get("empty"):
        lines.append("[注意] 账本里还没有任何运行记录，无法判定闭合。")
        lines.append("       跑一次 `mas run` 之后再来问。")
        lines.append("-" * 40)
        return "\n".join(lines)

    ok = bool(verdict.get("ok"))
    lines.append(
        ("[通过] %s" if ok else "[未闭] %s") % verdict.get("headline")
    )
    # Two grains, two rates, both named. The attempt rate is not
    # wrong - it is the rate of the tries - but putting it next to a
    # verdict without saying which grain it measures is how a reader
    # concludes the tool is contradicting itself.
    problems = int(counts.get("problems") or 0)
    problems_closed = int(counts.get("problems_closed") or 0)
    lines.append(
        "       问题 %d 个（已解决 %d / 还开着 %d），问题闭环率 %.0f%%"
        % (
            problems,
            problems_closed,
            int(counts.get("problems_open") or 0),
            float(verdict.get("problem_closure_rate") or 0.0) * 100,
        )
    )
    lines.append(
        "       尝试 %d 次（闭合 %d / 未闭合 %d），尝试闭环率 %.0f%%"
        % (
            int(counts.get("runs") or 0),
            int(counts.get("closed") or 0),
            int(counts.get("not_closed") or 0),
            float(verdict.get("closure_rate") or 0.0) * 100,
        )
    )
    # The superseded attempts are history, not work in progress. A
    # reader who sees "2 runs did not close" under a closed verdict
    # has to be told that a later attempt replaced them, or the two
    # numbers look like a contradiction.
    superseded = int(counts.get("not_closed_superseded") or 0)
    if superseded:
        lines.append(
            "       （其中 %d 次失败已被后来的成功尝试取代，不算待办）" % superseded
        )
    for reason in verdict.get("reasons") or []:
        lines.append("       - %s" % reason.get("detail"))
    for thread in verdict.get("open_threads") or []:
        lines.append("       * %s  %s" % (thread.get("thread_id") or "", thread.get("title") or "-"))
    lines.append("-" * 40)
    lines.append("结论：%s" % ("已闭合，没有留下待办。" if ok else "还有事情没做完。"))
    return "\n".join(lines)


def closure_verdict_for(
    rows: List[Dict[str, Any]],
    tickets: Optional[List[Dict[str, Any]]] = None,
    drafts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The closure verdict for a set of ledger rows, in one place.

    The gap this closes
    ------------------
    The verdict is only useful if every surface that can be asked
    gives the same answer. Assembling the report and judging it is
    two steps, and the second step had no shared home - so the CLI
    and the MCP server each grew their own way of asking, and the
    two were free to drift apart.

    They do not drift any more. This is the one function that turns
    ledger rows into a verdict; both surfaces call it.

    It reads. It never writes, and it never starts a loop: the caller
    brings the records, and the verdict is computed from them.
    """
    report = build_report(rows, tickets, drafts=drafts)
    verdict = closure_verdict(report)
    verdict["report"] = {
        "totals": report.get("totals") or {},
        "seal": report.get("seal") or {},
    }
    return verdict

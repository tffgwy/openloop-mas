"""Reporting and replay: turn a run ledger into a human readable report.

The ledger is the raw truth. A report is a projection of it: the same run
rendered for a human reviewer, a Markdown file or a replay trace. Nothing
here invents data - every number comes from the ledger.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List

from mas.util import now_iso

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
    for row in rows:
        role = str(row.get("role", "unknown"))
        roles[role] = roles.get(role, 0) + 1
        if role == "closure":
            closure = row
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

    closure = closure or {}
    scores = closure.get("scores") or []
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

    return {
        "run_id": run_id,
        "status": status,
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


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the full report payload for a set of ledger rows."""
    runs = [run_summary(run["run_id"], run["rows"]) for run in group_runs(rows)]
    closed = [r for r in runs if r["closed"]]
    failed_runs = [r for r in runs if not r["closed"]]
    return {
        "generated_at": now_iso(),
        "runs": runs,
        "totals": {
            "runs": len(runs),
            "closed": len(closed),
            "not_closed": len(failed_runs),
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
    lines.append(f"总花费：{int(totals.get('tokens', 0) or 0)} token")
    lines.append("")

    runs = report.get("runs") or []
    if not runs:
        lines.append("账本为空，暂无运行记录。")
        return "\n".join(lines) + "\n"

    lines.append("## 运行概览")
    lines.append("")
    lines.append("| 运行 ID | 状态 | 决策 | 迭代 | 停滞 | 产物 SHA | 条目 | token |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for run in runs:
        status = str(run.get("status") or "-")
        icon = STATUS_ICON.get(status, "-")
        lines.append(
            f"| `{run.get('run_id')}` | {icon} {status} | {run.get('decision') or '-'} | "
            f"{run.get('iterations', 0)} | {int(run.get('stagnation') or 0)} | "
            f"`{(run.get('artifact_sha') or '')[:12]}` | "
            f"{run.get('entries', 0)} | {int(run.get('tokens') or 0)} |"
        )
    lines.append("")

    for run in runs:
        lines.append(f"## 运行 `{run.get('run_id')}`")
        lines.append("")
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
            lines.append(f"- 未闭合原因：{run.get('error')}")
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
                lines.append(
                    f"  - [{flag}] `{score.get('id')}` {score.get('note', '')}{mark}"
                )
        if run.get("issues"):
            lines.append("- 未通过原因：")
            for issue in run["issues"]:
                lines.append(f"  - {issue}")
        unjudged = run.get("unjudged") or []
        if unjudged:
            lines.append("- 无人判定的标准（不等于失败，但也不能算通过）：")
            for score in unjudged:
                lines.append(
                    f"  - `{score.get('id')}` {score.get('note', '')}"
                    f"（判定来源：{score.get('source') or '-'}）"
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
    """
    steps: List[Dict[str, Any]] = []
    for row in rows:
        steps.append(
            {
                "seq": row.get("seq"),
                "ts": row.get("ts"),
                "iteration": row.get("iteration"),
                "role": row.get("role"),
                "decision": row.get("decision"),
                "artifact_sha": row.get("artifact_sha"),
                "verdict": row.get("verdict"),
                "closed": row.get("closed"),
                "detail": _short_detail(row),
            }
        )
    return {
        "steps": steps,
        "steps_count": len(steps),
        "artifact_chain": [s["artifact_sha"] for s in steps if s.get("artifact_sha")],
        "terminal": steps[-1] if steps else None,
    }


def _short_detail(row: Dict[str, Any]) -> str:
    role = str(row.get("role", ""))
    if role == "plan":
        return ""
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


def ledger_markdown(rows: List[Dict[str, Any]]) -> str:
    """Render raw ledger rows as a Markdown table."""
    lines = [
        "| # | 时间 | 运行 | 迭代 | 角色 | 摘要 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('seq', '')} | {row.get('ts', '')} | `{row.get('run_id', '')}` | "
            f"{row.get('iteration', '')} | {row.get('role', '')} | "
            f"{_short_detail(row).replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"

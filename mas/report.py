"""Reporting and replay: turn a run ledger into a human readable report.

The ledger is the raw truth. A report is a projection of it: the same run
rendered for a human reviewer, a Markdown file or a replay trace. Nothing
here invents data - every number comes from the ledger.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional

from mas.util import now_iso, short_sha

STATUS_ICON = {
    "closed": "OK",
    "escalated": "ESC",
    "budget_exhausted": "BUD",
    "error": "ERR",
    "not_closed": "OPEN",
    "incomplete": "OPEN",
}


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
        elif role == "error":
            errors.append(str(row.get("error", "")))

    closure = closure or {}
    scores = closure.get("scores") or []
    failed = [s for s in scores if not s.get("pass")]
    artifacts = [str(r.get("artifact_sha") or "") for r in rows if r.get("artifact_sha")]

    # The closure entry records the verdict of the *last* iteration and the
    # loop status. The status is what the user cares about, the verdict
    # stays available separately.
    last_verdict = str(closure.get("verdict") or "") if closure else ""
    decision = str(closure.get("decision") or "")
    if not closure:
        status = "incomplete"
    elif closure.get("closed"):
        status = "closed"
    elif decision == "escalate":
        status = "escalated"
    else:
        status = "not_closed"

    return {
        "run_id": run_id,
        "status": status,
        "verdict": last_verdict,
        "decision": decision,
        "closed": bool(closure.get("closed")),
        "iterations": closure.get("iteration") or rows[-1].get("iteration") if rows else 0,
        "artifact_sha": str(closure.get("artifact_sha") or ""),
        "scores": scores,
        "failed": failed,
        "issues": list(closure.get("issues") or []),
        "roles": roles,
        "plan": (planner or {}).get("plan") or {},
        "candidates": (candidates or {}).get("candidates") or [],
        "review": {
            "mode": (review or {}).get("mode"),
            "personas": (review or {}).get("personas") or [],
            "usable": (review or {}).get("usable"),
            "skipped": (review or {}).get("skipped"),
        },
        "errors": errors,
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
    lines.append("")

    runs = report.get("runs") or []
    if not runs:
        lines.append("账本为空，暂无运行记录。")
        return "\n".join(lines) + "\n"

    lines.append("## 运行概览")
    lines.append("")
    lines.append("| 运行 ID | 状态 | 决策 | 迭代 | 产物 SHA | 条目 |")
    lines.append("|---|---|---|---|---|---|")
    for run in runs:
        status = str(run.get("status") or "-")
        icon = STATUS_ICON.get(status, "-")
        lines.append(
            f"| `{run.get('run_id')}` | {icon} {status} | {run.get('decision') or '-'} | "
            f"{run.get('iterations', 0)} | `{(run.get('artifact_sha') or '')[:12]}` | {run.get('entries', 0)} |"
        )
    lines.append("")

    for run in runs:
        lines.append(f"## 运行 `{run.get('run_id')}`")
        lines.append("")
        lines.append(f"- 开始：{run.get('started_at') or '-'}")
        lines.append(f"- 结束：{run.get('finished_at') or '-'}")
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
            personas = review.get("personas") or []
            if personas:
                lines.append(f"  - 评审视角：{', '.join(personas)}")
        scores = run.get("scores") or []
        if scores:
            lines.append("- 验收判定：")
            for score in scores:
                flag = "PASS" if score.get("pass") else "FAIL"
                lines.append(f"  - [{flag}] `{score.get('id')}` {score.get('note', '')}")
        if run.get("issues"):
            lines.append("- 未通过原因：")
            for issue in run["issues"]:
                lines.append(f"  - {issue}")
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
        issues = row.get("issues") or []
        return "; ".join(str(i) for i in issues)[:300]
    if role == "candidates":
        candidates = row.get("candidates") or []
        return "; ".join(
            f"{c.get('angle')}:{'选中' if c.get('selected') else '淘汰'}" for c in candidates
        )[:300]
    if role == "review":
        return f"模式={row.get('mode')} 可用={row.get('usable')} 跳过={row.get('skipped')}"
    if role == "error":
        return str(row.get("error", ""))[:300]
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

"""Benchmark the loop runtime.

The benchmark measures the properties a loop engineering runtime actually
promises, not synthetic throughput:

* ``closure_rate``     - how often a run reaches a truthful closed state;
* ``iterations``       - how many plan/work/verify cycles it took;
* ``tokens_per_closure``- cost of one closed loop;
* ``latency``          - wall clock per run;
* ``ledger_integrity`` - the ledger tells the same story as the result.

Everything runs offline against the mock backend so the numbers are
reproducible on any machine, and so a beginner can see the shape of the
system before pointing it at a paid model.
"""

from __future__ import annotations

import json
import tempfile
import time
from typing import Any, Dict, List, Optional

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.teams import load_team

# Each scenario is (name, goal, criteria, expected_status).
SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "closure",
        "goal": "写一段包含关键结论的短文，用于基准测试。",
        "criteria": [
            {"id": "c1", "desc": "包含核心结论", "check": {"type": "contains", "arg": "关键结论"}},
            {"id": "c2", "desc": "长度达标", "check": {"type": "min_chars", "arg": 220}},
        ],
        "expect": "closed",
    },
    {
        "name": "structured",
        "goal": "写一份带小节标题的方案说明。",
        "criteria": [
            {"id": "c1", "desc": "有结构", "check": {"type": "section", "arg": "补充说明"}},
            {"id": "c2", "desc": "长度达标", "check": {"type": "min_chars", "arg": 200}},
        ],
        "expect": "closed",
    },
    {
        "name": "json",
        "goal": "输出一份结构化 JSON 结果。",
        "criteria": [
            {"id": "c1", "desc": "是合法 JSON", "check": {"type": "json_keys", "arg": ["verdict"]}},
        ],
        "expect": "closed",
    },
]


def _config(state_dir: str, **overrides: Any) -> Dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = state_dir
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


def run_bench(
    repeats: int = 3,
    scenarios: Optional[List[str]] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the benchmark and return a structured result."""
    repeats = max(1, int(repeats))
    selected = [s for s in SCENARIOS if scenarios is None or s["name"] in scenarios]
    if not selected:
        raise ValueError("没有匹配的场景")

    rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mas-bench-") as tmp:
        for index in range(repeats):
            for scenario in selected:
                run_dir = f"{tmp}/run-{index}-{scenario['name']}"
                config = _config(run_dir, **(config_overrides or {}))
                engine = LoopEngine(config, team=load_team("default"))
                started = time.perf_counter()
                result = engine.run(
                    scenario["goal"], scenario["criteria"], title=f"bench {scenario['name']}"
                )
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                entries = engine.ledger.entries()
                closures = [row for row in entries if row.get("role") == "closure"]
                ledger_ok = bool(closures) and bool(closures[-1].get("closed")) == result.closed
                rows.append(
                    {
                        "scenario": scenario["name"],
                        "repeat": index + 1,
                        "expect": scenario["expect"],
                        "status": result.status,
                        "closed": result.closed,
                        "ok": result.status == scenario["expect"],
                        "iterations": result.iterations,
                        "tokens": result.tokens_used,
                        "latency_ms": elapsed,
                        "ledger_entries": len(entries),
                        "ledger_integrity": ledger_ok,
                    }
                )

    closed_rows = [r for r in rows if r["closed"]]
    ok_rows = [r for r in rows if r["ok"]]
    latencies = [r["latency_ms"] for r in rows]
    iterations = [r["iterations"] for r in rows]
    tokens = [r["tokens"] for r in rows]
    integrity = [r["ledger_integrity"] for r in rows]

    return {
        "repeats": repeats,
        "scenarios": [s["name"] for s in selected],
        "rows": rows,
        "summary": {
            "runs": len(rows),
            "expected_matches": len(ok_rows),
            "closed": len(closed_rows),
            "closure_rate": round(len(closed_rows) / len(rows), 4) if rows else 0.0,
            "expectation_rate": round(len(ok_rows) / len(rows), 4) if rows else 0.0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "avg_iterations": round(sum(iterations) / len(iterations), 2) if iterations else 0.0,
            "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
            "tokens_per_closure": (
                round(sum(tokens) / len(closed_rows), 1) if closed_rows else 0.0
            ),
            "ledger_integrity_rate": round(sum(1 for x in integrity if x) / len(integrity), 4)
            if integrity
            else 0.0,
        },
    }


def format_bench(result: Dict[str, Any]) -> str:
    summary = result["summary"]
    lines = ["OpenLoop MAS 基准测试（离线 mock 后端）"]
    lines.append("-" * 62)
    lines.append(
        f"{'场景':<12}{'期望':<20}{'实际':<20}{'迭代':<6}{'耗时ms':<9}{'token':<8}"
    )
    for row in result["rows"]:
        lines.append(
            f"{row['scenario']:<12}{row['expect']:<20}{row['status']:<20}"
            f"{row['iterations']:<6}{row['latency_ms']:<9}{row['tokens']:<8}"
        )
    lines.append("-" * 62)
    lines.append(f"运行次数        : {summary['runs']}")
    lines.append(f"符合预期        : {summary['expected_matches']}/{summary['runs']}")
    lines.append(f"闭环率          : {summary['closure_rate']:.0%}")
    lines.append(f"平均迭代        : {summary['avg_iterations']}")
    lines.append(f"平均耗时        : {summary['avg_latency_ms']} ms")
    lines.append(f"平均 token      : {summary['avg_tokens']}")
    lines.append(f"每次闭环 token  : {summary['tokens_per_closure']}")
    lines.append(f"账本一致性      : {summary['ledger_integrity_rate']:.0%}")
    return "\n".join(lines)

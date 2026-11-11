"""Offline demonstration scenarios.

The demo proves three properties of the engine without any network access:

* ``closure``      - a task whose criteria fail on the first draft closes on a
                     later attempt, and the ledger contains one entry per cycle.
* ``stagnation``   - a loop that keeps producing the same artifact escalates
                     instead of burning the whole budget.
* ``budget``       - a loop with a one-iteration budget stops with a truthful
                     status instead of pretending to be done.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any, Dict, List

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.providers import Provider
from mas.teams import load_team


class StaticProvider(Provider):
    """Always returns the same artifact - exercises the stagnation guard."""

    name = "static"

    def __init__(self, artifact: str, **options: Any) -> None:
        super().__init__(model="static-1", **options)
        self.artifact = artifact

    def complete(self, *, system: str, prompt: str, role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
        if role == "worker":
            text = self.artifact
        elif role == "verifier":
            text = json.dumps({"verdict": "fail", "scores": [], "issues": ["stuck"]})
        elif role == "reflector":
            text = json.dumps({"summary": "same output", "issues": ["stuck"], "next_action": "revise"})
        elif role == "planner":
            text = json.dumps({"summary": "plan", "plan": ["one step"], "risks": []})
        else:
            text = json.dumps({"decision": "revise", "reason": "not verified"})
        self.calls += 1
        return {
            "text": text,
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            "raw": {},
        }


def _config(state_dir: str, **overrides: Any) -> Dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = state_dir
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


def run_scenarios(only: str = "all") -> List[Dict[str, Any]]:
    scenarios = {
        "closure": _closure,
        "stagnation": _stagnation,
        "budget": _budget,
    }
    selected = list(scenarios) if only == "all" else [only]
    results: List[Dict[str, Any]] = []
    for name in selected:
        if name not in scenarios:
            raise KeyError(f"unknown scenario: {name}")
        with tempfile.TemporaryDirectory(prefix="mas-demo-") as tmp:
            results.append(scenarios[name](tmp))
    return results


def _closure(tmp: str) -> Dict[str, Any]:
    config = _config(tmp)
    engine = LoopEngine(config, team=load_team("default"))
    criteria = [
        {"id": "c1", "desc": "包含核心结论", "check": {"type": "contains", "arg": "关键结论"}},
        {"id": "c2", "desc": "正文长度达标", "check": {"type": "min_chars", "arg": 220}},
    ]
    result = engine.run("写一段带核心结论的短文", criteria, title="closure demo")
    return {
        "scenario": "closure",
        "expect": "closed",
        "status": result.status,
        "closed": result.closed,
        "iterations": result.iterations,
        "scores": result.scores,
        "ledger_entries": len(engine.ledger.entries()),
        "ok": result.closed and result.iterations >= 2,
    }


def _stagnation(tmp: str) -> Dict[str, Any]:
    config = _config(tmp, stagnation_limit=3)
    provider = StaticProvider("# 同一份产物\n不变的输出。")
    engine = LoopEngine(config, provider=provider, team=load_team("default"))
    criteria = [
        {"id": "c1", "desc": "长度必须达标", "check": {"type": "min_chars", "arg": 5000}},
    ]
    result = engine.run("一个静态产物的任务", criteria, title="stagnation demo")
    return {
        "scenario": "stagnation",
        "expect": "escalated",
        "status": result.status,
        "closed": result.closed,
        "iterations": result.iterations,
        "ticket_id": result.ticket_id,
        "ok": result.status == "escalated" and bool(result.ticket_id),
    }


def _budget(tmp: str) -> Dict[str, Any]:
    config = _config(tmp, budget={"max_iterations": 1})
    engine = LoopEngine(config, team=load_team("default"))
    criteria = [
        {"id": "c1", "desc": "不可能满足的长度", "check": {"type": "min_chars", "arg": 999999}},
    ]
    result = engine.run("预算耗尽演示", criteria, title="budget demo")
    return {
        "scenario": "budget",
        "expect": "budget_exhausted",
        "status": result.status,
        "closed": result.closed,
        "iterations": result.iterations,
        "ok": result.status == "budget_exhausted",
    }

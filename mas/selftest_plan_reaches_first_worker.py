# -*- coding: utf-8 -*-
"""The fifty-eighth gate: the plan reaches the *first* worker.

The gap
------
The loop has a planner. The planner is called once, at the start of the
first iteration, and it costs a real call. The planner's answer was
paid for, booked in the ledger, and then handed to the worker of the
**second** attempt.

Measured, with a probe provider that recorded every prompt:

    call order: ['planner', 'worker', 'verifier', 'reflector', 'worker', ...]
    worker#1  sees PLAN_SUMMARY=False  sees PLAN_STEP=False
       state.plan.summary=None
    worker#4  sees PLAN_SUMMARY=True   sees PLAN_STEP=True

The **first** attempt - the one that actually produces the deliverable
from nothing - was written **blind**. It had no plan, no plan summary,
no plan steps. The plan was computed and then handed to a worker that
already had a deliverable in front of it.

The cause was an ordering mistake, not a missing feature. The state of
an iteration is built *before* the iteration runs, and the planner runs
*inside* the iteration. So the state was built with the plan of a
planner that had not run yet: an empty dict. The planner's answer went
into ``plan_payload`` and never went back into ``state``.

The worst case was a run allowed a single iteration: the planner was
called, the money was spent, and the answer reached **no worker at
all**.

The fix
------
The plan is written back into the state right after the planner
answers. Every prompt built from that state afterwards - the worker,
the verifier, the reflector, the lead - reads the plan from the state,
so one write covers all of them.

This gate watches:

* the first attempt sees the plan summary;
* the first attempt sees the plan steps;
* the plan is in the state's ``plan`` field, not only in the prose;
* a run allowed a **single** iteration still reaches its worker with
  the plan - the case where the plan used to reach nobody;
* the second attempt still sees the plan (the fix did not trade one
  attempt for another);
* and the gate proves it can fail: the engine stops writing the plan
  back into the state, and the gate has to notice.

The break is on the **wiring** again, and for the same reason as the
last gate: a break on the reader would make every prompt wrong in the
same way, so the two readings would still agree - and a gate whose two
readings agree on a broken world is not watching anything.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: The observations the gate makes.
PLAN_REACHES_FIRST_WORKER_OBSERVATIONS = (
    "the first attempt sees the plan summary",
    "the first attempt sees the plan steps",
    "the plan is in the state, not only in the prose",
    "a single-iteration run still reaches its worker with the plan",
    "the second attempt still sees the plan",
)

#: The plan the planner produces. The markers are unique on purpose:
#: a repeated string would also appear in the tail of a clipped prompt
#: and the gate would read a broken world as a working one.
PLAN_SUMMARY = "计划摘要标记-PLAN-SUMMARY-UNIQUE"
PLAN_STEP = "计划步骤标记-PLAN-STEP-UNIQUE"


class _Env:
    """Borrow the environment variables the gate has to borrow.

    The restore is absolute: a variable that was not there before is
    removed again.
    """

    def __init__(self, state_dir: str, config_path: str) -> None:
        self._values = {
            "MAS_STATE_DIR": state_dir,
            "MAS_CONFIG_PATH": config_path,
        }
        self._saved: Dict[str, Optional[str]] = {}

    def __enter__(self) -> "_Env":
        for name, value in self._values.items():
            self._saved[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *exc: Any) -> None:
        for name, saved in self._saved.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


class _Probe:
    """A provider that records every prompt it is given."""

    name = "probe"
    model = "probe-model"

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str]] = []

    def complete(
        self,
        *,
        system: str = "",
        prompt: str = "",
        role: str = "worker",
        expect_json: bool = False,
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        self.calls.append((role, prompt))
        usage = {"total_tokens": 10}
        if role == "planner":
            return {
                "text": json.dumps(
                    {
                        "summary": PLAN_SUMMARY,
                        "plan": [PLAN_STEP],
                        "risks": [],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "reflector":
            return {
                "text": json.dumps(
                    {"summary": "复盘", "issues": ["改"], "next_action": "revise"}
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "verifier":
            return {
                "text": json.dumps(
                    {
                        "verdict": "fail",
                        "scores": [
                            {"id": "c1", "pass": False, "note": "missing"}
                        ],
                        "issues": ["c1"],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        worker_calls = len([r for r, _ in self.calls if r == "worker"])
        text = "第一版交付物" if worker_calls <= 1 else "第二版交付物"
        return {"text": text, "usage": usage, "raw": {}}


def _config(root: pathlib.Path, max_iterations: int) -> Dict[str, Any]:
    """A configuration that keeps the run inside the gate's own folder."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    return {
        "provider": "mock",
        "model": "probe-model",
        "state_dir": str(state),
        "workspace": str(state / "ws"),
        "budget": {
            "max_iterations": max_iterations,
            "max_tokens": 1000000,
            "deadline_sec": 60,
        },
        "orchestration": {
            "parallel_workers": 1,
            "reviewers": 1,
            "review_mode": "all",
        },
        "team": {"language": "zh"},
    }


def _criteria() -> List[Dict[str, Any]]:
    """One criterion the deliverable cannot satisfy, so the loop fails."""
    return [
        {
            "id": "c1",
            "desc": "必须包含关键词",
            "check": {"type": "contains", "arg": "关键词"},
        }
    ]


def _prompts_of(calls: List[Tuple[str, str]], role: str) -> List[str]:
    return [prompt for name, prompt in calls if name == role]


def _state_of(prompt: str) -> Dict[str, Any]:
    """The raw state a prompt carried."""
    marker = "<<MAS_STATE>>"
    if not prompt or marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1]
    end = tail.find("<<END>>")
    if end == -1:
        return {}
    try:
        data = json.loads(tail[:end].strip())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _observe(root: pathlib.Path, max_iterations: int = 3) -> Dict[str, Any]:
    """Run one real loop and record what each role was told."""
    from mas.engine import LoopEngine

    seen: Dict[str, Any] = {}
    config = _config(root, max_iterations)
    config_path = root / "config.json"
    probe = _Probe()
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        engine.run(goal="做一个东西", criteria=_criteria())
    seen["calls"] = list(probe.calls)
    seen["workers"] = _prompts_of(probe.calls, "worker")
    return seen


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered prompts."""
    verdicts: Dict[str, bool] = {}
    workers = list(facts.get("workers") or [])

    first = workers[0] if workers else ""
    verdicts["the first attempt sees the plan summary"] = (
        bool(first) and PLAN_SUMMARY in first
    )
    verdicts["the first attempt sees the plan steps"] = (
        bool(first) and PLAN_STEP in first
    )

    # The plan has to be a *fact of the state*, not a line of prose: a
    # brief can be right while the state that travels with it is empty,
    # and then every other role - the verifier, the reflector - reads
    # the empty one.
    first_state = _state_of(first)
    plan = first_state.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    verdicts["the plan is in the state, not only in the prose"] = (
        str(plan.get("summary") or "") == PLAN_SUMMARY
        and PLAN_STEP in [str(s) for s in (plan.get("plan") or [])]
    )

    # The case where the plan used to reach nobody at all.
    single = list((facts.get("single") or {}).get("workers") or [])
    single_prompt = single[0] if single else ""
    verdicts["a single-iteration run still reaches its worker with the plan"] = (
        bool(single_prompt)
        and PLAN_SUMMARY in single_prompt
        and PLAN_STEP in single_prompt
    )

    second = workers[1] if len(workers) > 1 else ""
    verdicts["the second attempt still sees the plan"] = (
        bool(second) and PLAN_SUMMARY in second
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading, made from the raw state block of the prompts.

    The first reading looks for *text* in the rendered prompt. This one
    parses the ``<<MAS_STATE>>`` JSON block and asks what the state
    itself carried - because the brief and the state are two different
    channels, and a fact that reaches one of them is not necessarily in
    the other.
    """
    verdicts: Dict[str, bool] = {}
    calls = list(facts.get("calls") or [])
    workers = _prompts_of(calls, "worker")
    first = workers[0] if workers else ""
    state = _state_of(first)
    plan = state.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}

    verdicts["the first attempt sees the plan summary"] = (
        str(plan.get("summary") or "") == PLAN_SUMMARY
    )
    verdicts["the first attempt sees the plan steps"] = (
        PLAN_STEP in [str(s) for s in (plan.get("plan") or [])]
    )
    verdicts["the plan is in the state, not only in the prose"] = bool(plan)

    single_workers = list((facts.get("single") or {}).get("workers") or [])
    single_state = _state_of(single_workers[0] if single_workers else "")
    single_plan = single_state.get("plan") or {}
    if not isinstance(single_plan, dict):
        single_plan = {}
    verdicts["a single-iteration run still reaches its worker with the plan"] = (
        str(single_plan.get("summary") or "") == PLAN_SUMMARY
        and PLAN_STEP in [str(s) for s in (single_plan.get("plan") or [])]
    )

    second_state = _state_of(workers[1] if len(workers) > 1 else "")
    second_plan = second_state.get("plan") or {}
    if not isinstance(second_plan, dict):
        second_plan = {}
    verdicts["the second attempt still sees the plan"] = (
        str(second_plan.get("summary") or "") == PLAN_SUMMARY
    )
    return verdicts


def _facts_of(root: pathlib.Path) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    facts: Dict[str, Any] = {}
    facts.update(_observe(root / "run"))
    facts["single"] = _observe(root / "single", max_iterations=1)
    return facts


def check_plan_reaches_the_first_worker(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-eighth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-plan-"))
        try:
            try:
                facts = _facts_of(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in PLAN_REACHES_FIRST_WORKER_OBSERVATIONS:
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

            # The gate proves it can fail.
            #
            # The break is on the **wiring**: the engine stops writing
            # the plan back into the state, so the first attempt is
            # blind again - exactly what the code did before this
            # release.
            import mas.engine as engine_module

            real_call_json = engine_module.LoopEngine._call_json

            def _no_plan_call(self, role, state, extra="", _real=real_call_json):
                """The planner still answers - the answer just never
                reaches the state.

                The state is left exactly as it was built, so the
                worker's first attempt is blind. The call is still
                made and still paid for: the point of the break is that
                the answer is dropped, not that the money is saved.
                """
                if role == "planner":
                    answer = dict(_real(self, role, state, extra))
                    answer["summary"] = ""
                    answer["plan"] = []
                    return answer
                return _real(self, role, state, extra)

            try:
                engine_module.LoopEngine._call_json = _no_plan_call
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            pathlib.Path(
                                tempfile.mkdtemp(prefix="mas-selftest-plan-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                engine_module.LoopEngine._call_json = real_call_json

            if broken.get("the first attempt sees the plan summary"):
                failures.append(
                    "round %d: the gate did not notice that the plan no "
                    "longer reaches the first attempt - it cannot fail, so "
                    "it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "plan_reaches_the_first_worker",
        "title": "计划真的到达了第一轮工人手里",
        "skipped": False,
        "invariant": (
            "第一轮尝试必须看得见计划：计划在第一次迭代的状态里就"
            "构造好了，而计划是在迭代内部才产生的——所以计划算完"
            "必须写回状态，否则最关键的第一次尝试是盲写；"
            "只跑一轮的运行时，计划更是一个工人也到不了"
        ),
        "detail": (
            "计划付了钱、记了账，却只交给了第二轮工人：第一轮"
            "从零开始写的那一次，从来没拿到过计划"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_plan_reaches_the_first_worker.CHECK_NAME = (  # type: ignore[attr-defined]
    "plan_reaches_the_first_worker"
)

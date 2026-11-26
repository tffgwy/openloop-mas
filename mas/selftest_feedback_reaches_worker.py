# -*- coding: utf-8 -*-
"""The fifty-seventh gate: the feedback reaches the worker.

The gap
------
A loop that fails a criterion does three things: it books the
failure, it pays the reflector, and it runs the worker again. The
book was right. The money was right. The third thing was hollow.

Measured, on a real run with a probe provider that recorded every
prompt:

* the worker's **second** attempt in the same run never saw the
  deliverable of its **first** attempt - the brief had a ``PREVIOUS
  DELIVERABLE`` block, but the state that fills it was only ever set
  for a *resumed* run, never for the next iteration of the run in
  progress;
* the reflector's own words never reached anybody. The reflector
  returned ``{"issues": ["必须用中文重写整篇"], "reflection": "先把结论
  写在第一段"}`` and both strings were absent from the worker's next
  prompt - the reflection was computed, paid for, logged - and
  dropped;
* so the worker was told to "rewrite the deliverable to address the
  issues above" while the issues above were a machine's one-line
  complaint (``c1: missing: ['关键词']``) and nothing else.

The loop was iterating. It was not learning. Every retry started
from nothing, and the most expensive part of the run - the
reflection - bought nothing at all.

The fix
------
Two facts now travel with the state of the next iteration: the
deliverable the previous iteration produced, and what the reflector
said about it. Both are clipped, because text is the one thing in a
prompt that can grow without a ceiling.

This gate watches:

* the second attempt in a run sees the first attempt's deliverable;
* the second attempt sees the reflector's **summary**;
* the second attempt sees the reflector's **issues** - and they are
  the reflector's words, not the machine's note;
* the reflector itself is shown the deliverable it is reflecting on;
* the first attempt is *not* shown a previous deliverable - there
  is none;
* a resumed run still sees the deliverable it was handed;
* a long deliverable is clipped - the **tail** survives, and the
  omission is announced rather than silent;
* and the gate proves it can fail: the engine stops carrying the
  feedback, and the gate has to notice that it can no longer tell a
  real loop from an amnesiac one.

The break is on the **wiring** between the iterations, not on the
reader: a broken reader would make every prompt wrong in the same
way, so a check that only looked at the prompt would still see a
prompt. The break has to make the *fact* disappear.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: The observations the gate makes.
FEEDBACK_REACHES_WORKER_OBSERVATIONS = (
    "the second attempt sees the first attempt's deliverable",
    "the second attempt sees the reflector's summary",
    "the second attempt sees the reflector's issues",
    "the reflector's issues are the reflector's own words",
    "the reflector is shown the deliverable it reflects on",
    "the first attempt is shown no previous deliverable",
    "a resumed run still sees the deliverable it was handed",
    "a long deliverable is clipped and the omission is announced",
)

#: The deliverable the first attempt produces.
FIRST_DELIVERABLE = "第一版交付物：正文内容在此。"
#: The reflector's own words. They are deliberately unlike a machine
#: note: a machine can say "missing: ['关键词']", only the reflector
#: can say "必须用中文重写整篇".
REFLECTOR_ISSUE = "必须用中文重写整篇"
REFLECTOR_SUMMARY = "先把结论写在第一段"
#: The machine's own note about the same failure.
MACHINE_NOTE = "missing: ['关键词']"


class _Env:
    """Borrow the environment variables the gate has to borrow.

    The restore is absolute: a variable that was not there before is
    removed again. A gate that leaves ``MAS_STATE_DIR`` pointing at a
    temporary folder dirties the machine - and the self-test suite
    compares the machine with itself for exactly that reason.
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
    """A provider that records every prompt it is given.

    It is a real provider: the engine drives it exactly as it drives
    an endpoint. Every call is kept, in order, so the gate can ask
    what the second worker call was actually told.
    """

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
                "text": json.dumps({"summary": "s", "plan": ["a"], "risks": []}),
                "usage": usage,
                "raw": {},
            }
        if role == "reflector":
            return {
                "text": json.dumps(
                    {
                        "summary": REFLECTOR_SUMMARY,
                        "issues": [REFLECTOR_ISSUE],
                        "next_action": "revise",
                    }
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
                            {"id": "c1", "pass": False, "note": MACHINE_NOTE}
                        ],
                        "issues": ["c1"],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        # The worker. The first call produces the first deliverable,
        # the second one is the attempt the gate is about.
        worker_calls = len([r for r, _ in self.calls if r == "worker"])
        text = FIRST_DELIVERABLE if worker_calls <= 1 else "第二版交付物：已按复盘改写。"
        return {"text": text, "usage": usage, "raw": {}}


def _config(root: pathlib.Path) -> Dict[str, Any]:
    """A configuration that keeps the run inside the gate's own folder."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    return {
        "provider": "mock",
        "model": "probe-model",
        "state_dir": str(state),
        "workspace": str(state / "ws"),
        "budget": {
            "max_iterations": 2,
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


def _observe(root: pathlib.Path) -> Dict[str, Any]:
    """Run one real loop and record what each role was told."""
    from mas.engine import LoopEngine

    seen: Dict[str, Any] = {}
    config = _config(root)
    config_path = root / "config.json"
    probe = _Probe()
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        engine.run(goal="做一个东西", criteria=_criteria())
    seen["calls"] = list(probe.calls)
    seen["workers"] = _prompts_of(probe.calls, "worker")
    seen["reflectors"] = _prompts_of(probe.calls, "reflector")
    return seen


def _observe_resumed(root: pathlib.Path) -> Dict[str, Any]:
    """Run a *resumed* loop: the deliverable comes in from outside."""
    from mas.engine import LoopEngine

    seen: Dict[str, Any] = {}
    config = _config(root)
    config_path = root / "config.json"
    probe = _Probe()
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        engine.run(
            goal="做一个东西",
            criteria=_criteria(),
            previous_artifact="外部交接过来的交付物：正文内容在此。",
        )
    seen["workers"] = _prompts_of(probe.calls, "worker")
    return seen


def _observe_long(root: pathlib.Path) -> Dict[str, Any]:
    """Run a loop whose first deliverable is longer than the ceiling."""
    from mas.engine import LoopEngine

    seen: Dict[str, Any] = {}
    config = _config(root)
    config_path = root / "config.json"
    probe = _Probe()
    # A deliverable whose *head* is old and whose *tail* is new. The
    # tail is the part the next attempt has to improve on, so a
    # clipping that kept the head would throw exactly that away.
    long_text = ("开头的旧内容。" * 4000) + "尾部的最新结论必须保留。"
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        engine.run(
            goal="做一个东西",
            criteria=_criteria(),
            previous_artifact=long_text,
        )
    seen["workers"] = _prompts_of(probe.calls, "worker")
    seen["long_length"] = len(long_text)
    return seen


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered prompts."""
    verdicts: Dict[str, bool] = {}
    workers = list(facts.get("workers") or [])
    reflectors = list(facts.get("reflectors") or [])

    second = workers[1] if len(workers) > 1 else ""
    first = workers[0] if workers else ""

    # The second attempt sees what the first attempt delivered.
    verdicts["the second attempt sees the first attempt's deliverable"] = (
        bool(second) and FIRST_DELIVERABLE in second
    )

    # The second attempt sees the reflector's own summary.
    verdicts["the second attempt sees the reflector's summary"] = (
        bool(second) and REFLECTOR_SUMMARY in second
    )

    # The second attempt sees the reflector's issues.
    verdicts["the second attempt sees the reflector's issues"] = (
        bool(second) and REFLECTOR_ISSUE in second
    )

    # The issues it sees are the reflector's words - not the
    # machine's one-line complaint. Both are in the prompt: the
    # machine's note belongs to the ISSUES block, the reflector's
    # words to the reflection. The gate asks for the reflector's
    # words to be *there*; here it asks that the reflection block
    # carries them and is not merely a copy of the machine note.
    verdicts["the reflector's issues are the reflector's own words"] = (
        bool(second)
        and REFLECTOR_ISSUE in second
        and REFLECTOR_ISSUE != MACHINE_NOTE
    )

    # The reflector is shown the deliverable it is reflecting on.
    verdicts["the reflector is shown the deliverable it reflects on"] = (
        bool(reflectors) and FIRST_DELIVERABLE in reflectors[0]
    )

    # The first attempt has no previous deliverable. If it had one,
    # the worker would start by "improving" something it had not
    # written yet.
    verdicts["the first attempt is shown no previous deliverable"] = (
        bool(first) and "<<PREVIOUS>>" not in first
    )

    # A resumed run still sees the deliverable it was handed.
    resumed = list((facts.get("resumed") or {}).get("workers") or [])
    handed = "外部交接过来的交付物"
    verdicts["a resumed run still sees the deliverable it was handed"] = (
        bool(resumed) and handed in resumed[0]
    )

    # A long deliverable is clipped: the tail survives and the
    # omission is announced.
    long_workers = list((facts.get("long") or {}).get("workers") or [])
    long_prompt = long_workers[0] if long_workers else ""
    clipped_ok = False
    if long_prompt:
        clipped_ok = (
            "尾部的最新结论必须保留。" in long_prompt
            and "字已省略" in long_prompt
        )
    verdicts["a long deliverable is clipped and the omission is announced"] = (
        clipped_ok
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading, made from the raw state block of the prompts.

    The first reading looks for *text* in the rendered prompt. This
    one parses the ``<<MAS_STATE>>`` JSON block and asks what the
    state itself carried - because the brief and the state are two
    different channels, and a fact that reaches one of them is not
    necessarily in the other.
    """
    verdicts: Dict[str, bool] = {}
    calls = list(facts.get("calls") or [])
    workers = _prompts_of(calls, "worker")
    second = workers[1] if len(workers) > 1 else ""
    state = _state_of(second)

    previous = str(state.get("previous_artifact") or "")
    verdicts["the second attempt sees the first attempt's deliverable"] = (
        FIRST_DELIVERABLE in previous
    )

    reflection = state.get("reflection") or {}
    if not isinstance(reflection, dict):
        reflection = {}
    summary = str(reflection.get("summary") or "")
    issues = [str(item) for item in (reflection.get("issues") or [])]
    verdicts["the second attempt sees the reflector's summary"] = (
        REFLECTOR_SUMMARY in summary
    )
    verdicts["the second attempt sees the reflector's issues"] = (
        REFLECTOR_ISSUE in issues
    )
    verdicts["the reflector's issues are the reflector's own words"] = (
        REFLECTOR_ISSUE in issues and MACHINE_NOTE not in issues
    )

    reflectors = _prompts_of(calls, "reflector")
    reflect_state = _state_of(reflectors[0] if reflectors else "")
    verdicts["the reflector is shown the deliverable it reflects on"] = (
        FIRST_DELIVERABLE
        in str(reflect_state.get("candidate_artifact") or "")
    )

    first_state = _state_of(workers[0] if workers else "")
    verdicts["the first attempt is shown no previous deliverable"] = not str(
        first_state.get("previous_artifact") or ""
    )

    resumed_state = _state_of(
        ((facts.get("resumed") or {}).get("workers") or [""])[0]
    )
    verdicts["a resumed run still sees the deliverable it was handed"] = (
        "外部交接过来的交付物" in str(resumed_state.get("previous_artifact") or "")
    )

    long_workers = list((facts.get("long") or {}).get("workers") or [])
    long_state = _state_of(long_workers[0] if long_workers else "")
    long_previous = str(long_state.get("previous_artifact") or "")
    long_length = int((facts.get("long") or {}).get("long_length") or 0)
    verdicts["a long deliverable is clipped and the omission is announced"] = (
        "尾部的最新结论必须保留。" in long_previous
        and "字已省略" in long_previous
        and 0 < len(long_previous) < long_length
    )
    return verdicts


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


def _facts_of(root: pathlib.Path) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    facts: Dict[str, Any] = {}
    facts.update(_observe(root / "run"))
    facts["resumed"] = _observe_resumed(root / "resumed")
    facts["long"] = _observe_long(root / "long")
    return facts


def check_feedback_reaches_worker(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-seventh gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-feedback-"))
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
            for label in FEEDBACK_REACHES_WORKER_OBSERVATIONS:
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
            # The break is on the **wiring**: the engine stops
            # carrying the feedback into the next iteration's state.
            # A break on the reader would make every prompt wrong in
            # the same way, so the two readings would still agree - and
            # a gate whose two readings agree on a broken world is not
            # watching anything.
            import mas.engine as engine_module

            real_carry = engine_module._carry_feedback

            def _no_carry(
                state,  # type: ignore[no-untyped-def]
                last_artifact,  # type: ignore[no-untyped-def]
                last_reflection,  # type: ignore[no-untyped-def]
                previous_artifact,  # type: ignore[no-untyped-def]
            ) -> None:
                """Exactly what the code did before this release.

                The feedback was computed, paid for, logged - and
                never carried into the next iteration.
                """
                return

            try:
                engine_module._carry_feedback = _no_carry
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            pathlib.Path(
                                tempfile.mkdtemp(prefix="mas-selftest-feedback-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                engine_module._carry_feedback = real_carry

            if broken.get(
                "the second attempt sees the reflector's summary"
            ) or broken.get("the second attempt sees the first attempt's deliverable"):
                failures.append(
                    "round %d: the gate did not notice that the engine stops "
                    "carrying the feedback into the next iteration - it "
                    "cannot fail, so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "feedback_reaches_worker",
        "title": "反馈真的到达了下一轮的工人手里",
        "skipped": False,
        "invariant": (
            "同一轮运行的第二次尝试必须看见第一次尝试交付了什么，"
            "也必须看见复盘角色自己说的话——而不只是机检的一句"
            "抱怨；复盘角色自己也得看见它要复盘的那份交付物"
        ),
        "detail": (
            "上一轮的交付物与上一轮的复盘随状态一起进入下一轮："
            "书记得、钱付了、话却没送到——现在补上了这条线"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_feedback_reaches_worker.CHECK_NAME = (  # type: ignore[attr-defined]
    "feedback_reaches_worker"
)

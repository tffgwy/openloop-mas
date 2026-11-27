# -*- coding: utf-8 -*-
"""The sixtieth gate: the advisor's judgement has to reach the next worker.

The gap
-------
The advisor - the lead - exists for exactly one situation: some
acceptance criterion has no verdict at all. The machine checks could
not speak about it and the reviewers did not either, so the engine has
no answer. The advisor looks at the artifact and says what is
missing.

Its answer used to stop at the ledger. Measured, with a probe
provider:

    === the advisor judged c1 as NOT met ===
      result.issues : ['c1: unjudged: the review gave no verdict']
      -> the issue still says 'unjudged'          : True
      -> the advisor's own note is nowhere in it   : True

    === worker #2 (the one after the advisor spoke) ===
      sees lead NOTE     : False
      sees lead REASON   : False
      sees 'unjudged'    : True

Two facts were broken, and they are the same fact seen from two
sides.

*The advisor's judgement never became "what to fix".* The issues
list was built **before** the advisor was consulted, and it was never
rebuilt afterwards. So a criterion the advisor had judged "not met",
with its own note attached, still showed up as "unjudged: the review
gave no verdict for this criterion" - a complaint about a *missing
answer*, thrown at a worker who cannot produce a verdict. The
advisor's judgement reached the scores (the ledger's verdict list)
and stopped there. It never reached the issues, and the issues are
the thing the next attempt is told to fix.

*The advisor's own words never travelled.* The next attempt was
carried the deliverable and the reflector's reflection - and nothing
else. The advisor's reason, the one sentence that says *what* is
missing, was dropped on the floor.

The order was the cause. The advisor was consulted *after* the
reflector, and the issues were built before both.

The fix
------
The advisor is consulted **before** the reflector, and the issues are
built from the scores as they are *after* the advisor spoke. The
advisor's own words - the decision, the reason and the criteria it
judged - travel to the next attempt and to the reflector, in a block
of their own.

What does **not** change: the advisor is still an advisor and not a
judge. A failed machine check stays failed, and the advisor's
"close" cannot close a run whose checks did not pass. The advisor
may only *stop* the loop. That invariant was written down before
this release and this gate keeps watching it.

This gate watches:

* the issues carry the advisor's own note;
* the issues no longer claim that nobody judged the criterion;
* the next attempt is shown the advisor's section;
* the next attempt sees the advisor's own reason;
* the next attempt sees the advisor's judgement of the criterion;
* the reflector is shown the advisor's words;
* the advisor's escalation still stops the run early (no regression);
* a criterion the advisor judged as met still closes the loop (no
  regression).

The break is on the **wiring**: the advisor's judgement stops being
applied and its words stop being carried - exactly what the code
did before this release. The gate has to notice.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: The observations the gate makes.
LEAD_REACHES_THE_NEXT_WORKER_OBSERVATIONS = (
    "the issues carry the advisor's own note",
    "the issues no longer claim that nobody judged the criterion",
    "the next attempt is shown the advisor's section",
    "the next attempt sees the advisor's own reason",
    "the next attempt sees the advisor's judgement of the criterion",
    "the reflector is shown the advisor's words",
    "the advisor's escalation still stops the run early",
    "a criterion the advisor judged as met still closes the loop",
)

#: The advisor's own reason. The marker is unique on purpose: a
#: repeated string would also show up in the tail of a clipped text,
#: and the gate would read a broken world as a working one.
LEAD_REASON = "advisor-reason-marker-LEAD-REASON-UNIQUE-119"

#: The advisor's own note about the criterion it judged. It is
#: different from the reason on purpose - the gate asks for the note
#: in the issues and for the reason in the advice block, and a single
#: marker would let one of the two observations pass by accident.
LEAD_NOTE = "advisor-note-marker-LEAD-NOTE-UNIQUE-119"

#: The machine's complaint about the same criterion - the one the
#: issues list used to carry instead of the advisor's note.
MACHINE_COMPLAINT = "unjudged"


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
    """A provider whose advisor answers whatever the gate asked for.

    The verifier stays silent about the criterion, so the criterion
    has no verdict - which is the only situation in which the advisor
    is consulted at all.
    """

    name = "probe"
    model = "probe-model"

    def __init__(self, decision: str = "revise", met: bool = False) -> None:
        self.decision = decision
        self.met = met
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
        if role == "lead":
            return {
                "text": json.dumps(
                    {
                        "decision": self.decision,
                        "reason": LEAD_REASON,
                        "judged": [
                            {"id": "c1", "met": self.met, "note": LEAD_NOTE}
                        ],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "verifier":
            return {
                "text": json.dumps(
                    {"verdict": "fail", "scores": [], "issues": []}
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "reflector":
            return {
                "text": json.dumps(
                    {
                        "summary": "reflected",
                        "issues": ["change this"],
                        "next_action": "revise",
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "planner":
            return {
                "text": json.dumps({"summary": "s", "plan": ["a"], "risks": []}),
                "usage": usage,
                "raw": {},
            }
        return {"text": "deliverable", "usage": usage, "raw": {}}


def _config(root: pathlib.Path, max_iterations: int = 3) -> Dict[str, Any]:
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
    """One criterion with no machine check.

    A criterion with a machine check is decided by the machine, and an
    advisor that disagrees with a fact is not consulted at all - so
    the advisor could not be observed at all with one.
    """
    return [{"id": "c1", "desc": "must contain the keyword"}]


def _prompts_of(calls: List[Tuple[str, str]], role: str) -> List[str]:
    return [prompt for name, prompt in calls if name == role]


def _brief_of(prompt: str) -> str:
    """The readable part of a prompt - everything before the raw state.

    The prompt has two channels: the human-readable brief and the
    ``<<MAS_STATE>>`` JSON block. The gate's first reading looks at
    the brief, the second at the state - so a fact that reaches only
    one of them is noticed.
    """
    marker = "<<MAS_STATE>>"
    if not prompt:
        return ""
    if marker not in prompt:
        return prompt
    return prompt.split(marker, 1)[0]


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


def _observe(
    root: pathlib.Path,
    decision: str = "revise",
    met: bool = False,
    max_iterations: int = 3,
) -> Dict[str, Any]:
    """Run one real loop and record what each role was told."""
    from mas.engine import LoopEngine

    config = _config(root, max_iterations)
    config_path = root / "config.json"
    probe = _Probe(decision=decision, met=met)
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        result = engine.run(goal="make a thing", criteria=_criteria())

    workers = _prompts_of(probe.calls, "worker")
    reflectors = _prompts_of(probe.calls, "reflector")
    closure_rows = [
        row for row in engine.ledger.entries()
        if str(row.get("role") or "") == "closure"
    ]
    return {
        "calls": list(probe.calls),
        "workers": workers,
        "reflectors": reflectors,
        "issues": [str(item) for item in (result.issues or [])],
        "closure_issues": [
            str(item)
            for row in closure_rows
            for item in (row.get("issues") or [])
        ],
        "status": str(result.status or ""),
        "closed": bool(result.closed),
        "worker_calls": len(workers),
        "lead_calls": len(_prompts_of(probe.calls, "lead")),
    }


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the brief the roles were shown.

    This reading looks at the *readable* half of the prompt: what a
    person - or a model - would actually read.
    """
    verdicts: Dict[str, bool] = {}
    workers = list(facts.get("workers") or [])
    reflectors = list(facts.get("reflectors") or [])
    second = workers[1] if len(workers) > 1 else ""
    second_brief = _brief_of(second)

    # The issues the run recorded are the run's own book: the criteria
    # that failed, in the words of whoever judged them.
    issues = list(facts.get("closure_issues") or facts.get("issues") or [])
    verdicts["the issues carry the advisor's own note"] = (
        bool(issues)
        and any(LEAD_NOTE in item for item in issues)
    )
    verdicts["the issues no longer claim that nobody judged the criterion"] = (
        bool(issues)
        and not any(MACHINE_COMPLAINT in item for item in issues)
    )

    # The advisor gets a section of its own in the brief.
    verdicts["the next attempt is shown the advisor's section"] = (
        bool(second_brief)
        and "ADVISOR" in second_brief.upper()
    )
    verdicts["the next attempt sees the advisor's own reason"] = (
        bool(second_brief) and LEAD_REASON in second_brief
    )
    verdicts["the next attempt sees the advisor's judgement of the criterion"] = (
        bool(second_brief)
        and LEAD_NOTE in second_brief
        and "[c1]" in second_brief
    )

    # The reflector is the role that says *how* to fix what failed, so
    # it has to know what the advisor concluded.
    first_reflector = reflectors[0] if reflectors else ""
    verdicts["the reflector is shown the advisor's words"] = (
        bool(first_reflector)
        and (
            LEAD_REASON in _brief_of(first_reflector)
            or LEAD_REASON in first_reflector
        )
    )

    # No regressions: the advisor's own stop still stops the run. This
    # one is read from its own run - the escalation run - and not from
    # the round that carries the advisor's judgement, or the two
    # readings would be talking about two different runs.
    escalated = facts.get("escalated") or {}
    verdicts["the advisor's escalation still stops the run early"] = (
        str(escalated.get("status") or "") == "escalated"
        and not bool(escalated.get("closed"))
        and int(escalated.get("lead_calls") or 0) == 1
        and int(escalated.get("worker_calls") or 0) == 1
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading, made from the raw state the roles were given.

    The first reading looks at the brief. This one parses the
    ``<<MAS_STATE>>`` block and asks what the state itself carried -
    because the brief and the state are two different channels, and a
    fact that reaches one of them is not necessarily in the other.
    """
    verdicts: Dict[str, bool] = {}
    workers = list(facts.get("workers") or [])
    second_state = _state_of(workers[1] if len(workers) > 1 else "")
    advice = second_state.get("lead_advice") or {}
    if not isinstance(advice, dict):
        advice = {}

    issues = list(facts.get("issues") or [])
    verdicts["the issues carry the advisor's own note"] = (
        bool(issues)
        and any(LEAD_NOTE in item for item in issues)
    )
    verdicts["the issues no longer claim that nobody judged the criterion"] = (
        bool(issues)
        and not any(MACHINE_COMPLAINT in item for item in issues)
    )

    verdicts["the next attempt is shown the advisor's section"] = bool(advice)
    verdicts["the next attempt sees the advisor's own reason"] = (
        LEAD_REASON in str(advice.get("reason") or "")
    )
    judged = advice.get("judged") or []
    verdicts["the next attempt sees the advisor's judgement of the criterion"] = (
        any(
            str(item.get("id") or "") == "c1"
            and LEAD_NOTE in str(item.get("note") or "")
            for item in judged
            if isinstance(item, dict)
        )
    )

    reflectors = list(facts.get("reflectors") or [])
    reflector_state = _state_of(reflectors[0] if reflectors else "")
    reflector_advice = reflector_state.get("lead_advice") or {}
    if not isinstance(reflector_advice, dict):
        reflector_advice = {}
    reflector_notes = " ".join(
        str(item.get("note") or "")
        for item in (reflector_advice.get("judged") or [])
        if isinstance(item, dict)
    )
    verdicts["the reflector is shown the advisor's words"] = (
        LEAD_REASON in str(reflector_advice.get("reason") or "")
        or LEAD_NOTE in reflector_notes
    )

    escalated = facts.get("escalated") or {}
    verdicts["the advisor's escalation still stops the run early"] = (
        str(escalated.get("status") or "") == "escalated"
        and not bool(escalated.get("closed"))
        and int(escalated.get("lead_calls") or 0) == 1
        and int(escalated.get("worker_calls") or 0) == 1
    )
    return verdicts


def _facts_of(root: pathlib.Path) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    facts: Dict[str, Any] = {}
    facts.update(_observe(root / "revise"))
    facts["escalated"] = _observe(root / "escalate", decision="escalate")
    facts["met"] = _observe(root / "met", decision="revise", met=True)
    return facts


def _verdicts_with_regressions(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The full set, including the two no-regression observations.

    The escalation and the "judged as met" observations are read from
    their own runs, so they are added here rather than inside the two
    readings - the readings themselves only look at the round that
    carries the advisor's judgement.
    """
    verdicts = _verdicts(facts)
    met = facts.get("met") or {}
    verdicts["a criterion the advisor judged as met still closes the loop"] = (
        str(met.get("status") or "") == "closed"
        and bool(met.get("closed"))
    )
    return verdicts


def _verdicts_independent_with_regressions(
    facts: Dict[str, Any],
) -> Dict[str, bool]:
    """The second reading, with the same two regressions added."""
    verdicts = _verdicts_independent(facts)
    met = facts.get("met") or {}
    verdicts["a criterion the advisor judged as met still closes the loop"] = (
        str(met.get("status") or "") == "closed"
        and bool(met.get("closed"))
    )
    return verdicts


def check_lead_reaches_the_next_worker(iterations: int = 3) -> Dict[str, Any]:
    """The sixtieth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-lead2-"))
        try:
            try:
                facts = _facts_of(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts_with_regressions(facts)
            independent = _verdicts_independent_with_regressions(facts)
            for label in LEAD_REACHES_THE_NEXT_WORKER_OBSERVATIONS:
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
            # The break is on the **wiring**: the advisor's judgement
            # stops being applied to the scores, and its words stop
            # being carried into the next iteration. That is exactly
            # what the code did before this release - and the point
            # of the break is that the advisor is still consulted and
            # still paid for: the answer is not acted on, not skipped.
            import mas.engine as engine_module

            real_apply = engine_module.LoopEngine._apply_lead_judgement
            real_carry = engine_module._carry_feedback

            def _no_judgement(self, scores, checks, lead):
                """Exactly what the code did before this release."""
                return list(scores)

            def _no_words(state, last_artifact, last_reflection,
                          previous_artifact, last_lead=None, _real=real_carry):
                """The advice is computed and then dropped."""
                _real(
                    state,
                    last_artifact,
                    last_reflection,
                    previous_artifact,
                    last_lead,
                )
                state.pop("lead_advice", None)

            try:
                engine_module.LoopEngine._apply_lead_judgement = _no_judgement
                engine_module._carry_feedback = _no_words
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts_with_regressions(
                        _facts_of(
                            pathlib.Path(
                                tempfile.mkdtemp(prefix="mas-selftest-lead2-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                engine_module.LoopEngine._apply_lead_judgement = real_apply
                engine_module._carry_feedback = real_carry

            if broken.get(
                "the issues carry the advisor's own note"
            ) or broken.get("the next attempt sees the advisor's own reason"):
                failures.append(
                    "round %d: the gate did not notice that the advisor's "
                    "judgement stops reaching the next worker - it cannot "
                    "fail, so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "lead_reaches_the_next_worker",
        "title": "the advisor's judgement has to reach the next worker",
        "skipped": False,
        "invariant": (
            "the advisor judges a criterion and writes its own note, but "
            "the issues list is built before it is consulted and never "
            "rebuilt - so the run still says 'unjudged' after the advisor "
            "spoke, and the advisor's own words never reach the worker "
            "that has to fix it"
        ),
        "detail": (
            "the issues still said 'unjudged' after the advisor had judged "
            "the criterion; the next attempt was told to fix a complaint "
            "nobody could act on, and the advisor's sentence was dropped"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_lead_reaches_the_next_worker.CHECK_NAME = (  # type: ignore[attr-defined]
    "lead_reaches_the_next_worker"
)

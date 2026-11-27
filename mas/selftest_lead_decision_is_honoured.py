# -*- coding: utf-8 -*-
"""The fifty-ninth gate: the lead's decision is actually read.

The gap
------
The lead is one of the five roles. Its contract asks for a decision:

    {"decision": "close|revise|escalate", "reason": str,
     "judged": [{"id": str, "met": bool, "note": str}]}

and it says plainly: *"Use "escalate" when the remaining work needs a
human."* The README promises the same. The report even printed the
decision - so a reader of a replay could see the word "escalate" on
the lead's line.

The engine never read it. ``lead.get("decision")`` had **zero**
occurrences in ``mas/engine.py``; the only thing the engine took from
the advisor was ``judged``. So the word in the role prompt was a lie,
and the report showed a decision that had never happened.

Measured, with a probe provider:

    === lead says: escalate ===
      status: budget_exhausted  closed: False
      history decisions: ['revise', 'revise', 'revise']
    === lead says: close ===      <- byte-identical
    === lead says: revise ===     <- byte-identical

Three different answers, one behaviour. Worse: a lead that said
"escalate" was *ignored*, and the run kept burning rounds until the
iteration ceiling - so the advisor's request for a human cost the
user every remaining round.

There was a second bug in the same branch. The engine escalated on
its own evidence (the deliverable stopped changing) and then the lead
branch reset ``decision`` to "close" or "revise" - so a stagnation
stop was silently demoted to "one more round of the loop that is
already stuck". The engine's own justified stop did not survive the
advisor's answer.

The fix
------
The lead's decision is read, in one place, with one rule:

* ``escalate`` stops the loop and hands the problem to a human, with
  the advisor's own reason attached - **and only when the run is not
  already closing** (a run that passed everything has nothing for a
  human to do);
* ``close`` and ``revise`` do **not** override the engine's own
  judgement. A failed machine check is a fact and the budget is the
  user's; the advisor is an advisor, not a judge. This invariant was
  already written down in the README, and the fix does not weaken it.

The engine's own stop also survives now: a stagnation escalation is
no longer reset to "revise" by the lead branch.

This gate watches:

* a lead that says "escalate" stops the run early;
* the run does not burn its remaining rounds after the escalation;
* the ticket says the advisor asked for a human;
* the ticket carries the advisor's own reason;
* the ticket carries the advisor's structured answer;
* a lead that says "close" does not close a run whose machine check
  failed (the advisor cannot overrule a fact);
* the engine's own stagnation stop is not demoted by the advisor;
* and the gate proves it can fail: the engine stops reading the
  decision, and the gate has to notice.

The break is on the **reader**, and the two readings deliberately
look at different surfaces: one reads the run's outcome, the other
reads the ticket the run left behind. A break that turned both
surfaces wrong in the same way would let the two readings agree on a
broken world - and a gate whose two readings agree on a broken world
is not watching anything.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: The observations the gate makes.
LEAD_DECISION_IS_HONOURED_OBSERVATIONS = (
    "a lead that says escalate stops the run early",
    "the run does not burn its remaining rounds after the escalation",
    "the ticket says the advisor asked for a human",
    "the ticket carries the advisor's own reason",
    "the ticket carries the advisor's structured answer",
    "a lead that says close does not close a failed check",
    "the engine's own stagnation stop is not demoted by the advisor",
)

#: The advisor's reason. The marker is unique on purpose: a repeated
#: string would also show up in the tail of a clipped text, and the
#: gate would read a broken world as a working one.
LEAD_REASON = "advisor-reason-marker-LEAD-REASON-UNIQUE"


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
    """A provider whose advisor answers whatever the gate asked for."""

    name = "probe"
    model = "probe-model"

    def __init__(self, decision: str, met: Optional[bool] = False,
                 worker_text: str = "交付物") -> None:
        self.decision = decision
        self.met = met
        self.worker_text = worker_text
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
            judged: List[Dict[str, Any]] = []
            if self.met is not None:
                judged.append(
                    {"id": "c1", "met": self.met, "note": "advisor note"}
                )
            return {
                "text": json.dumps(
                    {
                        "decision": self.decision,
                        "reason": LEAD_REASON,
                        "judged": judged,
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "verifier":
            # The verifier stays silent about the criterion, so the
            # criterion has no verdict and the advisor has something
            # to decide - which is the only situation in which the
            # lead is consulted at all.
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
                        "summary": "复盘",
                        "issues": ["改"],
                        "next_action": "revise",
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        return {"text": self.worker_text, "usage": usage, "raw": {}}


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
    """One criterion with no machine check.

    A criterion with a machine check is decided by the machine, and a
    lead that disagrees with a fact is not consulted at all - so the
    advisor could not be observed at all with one.
    """
    return [{"id": "c1", "desc": "必须包含关键词"}]


def _observe(
    root: pathlib.Path,
    decision: str,
    max_iterations: int = 3,
    met: Optional[bool] = False,
    worker_text: str = "交付物",
    stagnation_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Run one real loop and record what came out of it."""
    from mas.engine import LoopEngine

    config = _config(root, max_iterations)
    if stagnation_limit is not None:
        config["stagnation_limit"] = stagnation_limit
    config_path = root / "config.json"
    probe = _Probe(decision, met=met, worker_text=worker_text)
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        result = engine.run(goal="做一个东西", criteria=_criteria())

    ticket: Dict[str, Any] = {}
    if result.ticket_id:
        try:
            ticket = engine.tickets.get(result.ticket_id) or {}
        except Exception:  # noqa: BLE001 - the failure is the subject
            ticket = {}
    payload = dict(ticket.get("payload") or {})
    return {
        "calls": list(probe.calls),
        "status": str(result.status or ""),
        "closed": bool(result.closed),
        "error": str(result.error or ""),
        "history_decisions": [
            str(row.get("decision") or "") for row in (result.history or [])
        ],
        "ticket": ticket,
        "payload": payload,
        "reason": str(payload.get("reason") or ""),
        "lead_verdict": payload.get("lead_verdict"),
    }


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the run's own outcome."""
    verdicts: Dict[str, bool] = {}
    calls = list(facts.get("calls") or [])
    lead_calls = [role for role, _ in calls if role == "lead"]

    # The advisor has to have been asked at all - otherwise the gate
    # is reading a run in which the advisor never spoke.
    verdicts["a lead that says escalate stops the run early"] = (
        bool(lead_calls)
        and str(facts.get("status") or "") == "escalated"
        and not bool(facts.get("closed"))
        and "escalate" in list(facts.get("history_decisions") or [])
    )

    # The escalation has to save the rounds the advisor asked to
    # save. A run allowed three rounds that stops after the first
    # advisor call has honoured the request; one that keeps going has
    # not.
    verdicts["the run does not burn its remaining rounds after the escalation"] = (
        bool(lead_calls)
        and len(lead_calls) == 1
        and len([r for r, _ in calls if r == "worker"]) == 1
    )

    # The ticket has to name the cause: an advisor asked for a human.
    reason = str(facts.get("reason") or "")
    verdicts["the ticket says the advisor asked for a human"] = (
        "lead" in reason.lower() and "escalat" in reason.lower()
    )

    # And it has to carry the advisor's own words - a handover that
    # says "escalated" without saying why is a note, not a handover.
    verdicts["the ticket carries the advisor's own reason"] = (
        LEAD_REASON in reason
    )

    # And the structured answer behind those words.
    verdict = facts.get("lead_verdict")
    verdicts["the ticket carries the advisor's structured answer"] = (
        isinstance(verdict, dict)
        and str(verdict.get("decision") or "") == "escalate"
        and str(verdict.get("reason") or "") == LEAD_REASON
    )

    close = facts.get("close") or {}
    # The advisor cannot overrule a fact. A machine check that failed
    # stays failed, whatever the advisor said.
    verdicts["a lead that says close does not close a failed check"] = (
        bool(close.get("calls"))
        and not bool(close.get("closed"))
        and str(close.get("status") or "") != "closed"
    )

    stagnant = facts.get("stagnant") or {}
    # The engine's own stop has to survive the advisor's answer: a
    # stagnation stop that was demoted to "revise" would keep the
    # stuck loop running.
    verdicts["the engine's own stagnation stop is not demoted by the advisor"] = (
        str(stagnant.get("status") or "") == "escalated"
        and "stagnation" in str(stagnant.get("error") or "").lower()
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading, made from the ticket the run left behind.

    The first reading asks about the run's outcome. This one asks
    about the *ticket* - because the ticket is what a human actually
    reads, and a run whose ticket says nothing about the advisor has
    not really handed the problem over.
    """
    verdicts: Dict[str, bool] = {}
    payload = dict(facts.get("payload") or {})
    reason = str(payload.get("reason") or "")
    verdict = payload.get("lead_verdict")

    verdicts["a lead that says escalate stops the run early"] = (
        str(payload.get("status") or "") == "escalated"
    )
    verdicts["the run does not burn its remaining rounds after the escalation"] = (
        len(list(facts.get("history_decisions") or [])) == 1
    )
    verdicts["the ticket says the advisor asked for a human"] = (
        "lead" in reason.lower() and "escalat" in reason.lower()
    )
    verdicts["the ticket carries the advisor's own reason"] = (
        LEAD_REASON in str(payload.get("reason") or "")
    )
    verdicts["the ticket carries the advisor's structured answer"] = (
        isinstance(verdict, dict)
        and str(verdict.get("reason") or "") == LEAD_REASON
        and isinstance(verdict.get("judged"), list)
    )

    close_payload = dict((facts.get("close") or {}).get("payload") or {})
    verdicts["a lead that says close does not close a failed check"] = (
        str(close_payload.get("status") or "") != "closed"
    )
    stagnant_payload = dict((facts.get("stagnant") or {}).get("payload") or {})
    verdicts["the engine's own stagnation stop is not demoted by the advisor"] = (
        str(stagnant_payload.get("status") or "") == "escalated"
        and "stagnation" in str(stagnant_payload.get("reason") or "").lower()
    )
    return verdicts


def _facts_of(root: pathlib.Path) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    facts: Dict[str, Any] = {}
    facts.update(_observe(root / "escalate", "escalate"))
    facts["close"] = _observe(root / "close", "close")
    facts["stagnant"] = _observe(
        root / "stagnant",
        "close",
        max_iterations=6,
        worker_text="恒定交付物",
        stagnation_limit=2,
    )
    return facts


def check_lead_decision_is_honoured(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-ninth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-lead-"))
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
            for label in LEAD_DECISION_IS_HONOURED_OBSERVATIONS:
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
            # The break is on the **reader**: the engine stops reading
            # the advisor's decision, so the advisor's "escalate" is
            # ignored again - exactly what the code did before this
            # release. The advisor is still consulted and still paid
            # for: the point of the break is that the answer is not
            # acted on, not that the call is skipped.
            import mas.engine as engine_module

            real_consult = engine_module.LoopEngine._consult_lead

            def _no_decision(self, verify_state, scores, checks, _real=real_consult):
                """The advisor answers - the decision is just dropped."""
                answer = dict(_real(self, verify_state, scores, checks))
                answer.pop("decision", None)
                answer.pop("reason", None)
                return answer

            try:
                engine_module.LoopEngine._consult_lead = _no_decision
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            pathlib.Path(
                                tempfile.mkdtemp(prefix="mas-selftest-lead-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                engine_module.LoopEngine._consult_lead = real_consult

            if broken.get("a lead that says escalate stops the run early"):
                failures.append(
                    "round %d: the gate did not notice that the advisor's "
                    "decision is ignored again - it cannot fail, so it "
                    "does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "lead_decision_is_honoured",
        "title": "主线的决定真的被读到了",
        "skipped": False,
        "invariant": (
            "主线角色被要求给出 close/revise/escalate，但引擎只读了 "
            "judged：三个不同的答案产生完全相同的运行——它说的 "
            "escalate 是一句空话，人还要多付几轮钱；引擎自己的"
            "停滞升级也会被主线的回答降级回 revise"
        ),
        "detail": (
            "主线说 escalate、close、revise，运行结果一模一样；"
            "它请求人工的那一次，白白烧完了剩下的所有轮次"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_lead_decision_is_honoured.CHECK_NAME = (  # type: ignore[attr-defined]
    "lead_decision_is_honoured"
)

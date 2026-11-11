"""The loop engine - the part that actually closes loops.

Design contract
---------------
A loop is *closed* when all of the following are true for the current task:

1. an artifact was produced,
2. every acceptance criterion received a verdict,
3. every required criterion passed,
4. a decision was taken and written to the ledger,
5. the ledger contains one entry per cycle.

Anything else is not a closed loop: it is either a failure, a budget stop or
an escalation. There is no third state where the loop "kind of finished".

Determinism boundary
--------------------
Criteria may carry a machine check (``contains``, ``min_chars``, ``regex``,
``json_keys`` ...). Machine checks are evaluated by this module, never by the
model, so a pass/fail decision is reproducible. Criteria without a machine
check fall back to the model verifier's judgement.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from mas.ledger import Ledger
from mas.providers import Provider, ProviderError, build_provider
from mas.roles import Roles
from mas.tickets import TicketStore
from mas.util import now_iso, parse_json_loose, sha256_text, short_sha, strip_reasoning

STATUS_CLOSED = "closed"
STATUS_ESCALATED = "escalated"
STATUS_BUDGET = "budget_exhausted"
STATUS_ERROR = "error"


@dataclass
class Criterion:
    id: str
    desc: str
    check: Optional[Dict[str, Any]] = None
    required: bool = True

    @classmethod
    def from_raw(cls, raw: Any, index: int) -> "Criterion":
        if isinstance(raw, str):
            return cls(id=f"c{index + 1}", desc=raw, check=None, required=True)
        if not isinstance(raw, dict):
            return cls(id=f"c{index + 1}", desc=str(raw), check=None, required=True)
        desc = str(raw.get("desc") or raw.get("description") or raw.get("text") or "")
        check = raw.get("check")
        if not isinstance(check, dict):
            check = None
        return cls(
            id=str(raw.get("id") or f"c{index + 1}"),
            desc=desc,
            check=check,
            required=bool(raw.get("required", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "desc": self.desc, "check": self.check, "required": self.required}


@dataclass
class RunResult:
    run_id: str
    task_id: str
    status: str
    iterations: int
    artifact: str = ""
    artifact_sha: str = ""
    issues: List[str] = field(default_factory=list)
    scores: List[Dict[str, Any]] = field(default_factory=list)
    ticket_id: Optional[str] = None
    tokens_used: int = 0
    ledger_path: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""

    @property
    def closed(self) -> bool:
        return self.status == STATUS_CLOSED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "status": self.status,
            "closed": self.closed,
            "iterations": self.iterations,
            "artifact_sha": self.artifact_sha,
            "artifact_preview": self.artifact[:800],
            "issues": self.issues,
            "scores": self.scores,
            "ticket_id": self.ticket_id,
            "tokens_used": self.tokens_used,
            "ledger_path": self.ledger_path,
            "history": self.history,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


def evaluate_check(criterion: Criterion, artifact: str) -> Dict[str, Any]:
    """Evaluate the machine check of a criterion against an artifact."""
    check = criterion.check
    if not check:
        return {"id": criterion.id, "pass": None, "note": "no machine check", "source": "none"}
    kind = str(check.get("type") or "").lower()
    arg = check.get("arg")
    try:
        if kind == "contains":
            items = arg if isinstance(arg, list) else [arg]
            items = [str(x) for x in items if x is not None]
            missing = [x for x in items if x not in artifact]
            passed = not missing
            note = "all required strings present" if passed else f"missing: {missing}"
        elif kind == "not_contains":
            items = arg if isinstance(arg, list) else [arg]
            present = [str(x) for x in items if x is not None and str(x) in artifact]
            passed = not present
            note = "no forbidden string found" if passed else f"forbidden string present: {present}"
        elif kind == "min_chars":
            limit = int(arg or 0)
            passed = len(artifact) >= limit
            note = f"length {len(artifact)} >= {limit}" if passed else f"length {len(artifact)} < {limit}"
        elif kind == "max_chars":
            limit = int(arg or 0)
            passed = len(artifact) <= limit
            note = f"length {len(artifact)} <= {limit}" if passed else f"length {len(artifact)} > {limit}"
        elif kind == "regex":
            pattern = str(arg or "")
            matched = re.search(pattern, artifact, re.DOTALL) is not None
            passed = matched
            note = f"regex /{pattern}/ matched" if matched else f"regex /{pattern}/ not found"
        elif kind == "lines_min":
            limit = int(arg or 0)
            count = len([line for line in artifact.splitlines() if line.strip()])
            passed = count >= limit
            note = f"{count} non-empty lines >= {limit}" if passed else f"{count} non-empty lines < {limit}"
        elif kind == "section":
            marker = f"## {arg}"
            passed = marker in artifact
            note = f"section '{marker}' found" if passed else f"section '{marker}' missing"
        elif kind == "json_keys":
            try:
                data = json.loads(artifact)
            except ValueError:
                data = None
            keys = arg if isinstance(arg, list) else [arg]
            if not isinstance(data, dict):
                passed = False
                note = "artifact is not a JSON object"
            else:
                missing = [str(k) for k in keys if str(k) not in data]
                passed = not missing
                note = "all keys present" if passed else f"missing keys: {missing}"
        else:
            return {"id": criterion.id, "pass": None, "note": f"unknown check type: {kind}", "source": "none"}
    except (TypeError, ValueError) as exc:
        return {"id": criterion.id, "pass": False, "note": f"check error: {exc}", "source": "rule"}
    return {"id": criterion.id, "pass": passed, "note": note, "source": "rule"}


class LoopEngine:
    """Runs a task through plan -> work -> verify -> reflect until it closes."""

    def __init__(
        self,
        config: Dict[str, Any],
        provider: Optional[Provider] = None,
        team: Optional[Dict[str, Any]] = None,
        ledger: Optional[Ledger] = None,
        tickets: Optional[TicketStore] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config
        self.provider = provider or build_provider(config)
        self.team = team or {}
        self.roles = Roles(self.team)
        self.ledger = ledger or Ledger(self.ledger_path(config))
        self.tickets = tickets or TicketStore(self.tickets_dir(config))
        self.clock = clock or time.time

    # ------------------------------------------------------------------ paths
    @staticmethod
    def state_root(config: Dict[str, Any]) -> Any:
        from mas.config import state_dir

        return state_dir(config)

    @classmethod
    def ledger_path(cls, config: Dict[str, Any]) -> str:
        return str(cls.state_root(config) / "ledgers" / "runs.jsonl")

    @classmethod
    def tickets_dir(cls, config: Dict[str, Any]) -> str:
        return str(cls.state_root(config) / "tickets")

    # ------------------------------------------------------------------- main
    def run(
        self,
        goal: str,
        criteria: List[Any],
        title: str = "",
        run_id: Optional[str] = None,
        human_note: str = "",
    ) -> RunResult:
        run_id = run_id or "R-" + uuid.uuid4().hex[:10]
        task_id = f"{run_id}-1"
        crits = [Criterion.from_raw(raw, index) for index, raw in enumerate(criteria or [])]
        if not crits:
            crits = [Criterion(id="c1", desc=goal, check=None)]

        budget = dict(self.config.get("budget") or {})
        max_iterations = int(budget.get("max_iterations", 8))
        deadline_sec = float(budget.get("deadline_sec", 900))
        max_tokens = int(budget.get("max_tokens", 200000))
        stagnation_limit = max(1, int(self.config.get("stagnation_limit", 3)))

        started_at = now_iso()
        start = self.clock()
        tokens = 0
        attempt = 0
        issues: List[str] = []
        artifact = ""
        scores: List[Dict[str, Any]] = []
        history: List[Dict[str, Any]] = []
        ticket_id: Optional[str] = None
        status = STATUS_BUDGET
        error = ""
        stagnant = 0
        last_sha = ""
        plan_payload: Dict[str, Any] = {}

        while True:
            if attempt >= max_iterations:
                status = STATUS_BUDGET
                error = f"budget: reached max_iterations={max_iterations}"
                break
            if (self.clock() - start) > deadline_sec:
                status = STATUS_BUDGET
                error = f"budget: exceeded deadline_sec={deadline_sec:.0f}"
                break

            attempt += 1
            state: Dict[str, Any] = {
                "run_id": run_id,
                "task_id": task_id,
                "goal": goal,
                "title": title or goal[:40],
                "attempt": attempt,
                "max_iterations": max_iterations,
                "criteria": [c.to_dict() for c in crits],
                "issues": issues,
                "human_note": human_note,
                "plan": plan_payload,
            }

            try:
                if attempt == 1:
                    plan_payload = self._call_json("planner", state)
                    self._log(run_id, task_id, attempt, "planner", {"plan": plan_payload})

                work = self._call_text("worker", state)
                artifact = strip_reasoning(work["text"])
                tokens += int(work["usage"].get("total_tokens", 0))

                checks = [evaluate_check(c, artifact) for c in crits]
                verify_state = dict(state)
                verify_state["candidate_artifact"] = artifact
                verify_state["checks"] = checks
                verdict = self._call_json("verifier", verify_state)
                scores = self._merge_scores(crits, checks, verdict)
                tokens += int(verdict.get("_usage", {}).get("total_tokens", 0))
                passed = bool(scores) and all(bool(s["pass"]) for s in scores)

                reflection: Dict[str, Any] = {}
                if not passed:
                    failed = [s for s in scores if not s["pass"]]
                    issues = [f"{s['id']}: {s['note']}" for s in failed]
                    reflect_state = dict(state)
                    reflect_state["issues"] = issues
                    reflection = self._call_json("reflector", reflect_state)

                art_sha = sha256_text(artifact)
                stagnant = stagnant + 1 if art_sha == last_sha else 0
                last_sha = art_sha

                decision = "close" if passed else "revise"
                if not passed and stagnant >= stagnation_limit:
                    decision = "escalate"
                if tokens >= max_tokens:
                    decision = "escalate"
                    error = f"budget: tokens {tokens} >= {max_tokens}"

                closure = {
                    "artifact_sha": art_sha,
                    "verdict": "pass" if passed else "fail",
                    "decision": decision,
                    "scores": scores,
                    "issues": issues,
                    "stagnation": stagnant,
                    "closed": bool(passed),
                }
                self._log(run_id, task_id, attempt, "closure", closure)
                history.append(
                    {
                        "iteration": attempt,
                        "artifact_sha": art_sha,
                        "artifact_chars": len(artifact),
                        "verdict": closure["verdict"],
                        "decision": decision,
                        "stagnation": stagnant,
                        "issues": issues,
                        "reflection": reflection,
                    }
                )

                if passed:
                    status = STATUS_CLOSED
                    break
                if decision == "escalate":
                    ticket = self.tickets.create(
                        "loop_escalation",
                        {
                            "run_id": run_id,
                            "task_id": task_id,
                            "goal": goal,
                            "title": title or goal[:40],
                            "criteria": [c.to_dict() for c in crits],
                            "last_artifact": artifact,
                            "issues": issues,
                            "attempts": attempt,
                            "history": history,
                            "stagnation": stagnant,
                            "reason": error or "loop could not close itself",
                        },
                    )
                    ticket_id = ticket["id"]
                    status = STATUS_ESCALATED
                    break
            except ProviderError as exc:
                error = f"provider error: {exc}"
                self._log(run_id, task_id, attempt, "error", {"error": error})
                status = STATUS_ERROR
                break

        return RunResult(
            run_id=run_id,
            task_id=task_id,
            status=status,
            iterations=attempt,
            artifact=artifact,
            artifact_sha=short_sha(artifact) if artifact else "",
            issues=issues,
            scores=scores,
            ticket_id=ticket_id,
            tokens_used=tokens,
            ledger_path=str(self.ledger.path),
            history=history,
            started_at=started_at,
            finished_at=now_iso(),
            error=error,
        )

    # --------------------------------------------------------------- helpers
    def _log(self, run_id: str, task_id: str, iteration: int, role: str,
             payload: Dict[str, Any]) -> None:
        entry = {
            "run_id": run_id,
            "task_id": task_id,
            "iteration": iteration,
            "role": role,
            "provider": self.provider.name,
            "model": self.provider.model,
            "ts": now_iso(),
        }
        entry.update(payload)
        self.ledger.append(entry)

    def _call_text(self, role: str, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.provider.complete(
            system=self.roles.system(role),
            prompt=self.roles.prompt(role, state),
            role=role,
            expect_json=False,
        )

    def _call_json(self, role: str, state: Dict[str, Any]) -> Dict[str, Any]:
        response = self.provider.complete(
            system=self.roles.system(role),
            prompt=self.roles.prompt(role, state),
            role=role,
            expect_json=True,
        )
        data = parse_json_loose(response["text"]) or {}
        data["_usage"] = response.get("usage", {})
        data["_raw"] = response.get("text", "")
        return data

    @staticmethod
    def _merge_scores(
        crits: List[Criterion],
        checks: List[Dict[str, Any]],
        verdict: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        model_scores = {}
        for item in verdict.get("scores") or []:
            if isinstance(item, dict) and item.get("id"):
                model_scores[str(item["id"])] = item
        merged: List[Dict[str, Any]] = []
        for criterion, check in zip(crits, checks):
            if not criterion.required:
                merged.append(
                    {"id": criterion.id, "pass": True, "note": "optional criterion", "source": "skipped"}
                )
                continue
            if check.get("pass") is not None:
                merged.append(
                    {
                        "id": criterion.id,
                        "pass": bool(check["pass"]),
                        "note": str(check.get("note", "")),
                        "source": "rule",
                    }
                )
                continue
            model = model_scores.get(criterion.id)
            if model is None:
                merged.append(
                    {
                        "id": criterion.id,
                        "pass": False,
                        "note": "unjudged: verifier 未给出该标准的判定",
                        "source": "model-missing",
                    }
                )
            else:
                merged.append(
                    {
                        "id": criterion.id,
                        "pass": bool(model.get("pass")),
                        "note": str(model.get("note", "")),
                        "source": "model",
                    }
                )
        return merged

    # --------------------------------------------------------------- resume
    def resume(self, ticket_id: str, note: str = "") -> RunResult:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket: {ticket_id}")
        if ticket.get("decision") == "rejected":
            raise ValueError(f"ticket was rejected by a human: {ticket_id}")
        payload = ticket.get("payload") or {}
        result = self.run(
            goal=str(payload.get("goal", "")),
            criteria=payload.get("criteria") or [],
            title=str(payload.get("title", "")),
            human_note=note or str(ticket.get("note") or ""),
        )
        if ticket.get("state") != "resolved":
            self.tickets.resolve(ticket_id, "resumed", note)
        return result

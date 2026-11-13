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
from mas.orchestration import (
    ParallelFailure,
    aggregate_reviews,
    angle_instruction,
    persona_instruction,
    pick_angles,
    pick_personas,
    run_parallel,
)
from mas.providers import Provider, ProviderError, build_provider_chain
from mas.roles import Roles
from mas.tickets import TicketStore
from mas.util import (
    _as_bool,
    normalize_verdict,
    now_iso,
    parse_criterion_shorthand,
    parse_json_loose,
    sha256_text,
    short_sha,
    strip_reasoning,
)

STATUS_CLOSED = "closed"
STATUS_ESCALATED = "escalated"
STATUS_BUDGET = "budget_exhausted"
STATUS_ERROR = "error"


def _required_flag(value: Any) -> bool:
    """Read the ``required`` flag of a criterion without guessing.

    ``bool("false")`` is True, so a criterion written as
    ``{"required": "false"}`` - a very natural mistake when a criterion
    travels through JSON, an MCP client or a shell script - would be
    treated as *required*. The user asked for a soft standard and got a
    hard one, and the loop would then refuse to close on it.

    The rule is the same as everywhere else in this project: read the
    *meaning* of the value, never its truthiness. A value that cannot be
    read stays required, because the strict reading is the safe one -
    an unreadable flag must never silently relax a standard.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    decided = _as_bool(value)
    if decided is None:
        return True
    return decided


@dataclass
class Criterion:
    id: str
    desc: str
    check: Optional[Dict[str, Any]] = None
    required: bool = True

    @classmethod
    def from_raw(cls, raw: Any, index: int) -> "Criterion":
        if isinstance(raw, str):
            # A shorthand such as "regex:foo" carries a machine check. The
            # parser runs here - not only in the CLI - so every entry point
            # (CLI, MCP, HTTP, ledger replay) treats it the same way.
            parsed = parse_criterion_shorthand(raw)
            return cls(
                id=str(parsed.get("id") or f"c{index + 1}"),
                desc=str(parsed.get("desc") or raw),
                check=parsed.get("check"),
                required=_required_flag(parsed.get("required")),
            )
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
            required=_required_flag(raw.get("required")),
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


# A machine check whose argument is missing or degenerate cannot decide
# anything. The old code silently degraded such a check into a constant:
# an empty regex matched everything, an empty ``contains`` list required
# nothing, and a missing ``min_chars`` became "at least 0 characters". The
# loop then reported a green light for a criterion nobody had actually
# specified - a fake closure produced by a typo in the standard itself.
#
# These helpers describe, per type, what a *usable* argument looks like. A
# check that does not meet the requirement is not a failing artifact, it is
# a broken check: it is reported as unjudged (never passed) so the caller
# can tell "the work did not meet the bar" apart from "the bar was never
# written down".
_ARG_REQUIRED = {
    "contains": "a string or a list of strings to look for",
    "not_contains": "a string or a list of strings to forbid",
    "min_chars": "a minimum character count",
    "max_chars": "a maximum character count",
    "regex": "a regular expression",
    "lines_min": "a minimum number of non-empty lines",
    "section": "the heading text to require",
    "json_keys": "a key name or a list of key names",
}

_INT_ARG_TYPES = {"min_chars", "max_chars", "lines_min"}


def _check_arg_problem(kind: str, arg: Any) -> str:
    """Return a human-readable reason why ``arg`` cannot be used, or "".

    The empty string means the argument is usable.
    """
    requirement = _ARG_REQUIRED.get(kind)
    if requirement is None:
        return ""
    if kind in _INT_ARG_TYPES:
        if arg is None or (isinstance(arg, str) and not arg.strip()):
            return f"missing argument: {kind} needs {requirement}"
        try:
            value = int(arg)
        except (TypeError, ValueError):
            return f"invalid argument: {kind} needs {requirement}, got {arg!r}"
        if value < 0:
            return f"invalid argument: {kind} needs a non-negative count, got {value}"
        return ""
    if kind == "regex":
        if arg is None or not str(arg):
            return f"missing argument: {kind} needs {requirement}"
        try:
            re.compile(str(arg))
        except re.error as exc:
            return f"invalid argument: {kind} cannot compile /{arg}/: {exc}"
        return ""
    if kind == "section":
        if arg is None or not str(arg).strip():
            return f"missing argument: {kind} needs {requirement}"
        return ""
    if kind == "json_keys":
        keys = arg if isinstance(arg, list) else [arg]
        keys = [str(k) for k in keys if k is not None]
        if not keys or not all(k.strip() for k in keys):
            return f"missing argument: {kind} needs {requirement}"
        return ""
    # contains / not_contains
    items = arg if isinstance(arg, list) else [arg]
    items = [str(x) for x in items if x is not None]
    if not items or not all(x != "" for x in items):
        return f"missing argument: {kind} needs {requirement}"
    return ""


def validate_criteria(crits: List[Criterion]) -> List[str]:
    """Return a list of human-readable problems with the criteria.

    A machine check with a missing or degenerate argument cannot decide
    anything. Catching that *before* the loop starts turns a silent fake
    closure into an immediate, actionable error - the run does not burn
    its whole budget discovering that the standard was never specified.
    """
    problems: List[str] = []
    for criterion in crits:
        if not criterion.check:
            continue
        kind = str(criterion.check.get("type") or "").lower()
        if kind not in _ARG_REQUIRED:
            # An unknown type is a typo in the type name, not a missing
            # argument. The checker refuses to decide it, so the run must
            # not start either - otherwise the criterion would be handed
            # to the model and could be closed by a guess.
            known = ", ".join(sorted(_ARG_REQUIRED))
            problems.append(
                f"{criterion.id}: unknown check type: {kind!r} "
                f"(known types: {known})"
            )
            continue
        problem = _check_arg_problem(kind, criterion.check.get("arg"))
        if problem:
            problems.append(f"{criterion.id}: {problem}")
    return problems


def evaluate_check(criterion: Criterion, artifact: str) -> Dict[str, Any]:
    """Evaluate the machine check of a criterion against an artifact."""
    check = criterion.check
    if not check:
        return {"id": criterion.id, "pass": None, "note": "no machine check", "source": "none"}
    kind = str(check.get("type") or "").lower()
    arg = check.get("arg")
    problem = _check_arg_problem(kind, arg)
    if problem:
        # A broken check is never a passing check.
        return {"id": criterion.id, "pass": None, "note": problem, "source": "invalid"}
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
            # A section is a *heading line*, not a substring anywhere in the
            # artifact. Matching the bare substring was wrong in three ways:
            # a body sentence mentioning "## 结论" counted as a section,
            # "## 结论分析" satisfied a check for "## 结论", and "### 结论"
            # passed even though it is a third-level heading. Now the line
            # must be exactly the second-level heading, with optional
            # trailing whitespace.
            marker = f"## {arg}"
            wanted = marker.strip()
            found = any(
                line.strip() == wanted
                for line in artifact.splitlines()
            )
            passed = found
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
            # An unknown type is a *broken check*, not an absent one. If it
            # stayed undecided the merge step would hand it to the model,
            # and a model that says "looks fine" would close a criterion
            # whose rule was never written - a fake closure.
            return {
                "id": criterion.id,
                "pass": None,
                "note": f"unknown check type: {kind}",
                "source": "invalid",
            }
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
        # A single provider stays a plain provider; a configured fallback
        # list turns it into a chain that survives one backend failing.
        self.provider = provider or build_provider_chain(config)
        self.team = team or {}
        self.roles = Roles(self.team)
        self.ledger = ledger or Ledger(self.ledger_path(config))
        self.tickets = tickets or TicketStore(self.tickets_dir(config))
        self.clock = clock or time.time

        orchestration = dict(self.config.get("orchestration") or {})
        # How many workers produce candidates in parallel. 1 keeps the
        # v0.1 single-worker behaviour exactly.
        self.parallel_workers = max(1, int(orchestration.get("parallel_workers", 1)))
        # How many independent verifiers review the artifact. 1 keeps v0.1.
        self.reviewers = max(1, int(orchestration.get("reviewers", 1)))
        # all | majority | any
        self.review_mode = str(orchestration.get("review_mode", "all"))
        # Optional anchor for the candidate-angle rotation.
        self.perspective = str(orchestration.get("perspective", "") or "")

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
        parallel: Optional[int] = None,
        reviewers: Optional[int] = None,
        review_mode: Optional[str] = None,
        perspective: str = "",
        parent_ticket_id: Optional[str] = None,
    ) -> RunResult:
        # Per-run overrides win over the configured defaults, so the CLI can
        # ask for a heavier setup for one task without touching the config.
        if parallel is not None:
            self.parallel_workers = max(1, int(parallel))
        if reviewers is not None:
            self.reviewers = max(1, int(reviewers))
        if review_mode:
            self.review_mode = str(review_mode)
        self.perspective = str(perspective or "")
        run_id = run_id or "R-" + uuid.uuid4().hex[:10]
        task_id = f"{run_id}-1"
        crits = [Criterion.from_raw(raw, index) for index, raw in enumerate(criteria or [])]
        if not crits:
            crits = [Criterion(id="c1", desc=goal, check=None)]
        problems = validate_criteria(crits)
        if problems:
            # Fail fast and loudly. A standard whose machine check cannot
            # decide anything must not be able to look like a closed loop.
            raise ValueError(
                "验收标准里有无法执行的机检规则:\n  - " + "\n  - ".join(problems)
            )

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

                candidates = self._produce_candidates(state)
                artifact = candidates["artifact"]
                tokens += candidates["tokens"]

                checks = [evaluate_check(c, artifact) for c in crits]
                verify_state = dict(state)
                verify_state["candidate_artifact"] = artifact
                verify_state["checks"] = checks
                verdict = self._review(verify_state)
                tokens += verdict.get("_usage", {}).get("total_tokens", 0)
                if self.reviewers > 1:
                    scores = self._merge_review_scores(crits, checks, verdict)
                else:
                    scores = self._merge_scores(crits, checks, verdict)

                # A verifier that stayed silent about a criterion is not a
                # failure of the artifact - it is a missing answer. Asking
                # the verifier again for the missing ids costs one cheap
                # call; regenerating the whole artifact costs a whole
                # iteration and usually changes nothing.
                if not self._all_judged(scores):
                    extra = self._ask_missing_verdicts(
                        verify_state, crits, checks, verdict, scores
                    )
                    if extra:
                        tokens += extra.get("_usage", {}).get("total_tokens", 0)
                        if self.reviewers > 1:
                            scores = self._merge_review_scores(crits, checks, extra)
                        else:
                            scores = self._merge_scores(crits, checks, extra)

                # Only the required criteria decide whether the loop closes.
                # An optional criterion may fail - it is recorded, it is
                # reported, but it never keeps the loop open forever.
                blocking = [s for s in scores if s.get("blocking", True)]
                passed = bool(scores) and all(bool(s["pass"]) for s in blocking)

                reflection: Dict[str, Any] = {}
                if not passed:
                    # Only blocking failures become issues: an optional
                    # failure is information, not a reason to revise.
                    failed = [s for s in blocking if not s["pass"]]
                    issues = [f"{s['id']}: {s['note']}" for s in failed]
                    reflect_state = dict(state)
                    reflect_state["issues"] = issues
                    reflection = self._call_json("reflector", reflect_state)
                else:
                    # The previous iteration's issues are resolved. Keeping
                    # them would make a closed run look like it still failed.
                    issues = []

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
                    payload = {
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
                    }
                    if parent_ticket_id:
                        # A resumed loop keeps the ticket it already has.
                        # Opening a second one for the same problem would
                        # leave two tickets in the human queue describing
                        # one single escalation - the queue then lies about
                        # how many problems are open.
                        ticket_id = parent_ticket_id
                        self.tickets.append_attempt(
                            parent_ticket_id,
                            payload,
                            reason=(
                                "resume did not close the loop: status="
                                + str(status)
                                + (f", {error}" if error else "")
                                + (f", issues: {'; '.join(issues)}" if issues else "")
                            ),
                        )
                    else:
                        ticket = self.tickets.create("loop_escalation", payload)
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

    # ------------------------------------------------- parallel candidates
    def _produce_candidates(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Produce the artifact for this iteration.

        With ``parallel_workers == 1`` this is exactly the v0.1 path.
        With more workers, several candidates are produced in parallel with
        different angles, each is machine-checked, and the best one wins:

        1. a candidate that passes every machine check wins outright;
        2. otherwise the candidate that passes the most checks wins;
        3. ties are broken by the longest artifact, then by the lowest index.

        The winner is deterministic, so a replay of the same run picks the
        same artifact.
        """
        count = self.parallel_workers
        if count <= 1:
            work = self._call_text("worker", state)
            artifact = strip_reasoning(work["text"])
            return {
                "artifact": artifact,
                "tokens": int(work["usage"].get("total_tokens", 0)),
                "candidates": [{"angle": "direct", "selected": True, "passed": None}],
                "errors": [],
            }

        angles = pick_angles(count, self.perspective)
        results = run_parallel(
            [self._make_candidate_task(state, angle) for angle in angles]
        )
        crits = [Criterion.from_raw(raw, i) for i, raw in enumerate(state.get("criteria") or [])]
        best_index = -1
        best_rank = (-1, -1, -1)
        errors: List[str] = []
        tokens = 0
        summary: List[Dict[str, Any]] = []
        for index, item in enumerate(results):
            if isinstance(item, ParallelFailure):
                errors.append(f"{angles[index]}: {item.message}")
                summary.append({"angle": angles[index], "selected": False, "error": item.message})
                continue
            artifact = item["text"]
            tokens += int(item["usage"].get("total_tokens", 0))
            passed_count = 0
            for crit in crits:
                outcome = evaluate_check(crit, artifact)
                if outcome.get("pass"):
                    passed_count += 1
            summary.append(
                {
                    "angle": angles[index],
                    "selected": False,
                    "passed": passed_count,
                    "chars": len(artifact),
                    "sha": short_sha(artifact),
                }
            )
            rank = (passed_count, len(artifact), -index)
            if rank > best_rank:
                best_rank = rank
                best_index = index

        if best_index < 0:
            # Every candidate failed - keep the first error so the caller
            # reports a provider problem rather than an empty artifact.
            raise ProviderError(
                "all parallel candidates failed: " + ("; ".join(errors) or "no candidates produced")
            )

        summary[best_index]["selected"] = True
        self._log(
            state["run_id"], state["task_id"], state["attempt"], "candidates",
            {"count": count, "selected_angle": angles[best_index], "candidates": summary},
        )
        return {
            "artifact": results[best_index]["text"],
            "tokens": tokens,
            "candidates": summary,
            "errors": errors,
        }

    def _make_candidate_task(self, state: Dict[str, Any], angle: str) -> Any:
        def task(index: int) -> Dict[str, Any]:
            worker_state = dict(state)
            worker_state["angle"] = angle
            worker_state["angle_instruction"] = angle_instruction(angle)
            return self._call_text("worker", worker_state, extra=angle_instruction(angle))

        return task

    # ------------------------------------------------------ adversarial review
    def _review(self, verify_state: Dict[str, Any]) -> Dict[str, Any]:
        """Run the verifier (or several verifiers) over the candidate."""
        if self.reviewers <= 1:
            return self._call_json("verifier", verify_state)

        personas = pick_personas(self.reviewers)
        results = run_parallel(
            [self._make_reviewer_task(verify_state, persona) for persona in personas]
        )
        reviews: List[Dict[str, Any]] = []
        errors: List[str] = []
        tokens = 0
        for index, item in enumerate(results):
            if isinstance(item, ParallelFailure):
                errors.append(f"{personas[index]}: {item.message}")
                continue
            reviews.append(item["verdict"])
            tokens += int(item["verdict"].get("_usage", {}).get("total_tokens", 0))
        aggregate = aggregate_reviews(reviews, mode=self.review_mode)
        aggregate["_usage"] = {"total_tokens": tokens}
        aggregate["errors"] = errors
        aggregate["personas"] = personas
        self._log(
            verify_state["run_id"], verify_state["task_id"], verify_state["attempt"],
            "review",
            {
                "mode": self.review_mode,
                "personas": personas,
                "usable": aggregate.get("usable"),
                "skipped": aggregate.get("skipped"),
                "errors": errors,
                "scores": aggregate.get("scores"),
            },
        )
        return aggregate

    @staticmethod
    def _all_judged(scores: List[Dict[str, Any]]) -> bool:
        """True when no criterion is left without a verdict."""
        return all(s.get("source") != "model-missing" for s in scores)

    def _ask_missing_verdicts(
        self,
        verify_state: Dict[str, Any],
        crits: List[Criterion],
        checks: List[Dict[str, Any]],
        verdict: Dict[str, Any],
        scores: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Re-ask the verifier for the criteria it did not judge.

        The artifact is unchanged, so this is a cheap targeted question
        rather than a new attempt. The returned verdict keeps the answers
        the verifier already gave and only adds the missing ones.
        """
        missing = [
            score["id"] for score in scores
            if score.get("source") == "model-missing" and score.get("id")
        ]
        if not missing:
            return {}

        by_id = {str(c.id): c for c in crits}
        follow_state = dict(verify_state)
        follow_state["missing_criteria"] = [
            {
                "id": cid,
                "desc": getattr(by_id.get(cid), "desc", ""),
            }
            for cid in missing
        ]
        extra = self._call_json(
            "verifier", follow_state, extra=self._missing_criteria_instruction(missing)
        )
        # Keep the verdicts that already arrived; only the new ones count.
        merged_scores = list(verdict.get("scores") or [])
        known = {str(item.get("id")) for item in merged_scores if isinstance(item, dict)}
        for item in extra.get("scores") or []:
            if isinstance(item, dict) and item.get("id") and str(item["id"]) not in known:
                merged_scores.append(item)
        merged = dict(verdict)
        merged["scores"] = merged_scores
        merged["_usage"] = extra.get("_usage", {})
        return merged

    @staticmethod
    def _missing_criteria_instruction(missing: List[str]) -> str:
        ids = ", ".join(missing)
        return (
            "You did not return a verdict for these acceptance criteria: "
            f"{ids}. Judge the artifact under review against each of them and "
            "answer with the same JSON shape. Include one score entry per "
            "missing id - do not omit any of them and do not restate the "
            "criteria you already judged."
        )

    def _make_reviewer_task(self, verify_state: Dict[str, Any], persona: str) -> Any:
        def task(index: int) -> Dict[str, Any]:
            state = dict(verify_state)
            state["persona"] = persona
            state["persona_instruction"] = persona_instruction(persona)
            verdict = self._call_json("verifier", state, extra=persona_instruction(persona))
            return {"verdict": verdict}

        return task

    @staticmethod
    def _merge_review_scores(
        crits: List[Criterion],
        checks: List[Dict[str, Any]],
        verdict: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Merge machine checks with the aggregated adversarial review.

        Machine checks still win: a criterion with a rule is decided by the
        rule. Only criteria without a machine check are decided by the
        reviewer consensus.
        """
        return LoopEngine._merge_verdict_scores(crits, checks, [verdict], with_votes=True)


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
            "provider_chain": (self.provider.describe() or {}).get("chain", [])
            if hasattr(self.provider, "describe")
            else [],
            "ts": now_iso(),
        }
        entry.update(payload)
        self.ledger.append(entry)

    def _call_text(self, role: str, state: Dict[str, Any], extra: str = "") -> Dict[str, Any]:
        return self.provider.complete(
            system=self.roles.system(role),
            prompt=self.roles.prompt(role, state, extra=extra),
            role=role,
            expect_json=False,
        )

    def _call_json(self, role: str, state: Dict[str, Any], extra: str = "") -> Dict[str, Any]:
        response = self.provider.complete(
            system=self.roles.system(role),
            prompt=self.roles.prompt(role, state, extra=extra),
            role=role,
            expect_json=True,
        )
        data = parse_json_loose(response["text"]) or {}
        if role == "verifier":
            # A model that answers with its own JSON shape must not silently
            # lose its verdict - normalize it into the canonical schema.
            data = normalize_verdict(data)
        data["_usage"] = response.get("usage", {})
        data["_raw"] = response.get("text", "")
        return data

    @staticmethod
    def _merge_verdict_scores(
        crits: List[Criterion],
        checks: List[Dict[str, Any]],
        verdicts: List[Dict[str, Any]],
        *,
        with_votes: bool = False,
    ) -> List[Dict[str, Any]]:
        """Combine machine checks with the verdicts of one or more verifiers.

        A criterion that carries a machine check is decided by that check -
        the model never gets to override it. A criterion without a machine
        check is decided by the verifier. If no verifier judged it, the
        criterion stays unjudged and is reported as such; it is never
        silently passed.
        """
        model_scores: Dict[str, Dict[str, Any]] = {}
        for verdict in verdicts:
            for item in verdict.get("scores") or []:
                if isinstance(item, dict) and item.get("id"):
                    model_scores.setdefault(str(item["id"]), item)
        merged: List[Dict[str, Any]] = []
        # ``checks`` is built one-per-criterion, so the two lists are the
        # same length. ``strict=True`` makes a mismatch loud instead of
        # silently dropping the trailing criteria.
        for criterion, check in zip(crits, checks, strict=True):
            if not criterion.required:
                # An optional criterion never blocks the closure, but its
                # result is still worth recording. Discarding it and
                # reporting "pass" would mean the report shows a green
                # light for a rule that actually failed.
                if check.get("pass") is not None:
                    merged.append(
                        {
                            "id": criterion.id,
                            "pass": bool(check["pass"]),
                            "note": str(check.get("note", "")),
                            "source": "rule",
                            "optional": True,
                            "blocking": False,
                        }
                    )
                    continue
                if check.get("source") == "invalid":
                    # A broken rule on an optional criterion is still a
                    # broken rule - it is recorded as such and stays
                    # non-blocking.
                    merged.append(
                        {
                            "id": criterion.id,
                            "pass": False,
                            "note": str(check.get("note", "")),
                            "source": "invalid",
                            "optional": True,
                            "blocking": False,
                        }
                    )
                    continue
                # No machine rule: the reviewer's judgement is recorded so
                # the report shows what the optional standard actually
                # looked like. An unreadable answer stays "skipped" rather
                # than "unjudged" - an optional criterion must never send
                # the loop back to the verifier for a second opinion.
                review = model_scores.get(criterion.id)
                decided = _as_bool(review.get("pass")) if review else None
                if decided is None:
                    merged.append(
                        {
                            "id": criterion.id,
                            "pass": True,
                            "note": "optional criterion",
                            "source": "skipped",
                            "optional": True,
                            "blocking": False,
                        }
                    )
                else:
                    merged.append(
                        {
                            "id": criterion.id,
                            "pass": decided,
                            "note": str(review.get("note", "")),
                            "source": review.get("source", "model"),
                            "optional": True,
                            "blocking": False,
                        }
                    )
                continue
            if check.get("pass") is not None:
                merged.append(
                    {
                        "id": criterion.id,
                        "pass": bool(check["pass"]),
                        "note": str(check.get("note", "")),
                        "source": "rule",
                        "blocking": True,
                    }
                )
                continue
            if check.get("source") == "invalid":
                # The criterion carries a machine check, but the check could
                # not decide anything (a missing argument, an unknown type).
                # The model never gets to override a rule - and a rule that
                # cannot decide is never a pass.
                merged.append(
                    {
                        "id": criterion.id,
                        "pass": False,
                        "note": str(check.get("note", "")),
                        "source": "invalid",
                        "blocking": True,
                    }
                )
                continue
            review = model_scores.get(criterion.id)
            if review is None:
                merged.append(
                    {
                        "id": criterion.id,
                        "pass": False,
                        "note": "unjudged: 评审没有给出该标准的判定",
                        "source": "model-missing",
                        "blocking": True,
                    }
                )
            else:
                # Never trust a bare bool() here. A model that answers
                # ``"pass": "false"`` would make bool("false") True, and a
                # criterion that was never satisfied would close the loop.
                # _as_bool reads the actual meaning of the string; an
                # unreadable answer stays unjudged instead of guessing.
                decided = _as_bool(review.get("pass"))
                if decided is None:
                    merged.append(
                        {
                            "id": criterion.id,
                            "pass": False,
                            "note": "unjudged: 评审给的判定无法识别（"
                                    + str(review.get("pass")) + "）",
                            "source": "model-missing",
                            "blocking": True,
                        }
                    )
                    continue
                entry = {
                    "id": criterion.id,
                    "pass": decided,
                    "note": str(review.get("note", "")),
                    "source": review.get("source", "model"),
                    "blocking": True,
                }
                if with_votes:
                    entry["votes"] = review.get("votes", "")
                merged.append(entry)
        return merged

    @staticmethod
    def _merge_scores(
        crits: List[Criterion],
        checks: List[Dict[str, Any]],
        verdict: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        return LoopEngine._merge_verdict_scores(crits, checks, [verdict])


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
            parent_ticket_id=ticket_id,
        )
        # A resumed loop that still cannot close must NOT look finished.
        # Marking the ticket resolved anyway would empty the human queue:
        # the escalation would disappear from `mas tickets` while the very
        # problem it was opened for is still open. The ticket stays
        # waiting_human in that case, and it carries the new failure.
        if result.closed:
            if ticket.get("state") != "resolved":
                self.tickets.resolve(ticket_id, "resumed", note)
        else:
            self.tickets.reopen(
                ticket_id,
                note,
                reason=(
                    f"resume did not close the loop: status={result.status}"
                    + (f", {result.error}" if result.error else "")
                    + (f", issues: {'; '.join(result.issues)}" if result.issues else "")
                ),
                result=result.to_dict(),
            )
        return result

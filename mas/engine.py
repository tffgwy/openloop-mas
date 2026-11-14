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

from mas.ceiling import call_ceiling
from mas.filechecks import (
    FILE_CHECK_TYPES,
    FileCheckError,
    evaluate_file_check,
    file_check_problem,
    parse_file_check_arg,
    workspace_root,
)
from mas.filewrite import apply_write_blocks, plan_writes, write_instruction
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
from mas.providers import (
    BudgetExceeded,
    Provider,
    ProviderError,
    TruncatedAnswer,
    build_provider_chain,
)
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
    # Whether the deliverable itself passed every machine check. This is
    # not the same question as "did the run close": a run can produce a
    # perfect deliverable and still not close, because it broke its
    # wall-clock budget or its token budget. Reporting those two
    # situations with the same word - "not closed" - hides from the user
    # which half of the system failed.
    deliverable_ok: bool = False

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
            "deliverable_ok": self.deliverable_ok,
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
        if kind in FILE_CHECK_TYPES:
            # A file check has its own argument shape (a path plus a
            # value), so it is validated by its own rule set. The same
            # principle applies: a check that cannot decide anything must
            # stop the run before it burns the budget.
            problem = file_check_problem(kind, criterion.check.get("arg"))
            if problem:
                problems.append(f"{criterion.id}: {problem}")
            continue
        if kind not in _ARG_REQUIRED:
            # An unknown type is a typo in the type name, not a missing
            # argument. The checker refuses to decide it, so the run must
            # not start either - otherwise the criterion would be handed
            # to the model and could be closed by a guess.
            known = ", ".join(sorted(_ARG_REQUIRED) + sorted(FILE_CHECK_TYPES))
            problems.append(
                f"{criterion.id}: unknown check type: {kind!r} "
                f"(known types: {known})"
            )
            continue
        problem = _check_arg_problem(kind, criterion.check.get("arg"))
        if problem:
            problems.append(f"{criterion.id}: {problem}")
    return problems


def evaluate_check(
    criterion: Criterion,
    artifact: str,
    config: Optional[Dict[str, Any]] = None,
    overlay: Optional[Dict[str, bytes]] = None,
) -> Dict[str, Any]:
    """Evaluate the machine check of a criterion against an artifact.

    ``config`` is optional and only used by the file checks, which
    resolve their paths against the configured workspace. When it is
    omitted the current working directory is used, so the existing
    call sites keep working unchanged.

    ``overlay`` maps absolute paths to the bytes a candidate *would*
    write there. The parallel candidate ranking passes it so a candidate
    is scored against the file it would actually produce - see
    ``mas.filewrite.plan_writes``.
    """
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
        elif kind in FILE_CHECK_TYPES:
            # A file check reads the real filesystem instead of the
            # artifact string. It is evaluated here - by this module - so
            # the verdict is reproducible and does not depend on the
            # model's willingness to describe what it "did".
            outcome = evaluate_file_check(kind, arg, workspace_root(config), overlay)
            outcome["id"] = criterion.id
            return outcome
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
        # The wall-clock budget of this run, remembered so that every
        # provider call can be given the *remaining* time as its own
        # timeout. See ``_remaining_budget_sec``.
        self._deadline_sec = deadline_sec
        self._run_started_at = start
        tokens = 0
        attempt = 0
        issues: List[str] = []
        artifact = ""
        scores: List[Dict[str, Any]] = []
        history: List[Dict[str, Any]] = []
        ticket_id: Optional[str] = None
        status = STATUS_BUDGET
        error = ""
        # Whether the deliverable itself passed every machine check.
        # A run can produce a perfect deliverable and still not close
        # because it broke its budget - the two facts have to stay
        # separate, or the report hides which half of the system failed.
        deliverable_ok = False
        # ``error`` describes *this* run's outcome. ``last_error`` keeps
        # the last transient failure - a truncation that was retried, a
        # provider that the fallback chain recovered from - so an
        # escalation can still say why the run was hard, without a
        # recovered run reporting a failure that no longer applies.
        last_error = ""
        stagnant = 0
        last_sha = ""
        plan_payload: Dict[str, Any] = {}
        # The decision of the last scoring round. A run that never
        # reached a scoring round - it hit the budget ceiling on its
        # first iteration - still has to be described in the ledger, so
        # the value starts out empty instead of being undefined.
        decision = ""

        while True:
            if attempt >= max_iterations:
                status = STATUS_BUDGET
                error = f"budget: reached max_iterations={max_iterations}"
                break
            if (self.clock() - start) > deadline_sec:
                status = STATUS_BUDGET
                error = f"budget: exceeded deadline_sec={deadline_sec:.0f}"
                break
            if tokens >= max_tokens:
                # The token ceiling is checked here as well as after the
                # scoring step. A truncated answer is retried with a
                # bigger output budget, and that retry runs *before* the
                # end-of-iteration check - so a run that kept hitting
                # the output limit could spend far past the ceiling
                # the user set. The ceiling has to stop the loop at
                # the top too, or it is not a ceiling.
                status = STATUS_BUDGET
                error = f"budget: tokens {tokens} >= {max_tokens}"
                break

            attempt += 1
            # A transient failure of the previous iteration does not
            # describe this one. The error is cleared here so a run that
            # recovers does not report the failure it already survived.
            error = ""
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
            # A criterion that demands a file creates an obligation the
            # worker has to know about: without this the worker only ever
            # *talks* about writing the file, and the file check - which
            # is right - then fails forever.
            write_paths = self._required_write_paths(crits)
            if write_paths:
                state["write_paths"] = write_paths
                state["write_instruction"] = write_instruction(write_paths)

            try:
                if attempt == 1:
                    plan_payload = self._call_json("planner", state)
                    # The planner really spent tokens. A call that
                    # produced no artifact is still a paid call, so it
                    # belongs to the budget.
                    plan_usage = dict(plan_payload.get("_usage") or {})
                    tokens += int(plan_usage.get("total_tokens", 0) or 0)
                    # The internal bookkeeping never leaves the engine.
                    # The plan goes into the state of every later
                    # prompt, so the raw provider text would ride along
                    # with it and pollute the worker's brief.
                    plan_payload = {
                        key: value
                        for key, value in plan_payload.items()
                        if key not in ("_usage", "_raw")
                    }
                    # The ledger still shows what the planner call cost -
                    # the audit trail has to carry the spend of every
                    # call, not only the ones that produced an artifact.
                    self._log(
                        run_id, task_id, attempt, "planner",
                        {
                            "plan": plan_payload,
                            "usage": plan_usage,
                            # The charge of this call. Every token the
                            # endpoint bills has to show up in the ledger
                            # exactly once, otherwise the book cannot be
                            # reconciled with the bill.
                            "tokens": int(plan_usage.get("total_tokens", 0) or 0),
                        },
                    )

                candidates = self._produce_candidates(state)
                artifact = candidates["artifact"]
                tokens += candidates["tokens"]

                checks = [evaluate_check(c, artifact, self.config) for c in crits]
                verify_state = dict(state)
                verify_state["candidate_artifact"] = artifact
                verify_state["checks"] = checks
                verdict = self._review(verify_state)
                review_tokens = int(
                    verdict.get("_usage", {}).get("total_tokens", 0) or 0
                )
                tokens += review_tokens
                # The first review round is booked right here. The final
                # panel is written further down, after the follow-up
                # question - and a follow-up that dies (a truncated
                # reviewer) never reaches that point. Booking the round
                # here means the money is in the book even when the
                # iteration ends in a retry.
                self._log_review(verify_state, verdict, review_tokens)
                if self.reviewers > 1:
                    scores = self._merge_review_scores(crits, checks, verdict)
                else:
                    scores = self._merge_scores(crits, checks, verdict)

                # A verifier that stayed silent about a criterion is not a
                # failure of the artifact - it is a missing answer. Asking
                # the verifier again for the missing ids costs one cheap
                # call; regenerating the whole artifact costs a whole
                # iteration and usually changes nothing.
                reasked = False
                if not self._all_judged(scores):
                    missing_ids = [
                        score["id"]
                        for score in scores
                        if score.get("source") == "model-missing" and score.get("id")
                    ]
                    extra = self._ask_missing_verdicts(
                        verify_state, crits, checks, verdict, scores
                    )
                    if extra:
                        reask_tokens = int(
                            extra.get("_usage", {}).get("total_tokens", 0) or 0
                        )
                        tokens += reask_tokens
                        if not extra.get("_charge_logged"):
                            # The follow-up question is a paid call and it
                            # has its own entry: the money must be
                            # traceable to the question that asked for it.
                            self._log(
                                run_id, task_id, attempt, "reask",
                                {
                                    "missing": missing_ids,
                                    "tokens": reask_tokens,
                                },
                            )
                        reasked = True
                        if self.reviewers > 1:
                            scores = self._merge_review_scores(crits, checks, extra)
                        else:
                            scores = self._merge_scores(crits, checks, extra)
                        # The re-ask answered only the missing criteria.
                        # The other criteria still carry the panel's own
                        # verdicts, so the aggregate is rebuilt from the
                        # merged panel - not from the re-ask alone.
                        # The ledger must describe the panel the engine
                        # decided on. The single reviewer path used to
                        # keep the *old* verdict here, so the book showed
                        # a review that had judged nothing next to a run
                        # that had actually closed on the follow-up
                        # answer. Both paths carry the re-ask forward now.
                        verdict = extra

                # After a follow-up question the first review entry no
                # longer describes what the engine decided on - it shows
                # the panel *before* the missing criteria were answered.
                # The final panel is written here, and it carries no
                # charge of its own: the two rounds are booked
                # separately, so a token is never counted twice.
                if reasked:
                    self._log_review(verify_state, verdict, 0)

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
                    # The reflection cost tokens as well, and the raw
                    # provider text has no business in the history.
                    reflect_usage = dict(reflection.get("_usage") or {})
                    reflect_tokens = int(reflect_usage.get("total_tokens", 0) or 0)
                    tokens += reflect_tokens
                    # The reflector is a paid call and the ledger never
                    # mentioned it: the book showed a run that revised
                    # itself again and again and never said what the
                    # reflection cost. It is booked here.
                    self._log(
                        run_id, task_id, attempt, "reflector",
                        {
                            "reflection": {
                                key: value
                                for key, value in reflection.items()
                                if key not in ("_usage", "_raw")
                            },
                            "tokens": reflect_tokens,
                        },
                    )
                    reflection = {
                        key: value
                        for key, value in reflection.items()
                        if key not in ("_usage", "_raw")
                    }
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
                    # The escalation has to name its cause. A ticket that
                    # only says "the loop could not close itself" sends
                    # the human looking for the wrong thing: this run
                    # stopped because the artifact stopped changing -
                    # the worker kept producing the same bytes while the
                    # checks kept failing. That is a different problem
                    # (and a different fix) than a run that simply ran
                    # out of budget.
                    error = (
                        f"stagnation: the deliverable did not change for "
                        f"{stagnant} consecutive attempts "
                        f"(stagnation_limit={stagnation_limit})"
                    )
                if tokens >= max_tokens:
                    decision = "escalate"
                    error = f"budget: tokens {tokens} >= {max_tokens}"
                # The wall-clock deadline is a ceiling, so it is checked
                # at the moment the closure is decided - not only at the
                # top of the loop. A run whose last call overran the
                # deadline used to close anyway and report success: the
                # measured case took 185s against a 180s deadline. The
                # deliverable was fine, the run was not: it broke the
                # budget the user set, and the ledger has to say so.
                if decision == "close":
                    remaining = self._remaining_budget_sec()
                    if remaining is not None and remaining <= 0:
                        decision = "escalate"
                        error = (
                            f"budget: exceeded deadline_sec={deadline_sec:.0f} "
                            "(the deliverable passed the checks, but the run "
                            "overran its wall-clock budget)"
                        )

                deliverable_ok = bool(passed)
                closure = {
                    "artifact_sha": art_sha,
                    "verdict": "pass" if passed else "fail",
                    "decision": decision,
                    "scores": scores,
                    "issues": issues,
                    "stagnation": stagnant,
                    "closed": bool(passed),
                    # The cumulative spend of the run at this point. This
                    # is a checkpoint, not a charge: the charges live on
                    # the call entries, and this number has to equal
                    # their sum. The ledger has to answer "how much did
                    # this run spend" on its own - the figure only lived
                    # in the engine's memory, so the replay report could
                    # never show it.
                    "tokens_total": int(tokens),
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

                # A run that overran its wall-clock budget is not a
                # closed run, even when the deliverable passed every
                # check. The check ordering matters: "passed" used to
                # break out of the loop unconditionally, so a run that
                # broke its budget was reported as a success and the
                # budget the user set was never a budget at all.
                if passed and decision != "escalate":
                    status = STATUS_CLOSED
                    # The run closed. Any error left over from an earlier
                    # iteration - a truncation that was retried, a failed
                    # provider that the fallback chain recovered from -
                    # describes a moment that has been resolved. Carrying
                    # it made a successful run report "truncated answer"
                    # as its explanation, which reads as a failed run.
                    error = ""
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
                        "reason": error or last_error or "loop could not close itself",
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
            except TruncatedAnswer as exc:
                # The model ran out of output tokens. The answer is
                # incomplete, so it must not be judged - but it is not a
                # provider failure either: the next attempt gets a bigger
                # output budget. The iteration is retried, and the ledger
                # records why.
                error = f"truncated answer: {exc}"
                last_error = error
                # The truncated call really did spend tokens - the
                # endpoint generated them before it ran out of room. They
                # are charged to the budget, so the ledger does not
                # under-report the spend of a run that had to retry.
                spent = int(getattr(exc, "tokens", 0) or 0)
                tokens += spent
                self._log(
                    run_id, task_id, attempt, "truncated",
                    {"error": error, "retry": True, "tokens": spent},
                )
                # The partial answer is discarded on purpose: a fragment
                # that happens to satisfy a check must not become the
                # deliverable.
                artifact = ""
                last_sha = ""
                continue
            except BudgetExceeded as exc:
                # The wall-clock budget ran out *inside* a provider call.
                # This is a stop-loss mechanism firing on purpose, not a
                # broken backend: the run ends as budget_exhausted and
                # says so, instead of reporting a provider error for a
                # run that was stopped exactly as the user asked.
                error = f"budget: {exc}"
                last_error = error
                spent = int(getattr(exc, "tokens", 0) or 0)
                tokens += spent
                self._log(
                    run_id, task_id, attempt, "error",
                    {"error": error, "kind": "budget", "tokens": spent},
                )
                status = STATUS_BUDGET
                break
            except ProviderError as exc:
                error = f"provider error: {exc}"
                last_error = error
                # The failed call was billed too. A provider that
                # answered and then returned a broken shape - or a
                # chain that fell over from one backend to the next -
                # spent real tokens, and a run that reports a spend of
                # zero for a call that cost money is a run whose budget
                # was never a budget.
                spent = int(getattr(exc, "tokens", 0) or 0)
                tokens += spent
                self._log(
                    run_id, task_id, attempt, "error",
                    {"error": error, "tokens": spent},
                )
                status = STATUS_ERROR
                break
            except Exception as exc:  # noqa: BLE001 - the run must stay auditable
                # Something that is not a provider error went wrong inside
                # the loop - a broken role contract, a malformed answer, a
                # bug in a check. Letting it escape would end the run
                # *without a ledger entry*: the ledger would show a run
                # that started and never ended, and the closure state
                # could not be audited. The CLI, the MCP tool and the HTTP
                # route each have their own boundary, but the engine is
                # also a library entry point, so the loop closes its own
                # books before the error leaves.
                error = f"internal error: {type(exc).__name__}: {exc}"
                last_error = error
                self._log(
                    run_id, task_id, attempt, "error",
                    {"error": error, "kind": "internal"},
                )
                status = STATUS_ERROR
                break

        # The ledger has to say how the run ended. Without this entry the
        # report could only *guess* the status from the closure rows: a
        # run that stopped on the budget ceiling looked like a run that
        # simply had not closed yet, and a run that died on a provider
        # error looked like a run that was still going. The replay could
        # not tell "stopped", "failed" and "unfinished" apart - and a
        # report that cannot tell them apart cannot explain a run.
        self._log(
            run_id, task_id, attempt, "run_end",
            {
                "status": status,
                "decision": decision,
                "error": error,
                "iterations": attempt,
                "closed": status == STATUS_CLOSED,
                "deliverable_ok": deliverable_ok,
                "artifact_sha": sha256_text(artifact) if artifact else "",
                "issues": list(issues),
                "tokens_total": int(tokens),
            },
        )

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
            deliverable_ok=deliverable_ok,
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
            written: List[Dict[str, Any]] = []
            write_errors: List[str] = []
            if state.get("write_paths"):
                applied = apply_write_blocks(artifact, self._write_workspace())
                artifact = applied["artifact"]
                written = applied["written"]
                write_errors = applied["errors"]
                if written:
                    self._log(
                        state["run_id"], state["task_id"], state["attempt"], "write",
                        {"written": written},
                    )
                if write_errors:
                    self._log(
                        state["run_id"], state["task_id"], state["attempt"], "write",
                        {"errors": write_errors},
                    )
            work_tokens = int(work["usage"].get("total_tokens", 0))
            # The single worker path wrote no ledger entry at all, so the
            # money it spent was nowhere in the book. A run whose ledger
            # cannot be reconciled with its bill is not auditable - the
            # charge of the call that produced the deliverable is booked
            # here, exactly like the parallel pool books its own.
            self._log(
                state["run_id"], state["task_id"], state["attempt"], "candidates",
                {
                    "count": 1,
                    "selected_angle": "direct",
                    # The single worker path produces one candidate, the
                    # parallel path produces several. The role is the
                    # same, so the report can show the pool of both.
                    "parallel": False,
                    "candidates": [
                        {"angle": "direct", "selected": True, "passed": None}
                    ],
                    "tokens": work_tokens,
                },
            )
            return {
                "artifact": artifact,
                "tokens": work_tokens,
                "candidates": [{"angle": "direct", "selected": True, "passed": None}],
                "errors": write_errors,
                "written": written,
            }

        angles = pick_angles(count, self.perspective)
        results = run_parallel(
            [self._make_candidate_task(state, angle) for angle in angles]
        )
        # A truncation anywhere in the pool invalidates the whole pool.
        # The tokens of *every* truncated candidate are collected here, not
        # just the one that is re-raised: the other candidates ran too and
        # really did spend their budget. Counting only the re-raised one
        # under-reported the spend of a parallel run.
        truncated_tokens = 0
        pool_tokens = 0
        failed_tokens = 0
        truncation: Optional[TruncatedAnswer] = None
        for item in results:
            if isinstance(item, ParallelFailure):
                if isinstance(item.error, TruncatedAnswer):
                    truncated_tokens += int(getattr(item.error, "tokens", 0) or 0)
                    if truncation is None:
                        truncation = item.error
                else:
                    # A candidate that failed after the endpoint billed it
                    # still cost money. The amount is collected here, in the
                    # same pre-pass as the truncation, so it does not depend
                    # on the order the pool happened to finish in.
                    failed_tokens += int(
                        getattr(item.error, "tokens", 0) or 0
                    )
                continue
            # The candidates that *did* finish also spent tokens - the
            # endpoint generated them before the pool was thrown away.
            # Only the truncated ones were counted, so a pool that lost
            # the race reported the cost of one candidate instead of the
            # cost of the whole pool, and the token ceiling was
            # systematically too generous.
            pool_tokens += int((item.get("usage") or {}).get("total_tokens", 0) or 0)
        if truncation is not None:
            truncation.usage = dict(truncation.usage or {})
            truncation.usage["total_tokens"] = (
                truncated_tokens + pool_tokens + failed_tokens
            )
            raise truncation
        crits = [Criterion.from_raw(raw, i) for i, raw in enumerate(state.get("criteria") or [])]
        best_index = -1
        best_rank = (-1, -1, -1)
        errors: List[str] = []
        tokens = 0
        summary: List[Dict[str, Any]] = []
        for index, item in enumerate(results):
            if isinstance(item, ParallelFailure):
                # A truncated candidate never reaches this point: the
                # pre-pass above already raised the truncation, so the
                # whole pool is retried with a bigger output budget
                # instead of being ranked as a pool of fragments.
                errors.append(f"{angles[index]}: {item.message}")
                # The spend of this candidate was already collected in the
                # pre-pass above - it is added to the total once, after the
                # ranking, so the accounting does not depend on the order
                # in which the pool happened to finish.
                summary.append({"angle": angles[index], "selected": False, "error": item.message})
                continue
            # The reasoning block is not part of the deliverable. The
            # single worker path has always stripped it; the parallel
            # path did not, so the scratchpad of a reasoning model was
            # delivered as the artifact, counted in the length tie
            # break, and written into files. Stripping it *before* the
            # ranking keeps both paths identical.
            artifact = strip_reasoning(item["text"])
            tokens += int(item["usage"].get("total_tokens", 0))
            # The file level checks read the real disk, but at ranking
            # time this candidate has not written its file yet. Scoring
            # it against the old disk state made every file level check
            # fail for every candidate - the file rules had no influence
            # on the choice at all, and the winner was picked by length.
            # The candidate is therefore scored against the files it
            # *would* write, through an overlay that never touches the
            # disk. Only the winning candidate is written afterwards.
            overlay: Dict[str, bytes] = {}
            if state.get("write_paths"):
                plan = plan_writes(artifact, self._write_workspace())
                for planned in plan["planned"]:
                    overlay[planned["absolute"]] = planned["content"].encode("utf-8")
            passed_count = 0
            for crit in crits:
                # The configuration carries the workspace, and the file
                # checks resolve their paths against it. Without it the
                # check would look at the *current working directory*
                # instead of the workspace the run was configured with -
                # so every file level check would fail here, and the
                # candidate ranking would be decided by the wrong
                # evidence. The winner is picked on this ranking, so a
                # wrong workspace meant the *wrong candidate* won.
                outcome = evaluate_check(crit, artifact, self.config, overlay)
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

        # A candidate that failed in the same pool was billed as well.
        # The winner is what gets delivered, but the money spent on the
        # losers is part of the run's cost - a pool that lost two members
        # did not cost one candidate's worth. It is added once, after the
        # ranking, so the total does not depend on the finishing order.
        tokens += failed_tokens
        if best_index < 0:
            # Every candidate failed - keep the first error so the caller
            # reports a provider problem rather than an empty artifact.
            # The candidates that failed were still billed, so the
            # error carries their combined spend: a pool that lost all
            # of its members cost money, and the run must say so.
            raise ProviderError(
                "all parallel candidates failed: "
                + ("; ".join(errors) or "no candidates produced"),
                usage={"total_tokens": failed_tokens},
            )

        summary[best_index]["selected"] = True
        self._log(
            state["run_id"], state["task_id"], state["attempt"], "candidates",
            {
                "count": count,
                "selected_angle": angles[best_index],
                "parallel": True,
                "candidates": summary,
                # The whole pool was billed - the winner and the
                # candidates that lost the race.
                "tokens": int(tokens),
            },
        )
        artifact = strip_reasoning(results[best_index]["text"])
        written: List[Dict[str, Any]] = []
        if state.get("write_paths"):
            # Only the *winning* candidate is written to disk. Writing
            # every candidate would leave the workspace with several
            # versions of the same file, and the file check would then
            # pass on whichever version happened to be written last -
            # the check would no longer describe the delivered artifact.
            applied = apply_write_blocks(artifact, self._write_workspace())
            artifact = applied["artifact"]
            written = applied["written"]
            errors.extend(applied["errors"])
            if written or applied["errors"]:
                self._log(
                    state["run_id"], state["task_id"], state["attempt"], "write",
                    {"written": written, "errors": applied["errors"]},
                )
        return {
            "artifact": artifact,
            "tokens": tokens,
            "candidates": summary,
            "errors": errors,
            "written": written,
        }

    def _make_candidate_task(self, state: Dict[str, Any], angle: str) -> Any:
        def task(index: int) -> Dict[str, Any]:
            worker_state = dict(state)
            worker_state["angle"] = angle
            worker_state["angle_instruction"] = angle_instruction(angle)
            return self._call_text("worker", worker_state, extra=angle_instruction(angle))

        return task

    # ------------------------------------------------------------ write files
    def _required_write_paths(self, crits: List[Criterion]) -> List[str]:
        """Return the file paths the acceptance criteria demand.

        Only file checks that assert *presence* or *content* create a file
        obligation. ``file_missing`` deliberately does not - it asserts that
        a file is gone.
        """
        paths: List[str] = []
        for crit in crits:
            check = crit.check or {}
            kind = str(check.get("type") or "").lower()
            if kind not in {
                "file_exists",
                "file_contains",
                "file_not_contains",
                "file_regex",
                "file_lines_min",
                "file_min_bytes",
                "file_max_bytes",
                "file_sha256",
                "file_mtime_newer",
            }:
                continue
            try:
                # The path is read with the *same* parser the machine
                # check uses. The engine used to parse the argument on
                # its own, and the two parsers disagreed: a list or
                # tuple argument (``["notes/a.txt"]``) was rendered by
                # the check as ``notes/a.txt`` but by the engine as the
                # literal text ``['notes/a.txt']``. The worker was then
                # told to write a file with brackets in its name, while
                # the check looked at another path - and the loop could
                # never close, no matter how many iterations it ran.
                raw, _value = parse_file_check_arg(check.get("arg"))
            except FileCheckError:
                raw = ""
            if raw and raw not in paths:
                paths.append(raw)
        return paths

    def _write_workspace(self) -> Any:
        return workspace_root(self.config)

    # ------------------------------------------------------ adversarial review
    def _review(self, verify_state: Dict[str, Any]) -> Dict[str, Any]:
        """Run the verifier (or several verifiers) over the candidate."""
        if self.reviewers <= 1:
            # A single reviewer is a panel of one, and it is described
            # like one. The single-reviewer path used to return the raw
            # verdict, so the ledger and the report showed "usable: None,
            # skipped: None, missing: None" - a reader could not tell
            # whether one reviewer had answered, whether it had stayed
            # silent, or whether the review had not run at all. The
            # numbers are the same numbers a panel of one produces, so
            # the two modes can be read the same way.
            verdict = self._call_json("verifier", verify_state)
            return self._single_review_aggregate(verdict)

        personas = pick_personas(self.reviewers)
        results = run_parallel(
            [self._make_reviewer_task(verify_state, persona) for persona in personas]
        )
        # ``panel`` keeps one slot per persona, including the failures.
        # Dropping a failed reviewer would shrink the panel silently and
        # the aggregate would then report "2/2" for a panel of three.
        panel: List[Optional[Dict[str, Any]]] = []
        errors: List[str] = []
        tokens = 0
        truncation: Optional[TruncatedAnswer] = None
        truncated_tokens = 0
        failed_tokens = 0
        for index, item in enumerate(results):
            if isinstance(item, ParallelFailure):
                errors.append(f"{personas[index]}: {item.message}")
                if not isinstance(item.error, TruncatedAnswer):
                    # A reviewer that failed outright was still billed.
                    # The panel continues without it - but the money is
                    # part of the run's cost.
                    failed_tokens += int(
                        getattr(item.error, "tokens", 0) or 0
                    )
                if isinstance(item.error, TruncatedAnswer):
                    # A reviewer that ran out of output room did not
                    # *disagree* with the artifact - it never finished.
                    # Counting it as an absent voter made the strict
                    # panel unclosable, so every iteration burned the
                    # budget until the run escalated for a reason that
                    # was one more retry away. A truncation is reported
                    # as a retryable failure of the review, with a
                    # bigger output budget on the next attempt - the
                    # same treatment the worker path already gets.
                    truncated_tokens += int(getattr(item.error, "tokens", 0) or 0)
                    if truncation is None:
                        truncation = item.error
                panel.append(None)
                continue
            panel.append(item["verdict"])
            tokens += int(item["verdict"].get("_usage", {}).get("total_tokens", 0))
        if truncation is not None:
            # The reviewers that did answer also spent tokens, and this
            # round is thrown away - so they are charged too. The ones
            # that failed outright are charged as well: they were billed
            # before they broke, and the round is lost either way.
            truncation.usage = dict(truncation.usage or {})
            truncation.usage["total_tokens"] = (
                truncated_tokens + tokens + failed_tokens
            )
            raise truncation
        reviews = [verdict for verdict in panel if verdict]
        aggregate = aggregate_reviews(
            reviews, mode=self.review_mode, expected=len(personas)
        )
        aggregate["_usage"] = {"total_tokens": tokens + failed_tokens}
        aggregate["errors"] = errors
        aggregate["personas"] = personas
        # Kept so a targeted re-ask can merge into the same panel instead
        # of replacing it with a single reviewer.
        aggregate["_panel"] = panel
        return aggregate

    def _log_review(
        self,
        verify_state: Dict[str, Any],
        aggregate: Dict[str, Any],
        tokens: int = 0,
    ) -> None:
        """Write the panel state to the ledger.

        The entry shows the aggregate the engine decided on. The charge
        is passed in explicitly: the first review round is booked on its
        own entry, the follow-up question on another one, so a token is
        never counted twice.
        """
        self._log(
            verify_state["run_id"], verify_state["task_id"], verify_state["attempt"],
            "review",
            {
                "mode": self.review_mode,
                "personas": aggregate.get("personas") or [],
                "usable": aggregate.get("usable"),
                "skipped": aggregate.get("skipped"),
                "missing": aggregate.get("missing"),
                "errors": aggregate.get("errors") or [],
                "scores": aggregate.get("scores"),
                # The charge of the round this entry books. Zero for the
                # final state entry - its money was booked with the
                # round that actually made the call.
                "tokens": int(tokens or 0),
            },
        )

    def _single_review_aggregate(self, verdict: Dict[str, Any]) -> Dict[str, Any]:
        """Describe a run of one reviewer the way a panel is described.

        A panel of one is still a panel: it can answer, it can stay
        silent, and it can fail. The aggregate carries the same fields
        as the multi-reviewer one so that the ledger, the report and
        the replay all read the same way in both modes.
        """
        scores = verdict.get("scores")
        answered = bool(scores)
        payload = dict(verdict)
        payload.update(
            {
                "scores": scores if answered else [],
                "usable": 1 if answered else 0,
                # A reviewer that answered but produced nothing to judge
                # was skipped, not absent - the difference matters when
                # somebody reads "the reviewer never showed up".
                "skipped": 0 if answered else 1,
                # The one reviewer was asked and did not answer.
                "missing": 0 if answered else 1,
                "reviewers": 1,
                "mode": self.review_mode,
            }
        )
        return payload

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
        instruction = self._missing_criteria_instruction(missing)

        if self.reviewers > 1:
            # The panel is asked again - all of it, with the same
            # personas. Asking one plain reviewer and calling that the
            # review turned "3/3" into "1/1" and handed the closing
            # decision to a single opinion.
            personas = pick_personas(self.reviewers)
            results = run_parallel(
                [
                    self._make_reviewer_task(follow_state, persona, extra=instruction)
                    for persona in personas
                ]
            )
            reask: List[Optional[Dict[str, Any]]] = []
            tokens = 0
            truncation: Optional[TruncatedAnswer] = None
            truncated_tokens = 0
            failed_tokens = 0
            for item in results:
                if isinstance(item, ParallelFailure):
                    if not isinstance(item.error, TruncatedAnswer):
                        failed_tokens += int(
                            getattr(item.error, "tokens", 0) or 0
                        )
                    if isinstance(item.error, TruncatedAnswer):
                        # The same rule as in the first review round: a
                        # reviewer that ran out of room is retried, not
                        # counted as an absent voter.
                        truncated_tokens += int(getattr(item.error, "tokens", 0) or 0)
                        if truncation is None:
                            truncation = item.error
                    reask.append(None)
                    continue
                reask.append(item["verdict"])
                tokens += int(item["verdict"].get("_usage", {}).get("total_tokens", 0))
            if truncation is not None:
                # Same rule as the first review round: the re-ask that
                # was thrown away is charged in full - the truncated
                # reviewers, the ones that answered, and the ones that
                # failed outright.
                truncation.usage = dict(truncation.usage or {})
                truncation.usage["total_tokens"] = (
                    truncated_tokens + tokens + failed_tokens
                )
                raise truncation

            # Every member keeps its own answers. The re-ask only fills
            # the criteria that member left open - a single reviewer's
            # opinion is never broadcast to the whole panel.
            panel = verdict.get("_panel")
            if not isinstance(panel, list) or not panel:
                panel = [None] * len(personas)
            merged_panel: List[Optional[Dict[str, Any]]] = []
            for index, member in enumerate(panel):
                if member is None:
                    merged_panel.append(None)
                    continue
                member = dict(member)
                scores = [
                    dict(s) for s in (member.get("scores") or []) if isinstance(s, dict)
                ]
                have = {str(s.get("id")) for s in scores}
                answer = reask[index] if index < len(reask) else None
                if answer:
                    for item in answer.get("scores") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("id")
                            and str(item["id"]) not in have
                            and str(item["id"]) in set(missing)
                        ):
                            scores.append(dict(item))
                            have.add(str(item["id"]))
                member["scores"] = scores
                merged_panel.append(member)

            rebuilt = aggregate_reviews(
                [m for m in merged_panel if m],
                mode=self.review_mode,
                expected=len(merged_panel),
            )
            rebuilt["_usage"] = {"total_tokens": tokens + failed_tokens}
            rebuilt["errors"] = verdict.get("errors", [])
            rebuilt["personas"] = verdict.get("personas", personas)
            rebuilt["_panel"] = merged_panel
            return rebuilt

        # A failed follow-up question is not a failed run. The panel
        # path already treats it that way: the reviewer that could not
        # answer simply stays silent, and the criterion keeps its
        # "unjudged" state, which the loop handles like any other
        # unresolved criterion. The single reviewer path used to let
        # the error escape and end the whole run - so the same missing
        # verdict crashed one setup and merely slowed the other down.
        try:
            extra = self._call_json("verifier", follow_state, extra=instruction)
        except (TruncatedAnswer, ProviderError) as exc:
            # The call was billed even though it did not answer, so the
            # failure is charged to the budget and reported.
            spent = int(getattr(exc, "tokens", 0) or 0)
            self._log(
                verify_state["run_id"], verify_state["task_id"],
                verify_state["attempt"], "reask_failed",
                {"error": str(exc), "tokens": spent, "missing": list(missing)},
            )
            merged = dict(verdict)
            merged["_usage"] = {"total_tokens": spent}
            merged["_reask_failed"] = True
            merged["_reask_error"] = str(exc)
            # The failure was already booked by the ``reask_failed``
            # entry above. The caller must not book it a second time.
            merged["_charge_logged"] = True
            return merged
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

    def _make_reviewer_task(
        self,
        verify_state: Dict[str, Any],
        persona: str,
        extra: str = "",
    ) -> Any:
        def task(index: int) -> Dict[str, Any]:
            state = dict(verify_state)
            state["persona"] = persona
            state["persona_instruction"] = persona_instruction(persona)
            verdict = self._call_json(
                "verifier",
                state,
                extra=(extra + " " + persona_instruction(persona)).strip(),
            )
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

    def _output_budget(self, role: str, attempt: int) -> int:
        """How many output tokens a role may use on this attempt.

        The provider default (4000) is small enough that a real deliverable
        gets cut off. A cut off answer is not a failure of the model - it is
        a budget problem, and it is solved by giving the next attempt more
        room. The budget grows with the attempt number, so the loop gets a
        real chance instead of failing the same way twice.
        """
        configured = self.config.get("output_max_tokens")
        base = int(configured) if configured else 8000
        # Every retry doubles the room, capped so a pathological run cannot
        # ask for an unbounded answer.
        grown = base * (2 ** max(0, attempt - 1))
        return min(grown, 32000)

    def _remaining_budget_sec(self) -> Optional[float]:
        """How much wall-clock budget this run has left, if it has one.

        The deadline is one of the four stop-loss mechanisms the project
        promises, so it has to be a *ceiling* and not a value that is
        merely checked between iterations. It used to be checked only at
        the top of the loop: one slow provider call could overrun it by
        minutes and the run still reported success - a measured run took
        185s against a 180s deadline and closed with "预算已用尽" never
        appearing anywhere.

        The remaining time is handed to every provider call as the
        per-call timeout, so a call that starts just before the deadline
        cannot outlive it by another full request timeout.
        """
        deadline = getattr(self, "_deadline_sec", None)
        started = getattr(self, "_run_started_at", None)
        if deadline is None or started is None:
            return None
        remaining = float(deadline) - (self.clock() - float(started))
        if remaining <= 0:
            return 0.0
        return remaining

    def _call_text(self, role: str, state: Dict[str, Any], extra: str = "") -> Dict[str, Any]:
        with call_ceiling(self._remaining_budget_sec()):
            return self.provider.complete(
                system=self.roles.system(role),
                prompt=self.roles.prompt(role, state, extra=extra),
                role=role,
                expect_json=False,
                max_tokens=self._output_budget(role, int(state.get("attempt") or 1)),
            )

    def _call_json(self, role: str, state: Dict[str, Any], extra: str = "") -> Dict[str, Any]:
        with call_ceiling(self._remaining_budget_sec()):
            response = self.provider.complete(
                system=self.roles.system(role),
                prompt=self.roles.prompt(role, state, extra=extra),
                role=role,
                expect_json=True,
                max_tokens=self._output_budget(role, int(state.get("attempt") or 1)),
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
    def _read_review_pass(review: Optional[Dict[str, Any]]) -> Optional[bool]:
        """Read a reviewer's answer, and say so when it cannot be read.

        ``normalize_verdict`` folds an unreadable answer into
        ``pass: False`` and marks it with ``unreadable``. Folding it
        into a plain ``False`` is what makes the two states
        indistinguishable downstream: the engine then believes the
        criterion was *judged and failed*, so it never asks the
        reviewer again - the very thing the re-ask path exists for.

        So the flag is read here. An unreadable answer is no answer:
        the criterion stays unjudged and the engine asks again.
        """
        if not review:
            return None
        if review.get("unreadable"):
            return None
        return _as_bool(review.get("pass"))

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
                            # The source is reported as it was produced:
                            # a file check says "file", a text check says
                            # "rule". Hiding that would make a
                            # disk-verified criterion indistinguishable
                            # from a prose-verified one in the report.
                            "source": str(check.get("source") or "rule"),
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
                decided = LoopEngine._read_review_pass(review)
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
                        "source": str(check.get("source") or "rule"),
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
                decided = LoopEngine._read_review_pass(review)
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

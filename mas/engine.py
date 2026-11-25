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

import contextlib
import json
import pathlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from mas.ceiling import call_ceiling
from mas.criteria_text import criterion_reading
from mas.filechecks import (
    FILE_CHECK_TYPES,
    FileCheckError,
    evaluate_file_check,
    file_check_problem,
    parse_file_check_arg,
    workspace_root,
)
from mas.filewrite import (
    MAX_WRITE_BYTES,
    FileWriteError,
    _request_bytes,
    _safe_target,
    _strip_placeholders,
    apply_write_blocks,
    parse_write_blocks_with_errors,
    plan_writes,
    write_instruction,
)
from mas.handoff import build_deliverable, collect_workspace_files, read_workspace_overlay
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
from mas.workspace import (
    DELIVERABLE_NAME,
    ensure_workspace,
    resolve_workspace,
)

STATUS_CLOSED = "closed"
STATUS_ESCALATED = "escalated"
STATUS_BUDGET = "budget_exhausted"
STATUS_ERROR = "error"

#: The keys the engine keeps for itself.
#:
#: They never leave the engine: the plan and the reflection travel to
#: the model on the next call, and a token count or a backend name is
#: bookkeeping, not work. They are listed in one place so a new
#: internal key cannot leak into a prompt by being added to one call
#: site and forgotten at the other three.
INTERNAL_KEYS = ("_usage", "_raw", "_provider_used", "_model_used")


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
    # Where the finished deliverable was written. Empty when the run
    # produced no artifact at all.
    artifact_path: str = ""
    # The folder the run worked in. Everything the run produced is
    # there: the deliverable and the files it wrote.
    workspace: str = ""
    # The files the run wrote, relative to the workspace.
    files: List[Dict[str, Any]] = field(default_factory=list)
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
    # The budget this run actually ran under. A run that stopped on a
    # ceiling says which one - and the continuation command can then
    # offer a wider one instead of sending the next run into the same
    # wall.
    budget: Dict[str, int] = field(default_factory=dict)

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
            "artifact_path": self.artifact_path,
            "workspace": self.workspace,
            "files": list(self.files),
            "history": self.history,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "deliverable_ok": self.deliverable_ok,
            "budget": dict(self.budget),
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
        # The clock the wall-clock ceiling is measured against.
        #
        # It used to be ``time.time`` - the wall clock. On Windows that
        # clock has a resolution of about 15.6ms on CPython 3.12 and
        # older (CPython 3.13 moved ``time.time`` to a high-resolution
        # timer), and two reads taken microseconds apart came back
        # *equal*. A run with a zero-second budget therefore did not
        # stop at its ceiling: ``clock() - start`` was 0, and the
        # comparison was ``> 0``, so the loop went on, judged
        # something, and closed - on a machine that had no budget at
        # all. The ceiling has to be measured on a clock that can
        # actually see the passage of time.
        #
        # A monotonic clock is the right instrument anyway: a
        # wall-clock ceiling must not be moved by the machine's clock
        # being set backwards mid-run. The value is only ever used as
        # a difference, so its epoch does not matter.
        self.clock = clock or time.monotonic
        # The config the checks resolve their paths against. It is a
        # copy: the run's own folder replaces the caller's directory,
        # but the caller's configuration must not be changed by that.
        self._check_config: Dict[str, Any] = dict(config)
        # The folder this run owns. It is empty until a run id is
        # known, and it is created lazily - a run that never writes
        # anything should not leave an empty folder behind.
        self._workspace: Optional[pathlib.Path] = None
        # The files the run wrote, in the order they were written.
        self._written_files: List[Dict[str, Any]] = []

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

    @classmethod
    def artifacts_dir(cls, config: Dict[str, Any]) -> str:
        """Where the finished deliverables live.

        The deliverable used to exist only in the engine's memory: the
        ledger booked its hash, the report printed its hash, and the
        moment the process ended the work was gone. A closed loop is
        supposed to hand something over, so the artifact is written to
        disk next to the ledger and the path is booked on the run's end
        entry.
        """
        return str(cls.state_root(config) / "artifacts")

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
        previous_artifact: str = "",
        max_iterations: Optional[int] = None,
        deadline_sec: Optional[float] = None,
        max_tokens: Optional[int] = None,
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
        # The workspace is named after the run id, so the engine has to
        # know the id before the first write happens.
        self._run_id = str(run_id)
        self._written_files = []
        self._workspace = None
        # From now on the file level checks look into the run's own
        # folder. Before this point they would have looked into the
        # caller's directory - a place the run does not write to.
        self._check_config = dict(self.config)
        self._check_config["workspace"] = self._run_workspace_path()
        # The folder is created here, before the first check reads the
        # disk: a file check against a folder that does not exist yet
        # would report "not found" for a file the run is about to
        # write, and the loop would never close.
        # A run that cannot make its own folder still has to run -
        # the checks then fall back to the caller's directory.
        with contextlib.suppress(OSError):
            ensure_workspace(self._run_workspace_path())
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
        # The budget of *this* run. A run that takes over a problem it
        # cannot solve with the budget it inherited is a dead end: it
        # stops at exactly the same ceiling and produces exactly the
        # same failure. So the three ceilings can be overridden per
        # run - a resumed run may be given a bigger budget without
        # changing the machine-wide default for every other task.
        #
        # The override is per run and not per engine: the engine is
        # reused, and a ceiling that stuck to it would leak into the
        # next run on the same object.
        if max_iterations is not None:
            max_iterations = max(1, int(max_iterations))
        else:
            max_iterations = int(budget.get("max_iterations", 8))
        if deadline_sec is not None:
            deadline_sec = float(deadline_sec)
        else:
            deadline_sec = float(budget.get("deadline_sec", 900))
        if max_tokens is not None:
            max_tokens = max(1, int(max_tokens))
        else:
            max_tokens = int(budget.get("max_tokens", 200000))
        stagnation_limit = max(1, int(self.config.get("stagnation_limit", 3)))
        # The budget this run actually ran under. The ticket carries it
        # so the human can see *which* ceiling stopped the run - and so
        # the "continue this" command can come with a wider one instead
        # of sending the next run into the same wall.
        effective_budget = {
            "max_iterations": int(max_iterations),
            "deadline_sec": int(deadline_sec),
            "max_tokens": int(max_tokens),
        }

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
        # The status of a stop that happened *inside* the scoring round
        # rather than between rounds. The token ceiling fires there, and
        # it is a budget stop - not the "the loop could not close
        # itself" escalation the status used to claim. Empty means "no
        # such stop happened".
        stop_status = ""
        # Which ceiling stopped this run, in one word.
        #
        # The gap this closes: the run's own end entry said
        # "budget_exhausted" and the error string, but not *which*
        # ceiling fired. The report could say "the budget ran out"
        # without saying whether it was the iteration count, the
        # wall-clock or the token ceiling - and those three need three
        # different reactions (raise the limit, raise the time, raise
        # the money). A reader who cannot tell them apart cannot fix
        # the run.
        #
        # Empty means "the run did not stop on a ceiling" - it
        # closed, or it escalated, or it died on a provider error.
        stopped_by = ""

        while True:
            if attempt >= max_iterations:
                status = STATUS_BUDGET
                error = f"budget: reached max_iterations={max_iterations}"
                stopped_by = "max_iterations"
                break
            # ``>=`` and not ``>``: a run with a zero-second budget has
            # to stop *before* it spends anything. With ``>`` the
            # comparison was 0 > 0 - false - so the ceiling was not a
            # ceiling at all, and the run went on to judge something
            # it was never allowed to work on. A ceiling of zero means
            # zero.
            if (self.clock() - start) >= deadline_sec:
                status = STATUS_BUDGET
                error = f"budget: exceeded deadline_sec={deadline_sec:.0f}"
                stopped_by = "deadline_sec"
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
                stopped_by = "max_tokens"
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
            # What the previous attempt delivered. A resumed loop is a
            # *new* run: the attempt counter starts at one again and
            # the issue list starts empty, so without this the worker
            # would never see the work that was already done and would
            # rewrite the deliverable from scratch, forever. The
            # deliverable of the last attempt is the thing the next
            # attempt has to improve on.
            if previous_artifact:
                state["previous_artifact"] = previous_artifact
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
                    # The backend that answered travels with the plan:
                    # a plan made by the backup after the primary died
                    # is a fact about the run, and the ledger is where
                    # facts about runs live. It is read *before* the
                    # internal keys are stripped, because that is
                    # exactly where it lives.
                    plan_backend = self._backend_fields(plan_payload)
                    # The internal bookkeeping never leaves the engine.
                    # The plan goes into the state of every later
                    # prompt, so the raw provider text would ride along
                    # with it and pollute the worker's brief.
                    plan_payload = {
                        key: value
                        for key, value in plan_payload.items()
                        if key not in INTERNAL_KEYS
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
                            # And whose money it was. A failover folded
                            # the dead endpoint's spend into the total,
                            # and the total used to be booked under the
                            # name of the endpoint that answered.
                            **self._spend_fields(plan_usage),
                            **plan_backend,
                        },
                    )

                candidates = self._produce_candidates(state)
                artifact = candidates["artifact"]
                tokens += candidates["tokens"]

                checks = [
                    evaluate_check(c, artifact, self._check_config, self._draft_overlay())
                    for c in crits
                ]
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
                    reflect_backend = self._backend_fields(reflection)
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
                                if key not in INTERNAL_KEYS
                            },
                            "tokens": reflect_tokens,
                            **self._spend_fields(reflect_usage),
                            **reflect_backend,
                        },
                    )
                    reflection = {
                        key: value
                        for key, value in reflection.items()
                        if key not in INTERNAL_KEYS
                    }
                else:
                    # The previous iteration's issues are resolved. Keeping
                    # them would make a closed run look like it still failed.
                    issues = []

                art_sha = sha256_text(artifact)
                stagnant = stagnant + 1 if art_sha == last_sha else 0
                last_sha = art_sha

                # The lead is consulted when nobody could decide. It is
                # not consulted when the machine checks already did:
                # a failed check is a fact, and a lead that disagrees
                # with a fact does not get to overrule it.
                lead_verdict: Dict[str, Any] = {}
                if not passed and self._lead_is_consulted(scores):
                    lead_verdict = self._consult_lead(
                        verify_state, scores, checks
                    )
                    # The lead is a paid call. Its tokens belong to the
                    # budget like every other call's - a run whose spend
                    # is understated is a run whose budget is a fiction.
                    lead_usage = dict(lead_verdict.get("_usage") or {})
                    tokens += int(
                        lead_usage.get("total_tokens", 0) or 0
                    )

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
                # The lead's judgement fills in the criteria nobody could
                # judge. It can only *unblock* a criterion the machine
                # checks did not speak about - it can never turn a failed
                # machine check into a pass, and it can never overrule the
                # user's budget. The engine keeps the final word.
                if lead_verdict:
                    scores = self._apply_lead_judgement(
                        scores, checks, lead_verdict
                    )
                    blocking = [s for s in scores if s.get("blocking", True)]
                    passed = bool(scores) and all(
                        bool(s["pass"]) for s in blocking
                    )
                    decision = "close" if passed else "revise"

                if tokens >= max_tokens:
                    decision = "escalate"
                    error = f"budget: tokens {tokens} >= {max_tokens}"
                    # The token ceiling is a budget, exactly like the
                    # iteration ceiling and the wall-clock ceiling. It
                    # used to set no status at all, so the escalation
                    # branch stamped ``escalated`` over it: a run that
                    # ran out of tokens was filed as "the loop could
                    # not close itself", and the human queue could not
                    # tell a budget problem from a stuck worker. The
                    # two need different reactions - raise the ceiling
                    # versus change the plan.
                    #
                    # The stop is recorded in its own variable, because
                    # ``status`` is initialised to the budget status
                    # before the loop starts - reading it here would
                    # call every stagnation a budget stop.
                    stop_status = STATUS_BUDGET
                    stopped_by = "max_tokens"
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
                        stopped_by = "deadline_sec"
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
                    # The payload is built by the same helper the other
                    # endings use. The escalation path used to spell the
                    # payload out by hand and it drifted: it left out the
                    # final status, so a ticket opened by a stagnant run
                    # showed a different set of keys than a ticket opened
                    # by a run that ran out of budget. A human reading
                    # the queue then had to know which kind of ticket
                    # they were looking at before they could tell what
                    # happened. One shape, one builder.
                    payload = self._handover_payload(
                        run_id=run_id,
                        task_id=task_id,
                        goal=goal,
                        title=title or goal[:40],
                        crits=crits,
                        artifact=artifact,
                        issues=issues,
                        attempt=attempt,
                        history=history,
                        stagnant=stagnant,
                        # The status of the run, not a hard-coded
                        # "escalated": a run that stopped on the token
                        # ceiling is a budget stop, and its ticket has
                        # to say so. A stagnation - the deliverable
                        # stopped changing - is still the escalation it
                        # has always been.
                        status=(stop_status or STATUS_ESCALATED),
                        reason=error or last_error or "loop could not close itself",
                        budget=effective_budget,
                    )
                    # The status of this run is settled here, before
                    # anything records it, so the reason written on
                    # the ticket and the status stored in its payload
                    # cannot drift apart. A run that stopped on a
                    # ceiling is a budget stop everywhere, not an
                    # escalation with a budget-shaped excuse.
                    status = stop_status or STATUS_ESCALATED
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
                        kind = (
                            "budget_exhausted"
                            if stop_status == STATUS_BUDGET
                            else ("run_error" if stop_status == STATUS_ERROR else "loop_escalation")
                        )
                        ticket = self.tickets.create(kind, payload)
                        ticket_id = ticket["id"]
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
                stopped_by = "deadline_sec"
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
                stopped_by = "provider_error"
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
                stopped_by = "internal_error"
                break

        # The ledger has to say how the run ended. Without this entry the
        # report could only *guess* the status from the closure rows: a
        # run that stopped on the budget ceiling looked like a run that
        # simply had not closed yet, and a run that died on a provider
        # error looked like a run that was still going. The replay could
        # not tell "stopped", "failed" and "unfinished" apart - and a
        # report that cannot tell them apart cannot explain a run.
        # Write the deliverable to disk before the run is booked as
        # ended. A run that says "closed" has to leave something behind
        # that the user can open - a green light with nothing behind it
        # is not a handover.
        artifact_path = self._save_artifact(run_id, artifact)

        # Every run that did not close has to leave something a human
        # can pick up. The escalation path opened a ticket, but the
        # other endings did not: a run that stopped on the iteration
        # ceiling, that ran out of wall-clock time, that ran out of
        # tokens, or that died on a provider error ended with the
        # status "budget_exhausted" or "error" and *no ticket at all*.
        # The status page then said "未闭环 1" and the human queue was
        # empty - the loop reported an unfinished job and offered
        # nobody a way to finish it. Measured on the live model: a run
        # that hit max_iterations left no ticket, so the deliverable
        # existed and nothing pointed at it.
        #
        # A run without a ticket is not a result. Every ending that is
        # not "closed" now opens one, so the queue always matches the
        # report.
        # A resumed run that closes is still part of the thread it
        # came from. Without this the closing run carried no ticket id
        # at all, so the ledger showed a closed run that belonged to
        # no problem and the report could not join the two.
        if status == STATUS_CLOSED and parent_ticket_id and not ticket_id:
            ticket_id = parent_ticket_id
        if status != STATUS_CLOSED and not ticket_id:
            ticket_id = self._open_handover_ticket(
                run_id=run_id,
                task_id=task_id,
                goal=goal,
                title=title or goal[:40],
                crits=crits,
                artifact=artifact,
                issues=issues,
                attempt=attempt,
                history=history,
                stagnant=stagnant,
                status=status,
                error=error or last_error,
                last_error=last_error,
                parent_ticket_id=parent_ticket_id,
                budget=effective_budget,
                workspace=self._run_workspace_path(),
            )

        self._log(
            run_id, task_id, attempt, "run_end",
            {
                "status": status,
                "decision": decision,
                "error": error,
                "iterations": attempt,
                "closed": status == STATUS_CLOSED,
                "deliverable_ok": deliverable_ok,
                # The ticket the escalation opened. Without it the
                # ledger says "escalated" and stops: the human who
                # reads the report has no way to find the ticket that
                # holds the deliverable, the criteria and the history.
                # The last link of the loop - escalate, handle it by
                # hand, resume - was written in the engine and never
                # reached the report.
                "ticket_id": ticket_id or "",
                # The thread this run belongs to. A run that closed on
                # the first try has no thread, and that is correct: it
                # never was anybody else's problem.
                "parent_ticket_id": parent_ticket_id or "",
                # What this run was *about*. The ledger used to record
                # everything about a run except the one thing a human
                # needs: which task produced these results. A replay
                # could say "a run closed, 241 characters, sha
                # fe8848013b" and the reader had no way to learn
                # whether it was the onboarding guide or the Q3 report.
                # A ledger that cannot name its own work is a log of
                # calls, not a record of work.
                "goal": goal,
                "title": title or goal[:40],
                "artifact_sha": sha256_text(artifact) if artifact else "",
                # Where the deliverable was written. Without it the
                # report can only print a hash, and a hash is not
                # something a human can open.
                "artifact_path": artifact_path,
                "artifact_chars": len(artifact),
                # Where the work landed. The deliverable used to be
                # the only thing on disk, and the files it was built
                # from could be anywhere - or already deleted. The
                # folder is booked so a reader can open the run and
                # see everything it produced.
                "workspace": self._run_workspace_path(),
                "files": list(self._written_files),
                # The ceiling this run ran under, and which ceiling
                # stopped it.
                #
                # The gap this closes: the budget was computed in the
                # engine and handed to the ticket, but the run's own
                # end entry never carried it. So the report could say
                # "the budget ran out" and the reader could not learn
                # *what* the ceiling was - while the report said
                # "this round was only allowed 2 rounds" without ever
                # saying where the 2 came from. The run has to be
                # able to name its own limits.
                "budget": dict(effective_budget),
                "stopped_by": stopped_by,
                # What this closure was decided on: the criteria, the
                # bytes of the deliverable, the bytes of every file the
                # run wrote and the per-criterion verdicts. Without it
                # the verdict could only be trusted - not recomputed.
                # And it is sealed only when there *was* a verdict.
                # A run that died on the budget ceiling before it
                # ever judged anything has no conclusion to seal -
                # and a seal for it is a claim about nothing that the
                # report then reads as "the conclusion drifted". The
                # report used to accuse a run that never judged
                # anything of lying. No verdict, no seal.
                "seal": self._seal_for_run(
                    run_id,
                    crits,
                    artifact,
                    scores,
                    "pass" if deliverable_ok else "fail",
                    decision,
                ),
                "issues": list(issues),
                "tokens_total": int(tokens),
                # Every backend that answered this run, and whether the
                # run had to fall over to one of them.
                #
                # The gap this closes: the ledger said "provider:
                # dead-primary" on every row of a run that was actually
                # delivered by the backup, so a reader of the book
                # could not tell that the machine had survived an
                # outage. The run's own end entry is where that fact
                # belongs - it is a fact about the *run*, not about one
                # call.
                **self._run_backends(run_id),
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
            artifact_path=artifact_path,
            workspace=self._run_workspace_path(),
            files=list(self._written_files),
            history=history,
            started_at=started_at,
            finished_at=now_iso(),
            error=error,
            deliverable_ok=deliverable_ok,
            budget=dict(effective_budget),
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
                applied = self._apply_writes(
                    artifact,
                    run_id=str(state.get("run_id") or ""),
                    task_id=str(state.get("task_id") or ""),
                )
                artifact = applied["artifact"]
                written = applied["written"]
                write_errors = applied["errors"]
                if applied.get("drafts"):
                    self._log(
                        state["run_id"], state["task_id"], state["attempt"], "draft",
                        {"drafts": applied["drafts"]},
                    )
                if written:
                    self._record_written(written)
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
                    **self._spend_fields(work.get("usage") or {}),
                    **self._backend_fields(work),
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
                overlay = self._planned_overlay(artifact)
                # A pending draft is a change the loop already proposed
                # for a file that exists. The ranking has to see those
                # bytes too, or the check would judge the old file while
                # the loop is proposing the new one.
                for key, value in self._draft_overlay().items():
                    overlay.setdefault(key, value)
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
                outcome = evaluate_check(
                    crit, artifact, self._check_config, overlay
                )
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
        # Every backend that answered the pool. A candidate pool is
        # served by whichever backend was alive when it ran, so the
        # list can hold more than one name - and the winning
        # candidate's own backend is named separately, because that is
        # the one that produced the deliverable.
        pool_backends = self._backends_of(
            [
                item
                for item in results
                if isinstance(item, dict)
            ]
        )
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
                # And whose money it was. A pool can be served by
                # several backends, and each of them can have burnt
                # tokens on a call that failed before the backup took
                # over - so the pool's spend is spread over the
                # backends that paid for it.
                **self._spend_of_pool(results),
                # The backend that produced the delivered candidate.
                **self._backend_fields(results[best_index]),
                # And every backend the pool was served by.
                "backends": pool_backends,
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
            applied = self._apply_writes(
                artifact,
                run_id=str(state.get("run_id") or ""),
                task_id=str(state.get("task_id") or ""),
            )
            artifact = applied["artifact"]
            written = applied["written"]
            errors.extend(applied["errors"])
            if applied.get("drafts"):
                self._log(
                    state["run_id"], state["task_id"], state["attempt"], "draft",
                    {"drafts": applied["drafts"]},
                )
            if written or applied["errors"]:
                self._record_written(written)
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
                "content_contains",
                "content_not_contains",
                "content_regex",
                "content_lines_min",
                "content_min_chars",
                "content_format_is",
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

    def _planned_overlay(self, artifact: str) -> Dict[str, bytes]:
        """The bytes a candidate *would* write, keyed by absolute path.

        The parallel ranking has to score a candidate against the files
        it would produce - at ranking time nothing has been written yet,
        so scoring against the old disk state made every file level
        check fail for every candidate, and the winner was picked by
        length instead.

        The overlay therefore carries the *bytes* the candidate would
        write. It used to carry ``planned["bytes"]``, which is the
        **number** of bytes: every file check that read it then judged
        a number instead of the file, so the candidate that would have
        written the right PDF lost to one that would have written
        nothing. The bytes live in ``planned["data"]``.

        The overlay never touches the disk - only the winning candidate
        is written afterwards.
        """
        overlay: Dict[str, bytes] = {}
        try:
            plan = plan_writes(artifact, self._write_workspace())
        except Exception:  # noqa: BLE001 - a broken plan must not kill the pool
            return overlay
        for planned in plan.get("planned") or []:
            data = planned.get("data")
            if not isinstance(data, (bytes, bytearray)):
                continue
            overlay[str(planned.get("absolute") or "")] = bytes(data)
        return overlay

    def _write_workspace(self) -> Any:
        """The folder this run writes into.

        It used to be the caller's current directory (or the directory
        named by ``--workspace``), which meant the files a run produced
        ended up wherever the process happened to stand - and, in an
        automated run, in a temporary folder that did not survive the
        run. Now it is the run's own folder under the state directory,
        so the output stays together and stays.
        """
        if self._workspace is None:
            self._workspace = ensure_workspace(self._run_workspace_path())
        return self._workspace

    def _run_workspace_path(self) -> str:
        """The folder of the current run, as a string (may not exist yet)."""
        return str(
            resolve_workspace(
                self.state_root(self.config),
                self._run_id or "run",
                self.config.get("workspace"),
            )
        )

    def _record_written(self, written: List[Dict[str, Any]]) -> None:
        """Remember the files this run wrote, for the ledger.

        The *length* of a file is not enough to recognise it later: two
        files of the same length are indistinguishable, so a file that
        was edited after the run - even to the same length - would leave
        no trace in the ledger. The content hash is therefore booked
        here, at the moment the file was written, and the closure seal
        (v0.7.88) is built on top of it.
        """
        from mas.seal import sha256_file

        for row in written or []:
            if not isinstance(row, dict):
                continue
            if not row.get("written"):
                continue
            absolute = str(row.get("absolute") or "")
            self._written_files.append(
                {
                    "path": str(row.get("path") or ""),
                    "absolute": absolute,
                    "bytes": int(row.get("bytes") or 0),
                    "sha256": sha256_file(absolute) if absolute else "",
                }
            )

    # ------------------------------------------------------------- drafts
    def _edit_policy(self) -> str:
        """What happens when a run writes over a file that already exists.

        "draft" (the default) stages the change as a draft and leaves
        the original file alone until a human approves it. "overwrite"
        keeps the old behaviour. A run that creates a *new* file is
        never affected - there is nothing to lose where there is
        nothing.
        """
        policy = str(self.config.get("edit_policy") or "draft").strip().lower()
        return policy if policy in ("draft", "overwrite") else "draft"

    def _apply_writes(
        self,
        artifact: str,
        run_id: str = "",
        task_id: str = "",
    ) -> Dict[str, Any]:
        """Write the files a candidate asked for, honouring the edit policy.

        With the default policy, a write that would *replace* an
        existing file becomes a draft: the original keeps its bytes and
        the proposed change waits for a human. A write that creates a
        new file is performed right away - the draft rule is about not
        destroying work, and there is no work to destroy in a file that
        does not exist yet.

        The returned dict has the same shape as
        ``mas.filewrite.apply_write_blocks`` plus a "drafts" list, so
        the callers do not have to know which path was taken.

        The run and the task are passed in, because the draft has to
        remember **which run proposed it**. A change that waits for a
        human is a change a person has to find later - and "which run
        wanted this?" is the first question they ask. A draft that
        does not know its own run makes that question unanswerable
        from the draft alone.
        """
        workspace = self._write_workspace()
        if self._edit_policy() != "draft":
            applied = apply_write_blocks(artifact, workspace)
            applied["drafts"] = []
            return applied

        from mas.drafts import stage_draft

        text, placeholders = _strip_placeholders(artifact)
        cleaned, writes, parse_errors = parse_write_blocks_with_errors(text)
        written: List[Dict[str, Any]] = []
        drafts: List[Dict[str, Any]] = []
        errors: List[str] = list(parse_errors)
        skipped: List[Dict[str, Any]] = [
            {"path": path, "reason": "placeholder path from the template"}
            for path in placeholders
        ]
        for request in writes:
            raw_path = request["path"]
            try:
                target = _safe_target(pathlib.Path(workspace), raw_path)
                data = _request_bytes(request["content"], request.get("encoding"))
                if len(data) > MAX_WRITE_BYTES:
                    raise FileWriteError(
                        f"refusing to write {len(data)} bytes (limit {MAX_WRITE_BYTES})"
                    )
            except (FileWriteError, OSError) as exc:
                errors.append(str(exc))
                continue
            if target.exists() and target.is_file():
                # The file is there and the loop wants to change it.
                # That is an *edit*, and an edit goes into a draft.
                try:
                    draft = stage_draft(
                        workspace,
                        str(target.relative_to(pathlib.Path(workspace).resolve())),
                        data,
                        reason="the loop proposed a change to an existing file",
                        source="engine",
                        run_id=str(run_id or ""),
                        task_id=str(task_id or ""),
                    )
                except (ValueError, OSError) as exc:
                    errors.append(str(exc))
                    continue
                drafts.append(
                    {
                        "id": draft.get("id"),
                        "path": draft.get("path"),
                        "absolute": draft.get("absolute"),
                        "bytes": int(draft.get("after_bytes") or 0),
                        "changed": bool(draft.get("changed")),
                        "written": False,
                        "draft": True,
                    }
                )
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                existed = target.exists()
                target.write_bytes(data)
                written.append(
                    {
                        "path": raw_path,
                        "absolute": str(target),
                        "bytes": len(data),
                        "written": True,
                        "created": not existed,
                    }
                )
            except OSError as exc:
                errors.append(str(exc))
        return {
            "artifact": cleaned,
            "written": written,
            "drafts": drafts,
            "errors": errors,
            "skipped": skipped,
        }

    def _draft_overlay(self) -> Dict[str, bytes]:
        """The pending drafts, as an overlay for the machine checks.

        A check that reads a file the loop wants to *change* has to see
        the changed version - otherwise the check judges the old bytes
        while the loop is proposing the new ones, and the loop can
        never close on its own proposal. The overlay is the same idea
        the parallel candidate ranking uses, and it never touches the
        disk.
        """
        try:
            from mas.drafts import draft_overlay

            return draft_overlay(self._write_workspace())
        except Exception:  # noqa: BLE001 - a broken overlay must not kill the run
            return {}

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
        # And whose money the panel spent. A panel is several calls,
        # and every one of them can have burnt tokens on a backend
        # that failed before the backup took over - so the panel's
        # spend is spread over the backends that paid for it. The
        # reviewers that failed outright are included: they were
        # billed before they broke.
        panel_spend = self._spend_of_usages(
            self._usages_of_panel(panel, results)
        ).get("backend_tokens", {})
        aggregate["_usage"] = {"total_tokens": tokens + failed_tokens}
        if panel_spend:
            aggregate["_usage"]["backend_tokens"] = panel_spend
        aggregate["errors"] = errors
        aggregate["personas"] = personas
        # Which backends answered the panel. A panel of three can be
        # served by three different backends when the chain fails over
        # mid-panel, and the report has to be able to say so.
        aggregate["_backends"] = self._backends_of(
            [
                member
                for member in panel
                if isinstance(member, dict)
            ]
        )
        # Kept so a targeted re-ask can merge into the same panel instead
        # of replacing it with a single reviewer.
        aggregate["_panel"] = panel
        return aggregate

    @staticmethod
    def _usages_of_panel(
        panel: List[Any], results: List[Any]
    ) -> List[Dict[str, Any]]:
        """The usage of every call a panel round made.

        The reviewers that answered keep their usage under the
        internal prefix; the ones that failed carry it on the error.
        Both were billed, so both belong to the bill.
        """
        usages: List[Dict[str, Any]] = []
        for member in panel or []:
            if not isinstance(member, dict):
                continue
            usage = member.get("_usage")
            if isinstance(usage, dict):
                usages.append(usage)
        for item in results or []:
            if isinstance(item, ParallelFailure):
                usage = getattr(item.error, "usage", None)
                if isinstance(usage, dict):
                    usages.append(usage)
        return usages

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
                # Which backends answered the panel. A panel can be
                # served by more than one backend, and the reader has
                # to be able to see that the review was not all one
                # machine talking to itself.
                "backends": aggregate.get("_backends") or [],
                # And whose money the panel spent. The reviewers that
                # failed outright are in here too: they were billed
                # before they broke, and a panel that names only the
                # backends that answered would hide exactly the
                # endpoint that was burning the budget.
                # The split is read out of the round's own usage, so
                # the charge and the split can never drift apart. A
                # round with no failover has no split to report, and
                # says nothing rather than showing an empty table.
                **self._spend_fields(aggregate.get("_usage") or {}),
                # And the backend of the single reviewer, in the
                # panel-of-one case, where there is only one.
                **self._backend_fields(aggregate),
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
                # Which backend answered. A panel of one is still
                # served by a backend, and the report reads the panel
                # and the single reviewer the same way.
                "_backends": self._backends_of([payload]),
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
            # And whose money the re-ask round spent. The re-ask is a
            # round of calls of its own, so it has to be able to name
            # the backends that paid for it. Only this round's calls
            # are counted here - the panel members keep the usage of
            # the round that first asked them, and the first round was
            # booked on its own entry.
            reask_spend = self._spend_of_usages(
                self._usages_of_panel([], results)
            ).get("backend_tokens", {})
            if reask_spend:
                rebuilt["_usage"]["backend_tokens"] = reask_spend
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
    def _seal_closure(
        self,
        run_id: str,
        crits: Any,
        artifact: str,
        scores: Any,
        verdict: str,
        decision: str,
    ) -> Dict[str, Any]:
        """Seal the closure to the bytes it was decided on.

        A verdict that cannot be recomputed is a claim, not a proof. So
        the moment the loop decides, it books *what it decided on*: the
        acceptance criteria in full, the hash of the deliverable's
        bytes, the hash of every file the run wrote, and the per-
        criterion verdicts. Afterwards ``mas seal`` can re-run the
        machine checks against the sealed criteria and say whether the
        closure still holds - or whether somebody edited the files
        afterwards.

        The seal is only written for a run that *decided* something. A
        run that died on the budget ceiling has no verdict to seal, and
        a seal for it would be a claim about nothing.
        """
        from mas.seal import build_seal, sha256_bytes

        return build_seal(
            run_id=str(run_id or ""),
            criteria=[criterion.to_dict() for criterion in crits or []],
            artifact=artifact or "",
            artifact_sha256=sha256_bytes((artifact or "").encode("utf-8")),
            files=list(self._written_files),
            scores=list(scores or []),
            verdict=str(verdict or ""),
            decision=str(decision or ""),
            # The workspace the machine checks actually read. This is
            # not always the run's own folder: a handback judges the
            # files of the run that produced the ticket, and a folder
            # handoff judges the folder the human edited. Sealing the
            # run's own folder instead would seal a folder that was
            # never checked - and the recomputation would then read
            # the wrong files and report a drift that never happened.
            workspace=str(
                self._check_config.get("workspace")
                or self._run_workspace_path()
            ),
        )

    def _seal_for_run(
        self,
        run_id: str,
        crits: Any,
        artifact: str,
        scores: Any,
        verdict: str,
        decision: str,
    ) -> Dict[str, Any]:
        """The seal of a run - but only if the run actually decided.

        A run that died on the budget ceiling, or that never reached a
        scoring round, has no verdict. Sealing it would book a
        conclusion about nothing - and the report would then read that
        seal as "the conclusion drifted", which accuses a run of lying
        about something it never said.

        The guard lives in its own method so it can be watched. A guard
        that is written inline in the middle of a hundred-line log call
        is a guard nobody can break on purpose - and a gate that
        cannot fail is not a gate.
        """
        if not scores:
            return {}
        return self._seal_closure(
            run_id,
            crits,
            artifact,
            scores,
            verdict,
            decision,
        )

    def _save_artifact(self, run_id: str, artifact: str) -> str:
        """Write the final deliverable to disk and return its path.

        The artifact is the whole point of the loop. Keeping it in
        memory only meant that a successful run left nothing behind
        for the user to open - the report could point at a hash, and a
        hash is not a deliverable. The file is written next to the
        ledger, named after the run, so every run has its own.

        A write that fails must not take the run down: the work is
        done, the artifact is in memory, and a closed loop that loses
        its file because of a permission problem would be reported as
        a crash of the loop itself.
        """
        if not artifact:
            return ""
        try:
            # The deliverable lives in the run's own workspace, next to
            # the files the run produced. A closed run therefore hands
            # over *one folder* holding everything it made - not a
            # deliverable in one place and its supporting files in
            # another (or in a temporary directory that is already
            # gone).
            target_dir = self._write_workspace()
            target = pathlib.Path(target_dir) / DELIVERABLE_NAME
            target.write_text(artifact, encoding="utf-8")
            return str(target)
        except OSError:
            return ""

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

    @staticmethod
    def _backend_fields(source: Dict[str, Any]) -> Dict[str, Any]:
        """Read the answering backend out of a provider answer.

        The answer carries it under two different names on purpose:
        a text answer keeps it at the top level, a JSON answer keeps
        it under the internal prefix so it cannot ride along into the
        next prompt. Both are read here, in one place, so a call site
        cannot forget one of them.
        """
        provider = str(
            source.get("_provider_used") or source.get("provider_used") or ""
        )
        model = str(source.get("_model_used") or source.get("model_used") or "")
        if not provider and not model:
            return {}
        return {"provider_used": provider, "model_used": model}

    @staticmethod
    def _spend_fields(usage: Dict[str, Any]) -> Dict[str, Any]:
        """Whose money a call really spent.

        The gap this closes
        ------------------
        A failover chain folds the spend of every backend that failed
        into the total the run is charged. That total is right - the
        run did pay for the failed calls - but it used to be booked
        under the name of the backend that *answered*. So a row said
        "backup-kimi spent 8 tokens" when backup-kimi had spent 3 and
        the dead primary had spent 5.

        That is a lie about money, and it is a lie in the direction
        that costs the reader the most: the endpoint that was burning
        the budget is exactly the one that failed, so the bill named
        the healthy one instead.

        So the chain keeps the spend of each backend apart, and the
        row keeps it too. A call with no failover has nothing to
        spread, and says nothing.
        """
        if not isinstance(usage, dict):
            return {}
        # The plain key is the shape the chain hands out; the prefixed
        # one is the shape an aggregate keeps it under, because an
        # aggregate is a verdict and a verdict travels into prompts.
        by_backend = usage.get("backend_tokens") or usage.get("_backend_tokens")
        if not isinstance(by_backend, dict) or not by_backend:
            return {}
        spent = {}
        for name, tokens in by_backend.items():
            try:
                amount = int(tokens or 0)
            except (TypeError, ValueError):
                continue
            if amount:
                spent[str(name)] = amount
        if not spent:
            return {}
        return {"backend_tokens": spent}

    def _spend_of_pool(
        self, results: List[Any]
    ) -> Dict[str, Any]:
        """Whose money a whole candidate pool spent.

        A pool is several calls, each of which may have been served by
        a different backend and each of which may have burnt tokens
        on a backend that failed before the backup took over. So the
        pool's spend is spread over the backends that paid for it -
        and a pool where three of four members failed on a dead
        endpoint has to say so, instead of naming the one member that
        answered.
        """
        usages: List[Dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            usages.append(item.get("usage") or {})
        return self._spend_of_usages(usages)

    def _spend_of_usages(
        self, usages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Whose money a set of calls spent, in total.

        Every call hands back the spend of each backend it touched, and
        a round of the loop is several calls. This adds them up, so a
        panel of three that was served by two backends can say that
        one backend paid for two of the three votes.
        """
        spent: Dict[str, int] = {}
        for usage in usages or []:
            for name, amount in self._spend_fields(usage).get(
                "backend_tokens", {}
            ).items():
                spent[name] = int(spent.get(name) or 0) + int(amount)
        if not spent:
            return {}
        return {"backend_tokens": spent}

    def _backends_of(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Every backend that answered, in the order they answered.

        A review panel can be served by different backends - one
        member answered by Claude, another by Kimi - so the truth is a
        list, not a single name. The list is deduplicated by name and
        model, and it keeps the order the answers came in.
        """
        seen: List[Dict[str, Any]] = []
        for source in sources:
            fields = self._backend_fields(source or {})
            if not fields:
                continue
            pair = (fields["provider_used"], fields["model_used"])
            if any(
                (item.get("provider_used"), item.get("model_used")) == pair
                for item in seen
            ):
                continue
            seen.append(fields)
        return seen

    def _run_backends(self, run_id: str) -> Dict[str, Any]:
        """Every backend this run was served by, and whether it fell over.

        The run's own end entry has to be able to say "the primary was
        down and the backup delivered this". The per-call rows carry
        the detail; this is the summary a reader of one run asks for.

        Rows written before this release carry no backend at all, and
        they are not guessed at: an old run says nothing about its
        backend rather than claiming the configured one answered.
        """
        primary = str(self.provider.name or "")
        backends: List[Dict[str, Any]] = []
        failed_over = False
        for row in self.ledger.entries():
            if str(row.get("run_id") or "") != run_id:
                continue
            fields = self._backend_fields(row)
            if not fields:
                continue
            pair = (fields["provider_used"], fields["model_used"])
            if not any(
                (item.get("provider_used"), item.get("model_used")) == pair
                for item in backends
            ):
                backends.append(fields)
            if primary and fields["provider_used"] != primary:
                failed_over = True
        if not backends:
            return {}
        return {
            "backends": backends,
            # The backend that answered the most calls, and whether the
            # run had to fall over at least once.
            "failed_over": failed_over,
        }

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
            response = self.provider.complete(
                system=self.roles.system(role),
                prompt=self.roles.prompt(role, state, extra=extra),
                role=role,
                expect_json=False,
                max_tokens=self._output_budget(role, int(state.get("attempt") or 1)),
            )
        # Which backend answered. A chain that failed over says so here,
        # and a single provider simply names itself - so every caller
        # can read the same two keys without asking what kind of
        # provider it was given.
        response.setdefault("provider_used", self.provider.name)
        response.setdefault("model_used", self.provider.model)
        return response

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
        # The backend that answered, kept under the internal prefix so
        # it never rides along into a prompt: the plan and the
        # reflection travel to the model on the next call, and a
        # backend name is bookkeeping, not work.
        data["_provider_used"] = str(
            response.get("provider_used") or self.provider.name or ""
        )
        data["_model_used"] = str(
            response.get("model_used") or self.provider.model or ""
        )
        return data

    def _consult_lead(
        self,
        verify_state: Dict[str, Any],
        scores: List[Dict[str, Any]],
        checks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Ask the lead to judge the criteria nobody else could judge.

        The lead is a paid call, so it is booked like every other call
        and its answer goes into the ledger. A lead that fails - a
        broken endpoint, a garbled answer - leaves the criteria
        unjudged: the run then continues as it did before, which is
        the honest outcome. A lead can never make a criterion pass by
        being silent.
        """
        state = dict(verify_state)
        state["scores"] = list(scores)
        state["checks"] = list(checks)
        try:
            answer = self._call_json("lead", state)
        except (ProviderError, TruncatedAnswer):
            # The lead is an advisor. If it cannot be reached, the run
            # goes on without it - the engine's own verdict is not
            # weakened by a missing advisor.
            return {}
        usage = dict(answer.get("_usage") or {})
        self._log(
            verify_state["run_id"], verify_state["task_id"],
            verify_state["attempt"], "lead",
            {
                "lead": {
                    key: value
                    for key, value in answer.items()
                    if key not in INTERNAL_KEYS
                },
                "tokens": int(usage.get("total_tokens", 0) or 0),
                # And whose money it was.
                **self._spend_fields(usage),
            },
        )
        return answer

    def _apply_lead_judgement(
        self,
        scores: List[Dict[str, Any]],
        checks: List[Dict[str, Any]],
        lead: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Fold the lead's verdicts into the criteria nobody could judge.

        The lead may only fill a gap. A criterion that a machine check
        decided is left exactly as the check decided it - the lead
        cannot overrule a machine check, whatever it says. A criterion
        the lead itself could not judge stays unjudged, so the run
        keeps asking rather than closing on a shrug.
        """
        judged: Dict[str, Dict[str, Any]] = {}
        for item in lead.get("judged") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "")
            if cid:
                judged[cid] = item
        if not judged:
            return scores

        decided_ids = {
            str(check.get("id"))
            for check in checks or []
            if check.get("pass") is not None
        }
        updated: List[Dict[str, Any]] = []
        for score in scores:
            cid = str(score.get("id") or "")
            answer = judged.get(cid)
            # A criterion the machine checks already decided is not the
            # lead's to decide.
            if (
                not answer
                or score.get("source") != "model-missing"
                or cid in decided_ids
            ):
                updated.append(score)
                continue
            met = _as_bool(answer.get("met"))
            if met is None:
                # The lead did not say. An undecided lead is not a
                # passing lead.
                updated.append(score)
                continue
            entry = dict(score)
            entry["pass"] = met
            entry["source"] = "lead"
            entry["note"] = str(answer.get("note") or score.get("note") or "")
            updated.append(entry)
        return updated

    @staticmethod
    def _lead_is_consulted(scores: List[Dict[str, Any]]) -> bool:
        """Whether the lead has anything to decide.

        The lead exists for exactly one situation: some acceptance
        criteria have no verdict at all. The machine checks could not
        speak about them and the reviewers did not either, so the
        engine has no answer - and without the lead such a run simply
        spins until it stagnates and escalates, which tells the human
        nothing except that nobody could judge.

        The lead is *not* consulted when the machine checks already
        decided. A check that failed is a fact, not a matter of
        opinion, and no amount of lead reasoning may turn it into a
        pass.
        """
        for score in scores or []:
            if not score.get("blocking", True):
                continue
            if score.get("source") == "model-missing":
                return True
        return False

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
            # Every score carries the sentence that says what this
            # criterion asked for. The score alone was a serial number
            # (``c1``) plus an English note; the user wrote several
            # standards and could not tell which one the answer was
            # about. The reading travels with the score into the
            # ledger, so the report can quote it back.
            reading = criterion_reading(criterion)
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
                            "reading": reading,
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
                            "reading": reading,
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
                            "reading": reading,
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
                            "reading": reading,
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
                        "reading": reading,
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
                        "reading": reading,
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
                        "reading": reading,
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
                            "reading": reading,
                        }
                    )
                    continue
                entry = {
                    "id": criterion.id,
                    "pass": decided,
                    "note": str(review.get("note", "")),
                    "source": review.get("source", "model"),
                    "blocking": True,
                    "reading": reading,
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


    # -------------------------------------------------------- handover ticket
    @staticmethod
    def _handover_payload(
        run_id: str,
        task_id: str,
        goal: str,
        title: str,
        crits: List[Any],
        artifact: str,
        issues: List[str],
        attempt: int,
        history: List[Dict[str, Any]],
        stagnant: int,
        status: str,
        reason: str,
        budget: Optional[Dict[str, Any]] = None,
        workspace: str = "",
    ) -> Dict[str, Any]:
        """Build the context a human needs to take a failed run over.

        Every ticket a run leaves behind carries the same keys. That is
        not tidiness - it is the difference between a handover and a
        note: the reader has to be able to ask the same questions of
        every ticket in the queue ("what was the goal", "what did it
        fail on", "what did the last attempt produce", "which runs are
        involved") without first working out which kind of failure
        they are looking at.

        The criteria are the load-bearing part. They come back as
        machine checks, so a resumed loop can still be decided by a
        rule instead of by the reviewer's mood. A ticket that lost them
        would still look complete - it would still have a goal and a
        reason - but the loop behind it would have no standards left.
        """
        return {
            "run_id": run_id,
            "task_id": task_id,
            "goal": goal,
            "title": title,
            "criteria": [c.to_dict() for c in crits],
            "last_artifact": artifact,
            "issues": list(issues),
            "attempts": attempt,
            # The rounds spent on this problem so far, and the runs
            # that spent them. The first run of a thread starts both
            # counters at its own attempt count.
            "iterations_total": attempt,
            "runs": [run_id],
            "history": history,
            "stagnation": stagnant,
            "reason": reason,
            "status": status,
            # The budget this run ran under. A ticket that says "it
            # stopped" but not "it stopped *here*" leaves the reader
            # guessing which ceiling to raise.
            "budget": dict(budget or {}),
            # The folder the run worked in. A human who edits the
            # files the run produced has to edit them *there* - and a
            # handback that checks the files has to look there too.
            "workspace": str(workspace or ""),
        }

    def _open_handover_ticket(
        self,
        run_id: str,
        task_id: str,
        goal: str,
        title: str,
        crits: List[Any],
        artifact: str,
        issues: List[str],
        attempt: int,
        history: List[Dict[str, Any]],
        stagnant: int,
        status: str,
        error: str,
        last_error: str,
        parent_ticket_id: Optional[str] = None,
        budget: Optional[Dict[str, Any]] = None,
        workspace: str = "",
    ) -> str:
        """Open a human ticket for a run that did not close.

        The reason a run stopped matters as much as the fact that it
        stopped: "hit the iteration ceiling" and "the provider died"
        call for different human reactions - raise the budget versus
        fix the credentials. The ticket carries the reason, the last
        deliverable and the criteria, so the human can decide without
        re-running anything.
        """
        if status == STATUS_BUDGET:
            kind = "budget_exhausted"
            reason = error or last_error or "the run ran out of budget"
        elif status == STATUS_ERROR:
            kind = "run_error"
            reason = error or last_error or "the run failed"
        else:
            kind = "loop_escalation"
            reason = error or last_error or "loop could not close itself"

        payload = self._handover_payload(
            run_id=run_id,
            task_id=task_id,
            goal=goal,
            title=title,
            crits=crits,
            artifact=artifact,
            issues=issues,
            attempt=attempt,
            history=history,
            stagnant=stagnant,
            status=status,
            reason=reason,
            budget=budget,
            workspace=workspace,
        )
        if parent_ticket_id:
            # A resumed loop keeps the ticket it already has: one
            # problem must not be listed twice.
            self.tickets.append_attempt(parent_ticket_id, payload, reason=reason)
            return parent_ticket_id
        ticket = self.tickets.create(kind, payload)
        return str(ticket["id"])

    # -------------------------------------------------------------- handback
    def handback(
        self,
        ticket_id: str,
        artifact: str,
        note: str = "",
        run_id: Optional[str] = None,
    ) -> RunResult:
        """Take a human-written deliverable back into the loop.

        The ticket queue could only be answered in one way: resume the
        loop and let the machine try again. A human who had already
        written the deliverable - the case that actually happens when a
        loop escalates - had nowhere to put it. Their work would be
        re-derived by the machine, usually worse, and the human had no
        way to say "this is the answer, check it".

        This is the handback path: the human submits a deliverable, and
        the *same* acceptance standards that the loop was held to decide
        it. The standards come back from the ticket, so they are the
        ones the task was written with - not a re-interpretation.

        Two things make this honest rather than a rubber stamp:

        * A criterion with a machine check is decided by that check.
          The reviewer cannot override it, exactly as in a normal run.
        * A deliverable that fails is *not* accepted. The failing
          criteria are written back onto the ticket, and the ticket
          stays in the human queue. A handback that fails is a second
          try, not a closure.

        The deliverable is written to disk on success, next to the
        ledger, so the accepted answer survives the process that
        accepted it.
        """
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket: {ticket_id}")
        payload = ticket.get("payload") or {}
        crits = [
            Criterion.from_raw(raw_criterion, index)
            for index, raw_criterion in enumerate(payload.get("criteria") or [])
        ]
        if not crits:
            raise ValueError(
                "工单 %s 没有验收标准，没法验收人工交付物" % ticket_id
            )
        problems = validate_criteria(crits)
        if problems:
            raise ValueError(
                "验收标准里有无法执行的机检规则:\n  - " + "\n  - ".join(problems)
            )
        if not artifact.strip():
            raise ValueError("人工交付物是空的，没法验收")

        run_id = run_id or "H-" + uuid.uuid4().hex[:10]
        task_id = f"{run_id}-1"
        self._run_id = str(run_id)
        self._written_files = []
        self._workspace = None
        # The file checks of a handback resolve against the workspace of
        # the run that produced the ticket - the human edited files in
        # *that* folder, and checking them anywhere else would report
        # every file as missing.
        check_config = dict(self.config)
        check_config["workspace"] = str(
            payload.get("workspace") or self._run_workspace_path()
        )
        self._check_config = check_config

        started_at = now_iso()
        checks = [
            evaluate_check(criterion, artifact, self._check_config) for criterion in crits
        ]
        state: Dict[str, Any] = {
            "goal": str(payload.get("goal") or ""),
            "title": str(payload.get("title") or ""),
            "criteria": [criterion.to_dict() for criterion in crits],
            "attempt": 1,
            "issues": [],
            "previous_artifact": str(payload.get("last_artifact") or ""),
            "human_note": note or str(ticket.get("note") or ""),
            "handback": True,
        }
        verify_state = dict(state)
        verify_state["candidate_artifact"] = artifact
        verify_state["checks"] = checks
        tokens = 0
        verdict: Dict[str, Any] = {}
        review_error = ""
        try:
            verdict = self._review(verify_state)
            tokens += int(verdict.get("_usage", {}).get("total_tokens", 0) or 0)
            if self.reviewers > 1:
                scores = self._merge_review_scores(crits, checks, verdict)
            else:
                scores = self._merge_scores(crits, checks, verdict)
            extra = self._ask_missing_verdicts(
                verify_state, crits, checks, verdict, scores
            )
            if extra:
                tokens += int(extra.get("_usage", {}).get("total_tokens", 0) or 0)
                if self.reviewers > 1:
                    scores = self._merge_review_scores(crits, checks, extra)
                else:
                    scores = self._merge_scores(crits, checks, extra)
                verdict = extra
        except Exception as exc:  # noqa: BLE001 - a dead reviewer must not crash a handback
            # The machine checks are already decided. A reviewer that
            # dies must not turn a handback into a crash - the
            # standards that can be checked without a model are checked
            # anyway, and the failure is reported.
            #
            # The catch is deliberately broad, and it matches the one
            # the main loop uses: a provider can fail in ways that are
            # not a ProviderError (a broken transport, a KeyError in a
            # response shape, a TimeoutError from a socket). The main
            # loop survives those; a handback that crashed on one of
            # them would lose a deliverable a human had already
            # written - the exact thing this path exists to protect.
            review_error = str(exc) or exc.__class__.__name__
            verdict = {}
            scores = self._merge_scores(crits, checks, verdict)

        issues = [
            str(score.get("note") or "")
            for score in scores
            if not score.get("pass") and score.get("blocking", True)
        ]
        deliverable_ok = all(
            score.get("pass")
            for score in scores
            if score.get("blocking", True)
        )
        # A criterion nobody judged is not a passing criterion. It is
        # reported as unjudged, and the handback does not close.
        unjudged = [
            str(score.get("id") or "")
            for score in scores
            if score.get("pass") is None and score.get("blocking", True)
        ]

        self._log(
            run_id,
            task_id,
            1,
            "handback",
            {
                "ticket_id": ticket_id,
                "note": note or str(ticket.get("note") or ""),
                "artifact_sha": sha256_text(artifact),
                "artifact_chars": len(artifact),
                "checks": checks,
                "scores": scores,
                "issues": issues,
                "unjudged": unjudged,
                "review_error": review_error,
                "tokens": tokens,
            },
        )

        artifact_path = ""
        if deliverable_ok and not unjudged:
            status = STATUS_CLOSED
            error = ""
            artifact_path = self._save_artifact(run_id, artifact)
        else:
            status = STATUS_ESCALATED
            error = "人工交付物未通过验收标准"
            if unjudged:
                error += "（未判定：%s）" % ", ".join(unjudged)
            if review_error:
                error += "：评审失败：%s" % review_error

        result = RunResult(
            run_id=run_id,
            task_id=task_id,
            status=status,
            iterations=1,
            artifact=artifact,
            artifact_sha=sha256_text(artifact),
            issues=issues,
            scores=scores,
            ticket_id=ticket_id,
            tokens_used=tokens,
            ledger_path=str(self.ledger.path),
            artifact_path=artifact_path,
            workspace=self._run_workspace_path(),
            files=list(self._written_files),
            history=[],
            started_at=started_at,
            finished_at=now_iso(),
            error=error,
            deliverable_ok=deliverable_ok,
            budget=dict(payload.get("budget") or {}),
        )
        self._log(
            run_id,
            task_id,
            1,
            "run_end",
            {
                "status": status,
                "iterations": 1,
                "tokens": tokens,
                "artifact_sha": result.artifact_sha,
                "artifact_path": artifact_path,
                "deliverable_ok": deliverable_ok,
                "issues": issues,
                "ticket_id": ticket_id,
                "workspace": result.workspace,
                # The end entry is the engine's own account of the run,
                # and the report reads it for everything it knows about
                # a run. The handback entry used to carry only a part of
                # that account - no ``closed``, no goal, no files - so a
                # human deliverable that *did* close the loop was
                # reported as an open problem. The reader was told the
                # opposite of what happened.
                "closed": status == STATUS_CLOSED,
                # The decision of a handback is its own word: the
                # deliverable was not written by the loop, it was
                # written by a person and then judged. Saying
                # "close" would hide the difference between "the
                # machine did it" and "a human did it and the
                # machine checked it" - and that difference is
                # the whole point of the handback path.
                "decision": "handback" if status == STATUS_CLOSED else "revise",
                "error": error,
                "goal": str(payload.get("goal") or ""),
                "title": str(payload.get("title") or payload.get("goal") or ""),
                "artifact_chars": len(artifact),
                "files": list(self._written_files),
                # A handback is judged by the same standards as any
                # other closure, so it is sealed the same way.
                "seal": self._seal_for_run(
                    run_id,
                    crits,
                    artifact,
                    scores,
                    "pass" if deliverable_ok else "fail",
                    "handback" if status == STATUS_CLOSED else "revise",
                ),
                # The run that produced the ticket this handback answers.
                # Without it the report cannot say "this attempt came
                # after that one" - the handback looks like a run of its
                # own that solved a different problem.
                "parent_ticket_id": ticket_id,
                # The mark that says a human wrote this deliverable.
                # Every report, replay and thread view keys off it, so
                # it has to be on the ledger, not only in the engine's
                # memory.
                "handback": True,
                "human_note": note or str(ticket.get("note") or ""),
            },
        )

        if result.closed:
            # The handback closed the problem, so the ticket closes with
            # it - and it points at the run that closed it, so the queue
            # can answer "how was this solved?" with one click.
            self.tickets.resolve(ticket_id, "handback", note or "人工交付物通过验收")
            self.tickets.note_success(
                ticket_id,
                run_id=run_id,
                iterations=1,
                artifact=artifact_path or artifact,
            )
        else:
            # A rejected handback is evidence on the ticket, not a new
            # ticket. One problem stays one thread, and the failing
            # criteria travel with it.
            self.tickets.reopen(
                ticket_id,
                note or "人工交付物未通过验收",
                reason=(
                    "handback did not meet the acceptance criteria"
                    + (f": {'; '.join(issues)}" if issues else "")
                ),
                result=result.to_dict(),
            )
            self.tickets.append_attempt(
                ticket_id,
                {
                    "run_id": run_id,
                    "attempts": 1,
                    "last_artifact": artifact,
                    "issues": issues,
                    "criteria": [criterion.to_dict() for criterion in crits],
                },
                reason=error,
            )
        return result

    # --------------------------------------------------- folder handoff
    def handback_workspace(
        self,
        ticket_id: str,
        note: str = "",
        workspace: Optional[str] = None,
    ) -> RunResult:
        """Take a *folder* of human edits back into the loop.

        The text handback takes one string. That is the wrong shape for
        the tasks this loop actually runs: a worker writes files, and
        the acceptance criteria are file checks that read the disk. A
        human who fixes such a task edits the files in the workspace -
        and there was no way to hand that back. The folder they had
        edited was never read; the handback took a string instead, so
        the file checks judged the *old* files and the human's work was
        invisible to the loop.

        This is the "draft first, write back after approval" rule, the
        same rule a document editor follows:

        * The folder is read as it stands now - the human's edits are
          already in it.
        * It is judged against the ticket's own criteria, with the
          folder's contents as an overlay, so the judge sees the
          edited files and writes nothing.
        * Only a deliverable that passes is written back as the
          finished artifact.

        A refused handoff changes nothing: the human's edits stay on
        the disk exactly as they left them, because they are the
        human's work and not this loop's to discard.
        """
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket: {ticket_id}")
        payload = ticket.get("payload") or {}
        crits = [
            Criterion.from_raw(raw_criterion, index)
            for index, raw_criterion in enumerate(payload.get("criteria") or [])
        ]
        if not crits:
            raise ValueError(
                "工单 %s 没有验收标准，没法验收人工交付物" % ticket_id
            )
        problems = validate_criteria(crits)
        if problems:
            raise ValueError(
                "验收标准里有无法执行的机检规则:\n  - " + "\n  - ".join(problems)
            )

        # The folder the human edited. The ticket knows it - it is
        # where the run worked - and an explicit path wins over it, so
        # a human can hand back a copy from somewhere else.
        root = str(
            workspace
            or payload.get("workspace")
            or self._run_workspace_path()
        ).strip()
        if not root:
            raise ValueError("工单 %s 没有工作区路径，不知道该验收哪个文件夹" % ticket_id)
        folder = pathlib.Path(root).expanduser()
        if not folder.exists() or not folder.is_dir():
            raise ValueError("工作区不存在或不是文件夹：%s" % folder)

        files = collect_workspace_files(folder)
        if not files:
            raise ValueError(
                "工作区 %s 里没有可交回的文件" % folder
            )

        run_id = "H-" + uuid.uuid4().hex[:10]
        task_id = f"{run_id}-1"
        self._run_id = str(run_id)
        self._written_files = []
        self._workspace = None

        # The checks of a folder handoff resolve against the folder the
        # human edited. The overlay is what they read: the bytes that
        # are on the disk *now*, so the file checks judge the human's
        # edits rather than the ones the loop left behind.
        check_config = dict(self.config)
        check_config["workspace"] = str(folder)
        self._check_config = check_config
        overlay = read_workspace_overlay(folder)

        # The deliverable is written *into the folder the human
        # edited*. The run that produced the ticket had a workspace of
        # its own, and a handoff has a run id of its own too - so
        # without this the accepted deliverable would land in a
        # second folder, and the human's files and the deliverable
        # that names them would be apart again. The whole point of
        # editing in place is that the result stays in one place.
        self._workspace = ensure_workspace(folder)

        # The text of the deliverable: an index of the files plus the
        # content of the small ones. The text checks are judged against
        # it, so it has to carry the human's own words.
        artifact = build_deliverable(files)

        started_at = now_iso()
        checks = [
            evaluate_check(criterion, artifact, check_config, overlay)
            for criterion in crits
        ]
        state: Dict[str, Any] = {
            "goal": str(payload.get("goal") or ""),
            "title": str(payload.get("title") or ""),
            "criteria": [criterion.to_dict() for criterion in crits],
            "attempt": 1,
            "issues": [],
            "previous_artifact": str(payload.get("last_artifact") or ""),
            "human_note": note or str(ticket.get("note") or ""),
            "handback": True,
        }
        verify_state = dict(state)
        verify_state["candidate_artifact"] = artifact
        verify_state["checks"] = checks
        tokens = 0
        verdict: Dict[str, Any] = {}
        review_error = ""
        try:
            verdict = self._review(verify_state)
            tokens += int(verdict.get("_usage", {}).get("total_tokens", 0) or 0)
            if self.reviewers > 1:
                scores = self._merge_review_scores(crits, checks, verdict)
            else:
                scores = self._merge_scores(crits, checks, verdict)
            extra = self._ask_missing_verdicts(
                verify_state, crits, checks, verdict, scores
            )
            if extra:
                tokens += int(extra.get("_usage", {}).get("total_tokens", 0) or 0)
                if self.reviewers > 1:
                    scores = self._merge_review_scores(crits, checks, extra)
                else:
                    scores = self._merge_scores(crits, checks, extra)
                verdict = extra
        except Exception as exc:  # noqa: BLE001 - a dead reviewer must not crash a handoff
            review_error = str(exc) or exc.__class__.__name__
            verdict = {}
            scores = self._merge_scores(crits, checks, verdict)

        issues = [
            str(score.get("note") or "")
            for score in scores
            if not score.get("pass") and score.get("blocking", True)
        ]
        deliverable_ok = all(
            score.get("pass")
            for score in scores
            if score.get("blocking", True)
        )
        unjudged = [
            str(score.get("id") or "")
            for score in scores
            if score.get("pass") is None and score.get("blocking", True)
        ]

        self._log(
            run_id,
            task_id,
            1,
            "handback",
            {
                "ticket_id": ticket_id,
                "note": note or str(ticket.get("note") or ""),
                "artifact_sha": sha256_text(artifact),
                "artifact_chars": len(artifact),
                "checks": checks,
                "scores": scores,
                "issues": issues,
                "unjudged": unjudged,
                "review_error": review_error,
                "tokens": tokens,
                # The folder the human edited and the files in it. A
                # report that cannot say which files were offered cannot
                # show the human what was handed in.
                "handoff_workspace": str(folder),
                "handoff_files": files,
            },
        )

        artifact_path = ""
        written_files: List[Dict[str, Any]] = []
        if deliverable_ok and not unjudged:
            status = STATUS_CLOSED
            error = ""
            # The write back. The human's files are already on the
            # disk; what is missing is the deliverable that names them,
            # and it is written next to them - the same "one folder
            # holds everything" rule every run obeys.
            artifact_path = self._save_artifact(run_id, artifact)
            written_files = [
                {
                    "path": str(row.get("path") or ""),
                    "absolute": str(row.get("absolute") or ""),
                    "bytes": int(row.get("bytes") or 0),
                }
                for row in files
            ]
        else:
            status = STATUS_ESCALATED
            error = "人工交付物未通过验收标准"
            if unjudged:
                error += "（未判定：%s）" % ", ".join(unjudged)
            if review_error:
                error += "：评审失败：%s" % review_error

        result = RunResult(
            run_id=run_id,
            task_id=task_id,
            status=status,
            iterations=1,
            artifact=artifact,
            artifact_sha=sha256_text(artifact),
            issues=issues,
            scores=scores,
            ticket_id=ticket_id,
            tokens_used=tokens,
            ledger_path=str(self.ledger.path),
            artifact_path=artifact_path,
            workspace=str(folder),
            files=written_files,
            history=[],
            started_at=started_at,
            finished_at=now_iso(),
            error=error,
            deliverable_ok=deliverable_ok,
            budget=dict(payload.get("budget") or {}),
        )
        self._log(
            run_id,
            task_id,
            1,
            "run_end",
            {
                "status": status,
                "iterations": 1,
                "tokens": tokens,
                "artifact_sha": result.artifact_sha,
                "artifact_path": artifact_path,
                "deliverable_ok": deliverable_ok,
                "issues": issues,
                "ticket_id": ticket_id,
                "workspace": result.workspace,
                "closed": status == STATUS_CLOSED,
                "decision": "handback" if status == STATUS_CLOSED else "revise",
                "error": error,
                "goal": str(payload.get("goal") or ""),
                "title": str(payload.get("title") or payload.get("goal") or ""),
                "artifact_chars": len(artifact),
                "files": written_files,
                # The folder handoff is judged the same way and sealed
                # the same way - the human's files are part of what the
                # closure decided on.
                "seal": self._seal_for_run(
                    run_id,
                    crits,
                    artifact,
                    scores,
                    "pass" if deliverable_ok else "fail",
                    "handback" if status == STATUS_CLOSED else "revise",
                ),
                "parent_ticket_id": ticket_id,
                "handback": True,
                "human_note": note or str(ticket.get("note") or ""),
            },
        )

        if result.closed:
            self.tickets.resolve(ticket_id, "handback", note or "人工交付物通过验收")
            self.tickets.note_success(
                ticket_id,
                run_id=run_id,
                iterations=1,
                artifact=artifact_path or artifact,
            )
        else:
            self.tickets.reopen(
                ticket_id,
                note or "人工交付物未通过验收",
                reason=(
                    "handoff did not meet the acceptance criteria"
                    + (f": {'; '.join(issues)}" if issues else "")
                ),
                result=result.to_dict(),
            )
            self.tickets.append_attempt(
                ticket_id,
                {
                    "run_id": run_id,
                    "attempts": 1,
                    "last_artifact": artifact,
                    "issues": issues,
                    "criteria": [criterion.to_dict() for criterion in crits],
                    "handoff_workspace": str(folder),
                    "handoff_files": files,
                },
                reason=error,
            )
        return result

    # --------------------------------------------------------------- resume
    def resume(
        self,
        ticket_id: str,
        note: str = "",
        max_iterations: Optional[int] = None,
        deadline_sec: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> RunResult:
        """Take a ticket over, with the work that was already done.

        A resumed loop is a *new* run - the attempt counter starts at
        one again, the issue list starts empty. Two things used to be
        missing from it, and both made a takeover impossible:

        * **The deliverable of the last attempt.** The ticket stored
          it under ``last_artifact`` and nobody read it. The worker
          was therefore asked to produce the deliverable from nothing,
          and the loop never converged on the work that was already
          done. The deliverable is now handed to the worker as the
          thing to improve on.
        * **A budget of its own.** The resumed run reused the
          ceilings of the machine-wide configuration. A problem that
          could not be solved within those ceilings therefore stopped
          at exactly the same ceiling again - the takeover was a
          formality. The three ceilings can now be given for this
          run only, so a human can say "try again with more rounds"
          without changing the default for every other task.
        """
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
            # What the last attempt delivered. This is the difference
            # between taking a problem over and starting it again.
            previous_artifact=str(payload.get("last_artifact") or ""),
            max_iterations=max_iterations,
            deadline_sec=deadline_sec,
            max_tokens=max_tokens,
        )
        # A resumed loop that still cannot close must NOT look finished.
        # Marking the ticket resolved anyway would empty the human queue:
        # the escalation would disappear from `mas tickets` while the very
        # problem it was opened for is still open. The ticket stays
        # waiting_human in that case, and it carries the new failure.
        if result.closed:
            if ticket.get("state") != "resolved":
                self.tickets.resolve(ticket_id, "resumed", note)
            # The ticket must point at the run that closed it. A
            # resolved ticket that only remembers the failures sends
            # the reader back into the failed history to find out how
            # the problem was actually solved.
            self.tickets.note_success(
                ticket_id,
                run_id=result.run_id,
                iterations=int(result.iterations or 0),
                artifact=str(result.artifact or ""),
            )
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

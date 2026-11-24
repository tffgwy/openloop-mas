# -*- coding: utf-8 -*-
"""The fifty-second gate: a run knows which backend paid for it.

The gap
------
The failover chain folds the spend of every backend that failed into
the total the run is charged. That total is right - the run really
did pay for the failed calls.

But the *name* was wrong. The total used to be booked under the name
of the backend that **answered**, so a row said "backup-kimi spent 8
tokens" when backup-kimi had spent 3 and the dead primary had spent
5. The money of a dead endpoint was booked under the name of the
endpoint that answered.

That is a lie about money, and it is a lie in the direction that
costs the reader the most. The endpoint that was burning the budget
is exactly the one that failed - so a reader asking "which backend is
draining my budget?" got the healthy one instead of the sick one.

The fix
------
The chain keeps the spend of each backend apart, the ledger row keeps
it apart, and every surface reads it from the same place. The total
does not change - the run is still charged what it really cost. Only
the attribution becomes true.

This gate watches:

* the ledger splits the spend of a failed-over call by backend;
* the dead backend's spend is **not** booked under the living one;
* the run's own summary says who paid;
* the report's run detail says it in words;
* a run that never failed over claims no split at all;
* the command line agrees with the report;
* the MCP surface agrees with the report;
* and the gate proves it can fail: the **wiring** is broken on
  purpose - the engine stops carrying the split into the ledger - and
  the gate has to notice that the run can no longer say.

The break is on the wiring, not on the reader: a broken reader makes
every surface wrong in the same way, so the surfaces agree and the
gate sees nothing. The break has to make the *book* lose the fact.

Two runs are needed, and both have to be real. A gate that only ever
sees a failover can be satisfied by a filter that returns *everything*
all the time - so one run must be served by its own backend and must
see no split, and only the other must claim one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional

#: The observations the gate makes.
RUN_KNOWS_WHO_PAID_OBSERVATIONS = (
    "the ledger splits the spend by backend",
    "the dead backend's spend is not booked under the living one",
    "the run's own summary says who paid",
    "the report's run detail says it in words",
    "a run that never failed over claims no split",
    "the command line agrees with the report",
    "the MCP surface agrees with the report",
)

#: What the dead backend burns before it breaks.
DEAD_SPEND = 5
#: What the surviving backend spends on the answer.
LIVE_SPEND = 3


class _Env:
    """The environment variables the gate has to borrow.

    The restore is **absolute**: leaving the context puts back
    exactly the values that were there when it was entered - and
    removes the ones that were not. The gate enters and leaves this
    context many times per round, and a restore that only *sets* the
    variables would leave the last one behind: a self-test that
    points the machine's state directory at a temporary folder is a
    self-test that dirties the machine.
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


def _answer_text() -> str:
    """The deliverable every backend in the gate produces."""
    return "final answer line\n"


def _json_answer() -> str:
    """The review verdict every backend in the gate produces."""
    return json.dumps(
        {
            "verdict": "pass",
            "scores": [{"id": "c1", "pass": True, "note": "ok"}],
            "issues": [],
            "summary": "s",
            "plan": ["p"],
            "risks": [],
            "decision": "approve",
            "reason": "ok",
        }
    )


class _Backend:
    """One backend of the gate.

    It answers like a real provider, it says who it is, and it says
    how much it spent - including the dead one, which is the whole
    point: a backend that breaks *after* it was billed still cost the
    run money.
    """

    def __init__(self, name: str, model: str, alive: bool = True) -> None:
        from mas.providers import Provider, ProviderError

        self._provider_cls = Provider
        self._error_cls = ProviderError
        self.name = name
        self.model = model
        self.alive = alive

    def as_provider(self) -> Any:
        """A real provider object this backend answers through."""
        backend = self

        class _P(self._provider_cls):  # type: ignore[misc,valid-type]
            name = backend.name

            def __init__(self) -> None:
                super().__init__(model=backend.model)

            def complete(
                self,
                *,
                system: str = "",
                prompt: str = "",
                role: str = "worker",
                expect_json: bool = False,
                max_tokens: int = 4000,
            ) -> Dict[str, Any]:
                if not backend.alive:
                    raise backend._error_cls(
                        "502 backend %s is down" % backend.name,
                        usage={"total_tokens": DEAD_SPEND},
                    )
                usage = {"total_tokens": LIVE_SPEND}
                if role == "worker":
                    return {
                        "text": _answer_text(),
                        "usage": usage,
                        "raw": {},
                    }
                return {
                    "text": _json_answer(),
                    "usage": usage,
                    "raw": {},
                }

        return _P()


def _make_case(root: str) -> Dict[str, Any]:
    """Two closed runs: one served by its own backend, one that fell over.

    The two are needed for a reason that is easy to get wrong: a
    filter that returns *every* backend for *every* run would satisfy
    a gate that only ever looks at the failing run. So the healthy
    run has to see itself and nothing else.

    The runs are real - the engine closes them - so the run summaries
    have real closures and real artifacts.
    """
    from mas.engine import LoopEngine
    from mas.orchestration import ProviderChain

    state = pathlib.Path(root) / "state"
    artifacts = state / "artifacts"
    config_path = pathlib.Path(root) / "config.json"
    state.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    workspace = artifacts / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text(_answer_text(), encoding="utf-8")

    config = {
        "provider": "mock",
        "model": "selftest-model",
        "state_dir": str(state),
        "workspace": str(workspace),
        "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }

    healthy = _Backend("healthy-primary", "healthy-model", alive=True)
    dead = _Backend("dead-primary", "dead-model", alive=False)
    backup = _Backend("backup-kimi", "kimi-k2", alive=True)

    run_ids: List[str] = []
    with _Env(str(state), str(config_path)):
        criteria = [
            {
                "id": "c1",
                "desc": "含最终答案",
                "check": {"type": "contains", "arg": "final answer"},
            }
        ]
        # The healthy run: one backend, alive, no failover.
        engine = LoopEngine(config, provider=healthy.as_provider())
        result = engine.run(goal="写一份说明", criteria=criteria).to_dict()
        healthy_run = str(result.get("run_id") or "")
        if healthy_run:
            run_ids.append(healthy_run)

        # The failover run: the primary is down, the backup answers.
        engine = LoopEngine(
            config,
            provider=ProviderChain([dead.as_provider(), backup.as_provider()]),
        )
        result = engine.run(goal="再写一份说明", criteria=criteria).to_dict()
        failover_run = str(result.get("run_id") or "")
        if failover_run:
            run_ids.append(failover_run)

    return {
        "state_dir": str(state),
        "artifacts": str(artifacts),
        "config_path": str(config_path),
        "workspace": str(workspace),
        "healthy": run_ids[0] if len(run_ids) > 0 else "",
        "failover": run_ids[1] if len(run_ids) > 1 else "",
    }


def _run_cli(args: List[str], state_dir: str, config_path: str) -> str:
    """Run one command of the command line and keep what it printed."""
    from mas.cli import main

    buffer = io.StringIO()
    with _Env(state_dir, config_path):
        try:
            with contextlib.redirect_stdout(buffer):
                main(args)
        except SystemExit:
            pass
    return buffer.getvalue()


def _json_cli(args: List[str], state_dir: str, config_path: str) -> Dict[str, Any]:
    """Run a command that prints JSON and read it back."""
    text = _run_cli(args, state_dir, config_path)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a broken page is the subject
        return {}


def _section_of(markdown: str, run_id: str) -> str:
    """The part of the report that is about one run.

    The report is a page with a section per run, and the gate asks
    about one run at a time: "does *this* run's own section say it?".
    Reading the whole page would let the other run's words answer the
    question.
    """
    lines = (markdown or "").split("\n")
    kept: List[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = ("`%s`" % run_id) in line
            if inside:
                kept.append(line)
            continue
        if inside:
            kept.append(line)
    return "\n".join(kept)


def _run_row(report: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """The one run's row, in the report."""
    for entry in report.get("runs") or []:
        if isinstance(entry, dict) and str(entry.get("run_id") or "") == run_id:
            return entry
    return {}


def _facts_of(case: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.ledger import Ledger
    from mas.report import build_report, render_markdown, run_summaries

    state_dir = str(case.get("state_dir") or "")
    config_path = str(case.get("config_path") or "")

    ledger_path = pathlib.Path(state_dir) / "ledgers" / "runs.jsonl"
    rows: List[Dict[str, Any]] = []
    try:
        if ledger_path.exists():
            rows = list(Ledger(ledger_path).entries())
    except Exception:  # noqa: BLE001 - the ledger may not be there
        rows = []

    with _Env(state_dir, config_path):
        summaries = run_summaries(rows)
        report = build_report(rows)
        markdown = render_markdown(report)
        status_json = _json_cli(["status", "--json"], state_dir, config_path)
        status_text = _run_cli(["status"], state_dir, config_path)
        from mas.mcp_server import handle_tool

        mcp_status = handle_tool("mas_status", {})
        mcp_report = handle_tool("mas_report", {"as_markdown": False})
        mcp_replay = handle_tool("mas_replay", {})

    return {
        "healthy": str(case.get("healthy") or ""),
        "failover": str(case.get("failover") or ""),
        "rows": rows,
        "summaries": summaries,
        "report": report,
        "markdown": markdown,
        "status_json": status_json,
        "status_text": status_text,
        "mcp_status": mcp_status,
        "mcp_report": mcp_report,
        "mcp_replay": mcp_replay,
    }


def _summary_of(facts: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """One run's own summary."""
    for summary in facts.get("summaries") or []:
        if isinstance(summary, dict) and str(summary.get("run_id") or "") == run_id:
            return summary
    return {}


def _rows_of(facts: Dict[str, Any], run_id: str) -> List[Dict[str, Any]]:
    """One run's own ledger rows."""
    return [
        row
        for row in (facts.get("rows") or [])
        if isinstance(row, dict) and str(row.get("run_id") or "") == run_id
    ]


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    healthy = str(facts.get("healthy") or "")
    failover = str(facts.get("failover") or "")
    markdown = str(facts.get("markdown") or "")

    fail_summary = _summary_of(facts, failover)
    healthy_summary = _summary_of(facts, healthy)
    fail_rows = _rows_of(facts, failover)

    # The ledger splits the spend of a failed-over call by backend.
    split_rows = [
        row
        for row in fail_rows
        if isinstance(row.get("backend_tokens"), dict)
        and row.get("backend_tokens")
    ]
    split_ok = bool(split_rows) and all(
        str(row.get("provider_used") or "") == "backup-kimi"
        and int(row.get("backend_tokens", {}).get("dead-primary") or 0) == DEAD_SPEND
        and int(row.get("backend_tokens", {}).get("backup-kimi") or 0) == LIVE_SPEND
        # The split has to add up to the charge of the row. A split
        # that does not add up is a different lie.
        and sum(int(v or 0) for v in row["backend_tokens"].values())
 == int(row.get("tokens") or 0)
        for row in split_rows
    )

    # The dead backend's spend is not booked under the living one.
    # The old behaviour named only "backup-kimi" for the whole 8
    # tokens. The honest split names both, and the living backend's
    # own share is smaller than the charge.
    fail_split = fail_summary.get("spend_by_backend")
    live_share = int((fail_split or {}).get("backup-kimi") or 0)
    dead_share = int((fail_split or {}).get("dead-primary") or 0)
    fail_total = int(fail_summary.get("tokens") or 0)
    not_booked_on_living = (
        dead_share > 0
        and live_share > 0
        and live_share < fail_total
        and live_share == LIVE_SPEND * len(split_rows)
    )

    # The run's own summary says who paid.
    summary_says = (
        isinstance(fail_split, dict)
        and str(fail_split.get("backup-kimi") or "") != ""
        and int(fail_split.get("dead-primary") or 0) > 0
    )

    # The report's run detail says it in words.
    fail_section = _section_of(markdown, failover)
    detail_says = (
        "花费" in fail_section
        and "dead-primary" in fail_section
        and "backup-kimi" in fail_section
    )

    # A run that never failed over claims no split.
    healthy_rows = _rows_of(facts, healthy)
    healthy_section = _section_of(markdown, healthy)
    healthy_clean = (
        not (healthy_summary.get("spend_by_backend") or {})
        and not any(row.get("backend_tokens") for row in healthy_rows)
        and "💸" not in healthy_section
        and "dead-primary" not in healthy_section
    )

    # The command line agrees with the report.
    cli_backends = (facts.get("status_json") or {}).get("backends") or {}
    cli_items = [
        item
        for item in (cli_backends.get("items") or [])
        if isinstance(item, dict)
        and str(item.get("run_id") or "") == failover
    ]
    cli_agrees = bool(cli_items) and (
        int((cli_items[0] or {}).get("spend_by_backend", {}).get("dead-primary") or 0)
        > 0
        and int(
            (cli_items[0] or {}).get("spend_by_backend", {}).get("backup-kimi")
            or 0
        )
        > 0
        and "dead-primary" in str(facts.get("status_text") or "")
    )

    # The MCP surface agrees with the report.
    mcp_status_backends = (facts.get("mcp_status") or {}).get("backends") or {}
    mcp_items = [
        item
        for item in (mcp_status_backends.get("items") or [])
        if isinstance(item, dict)
        and str(item.get("run_id") or "") == failover
    ]
    mcp_report = facts.get("mcp_report") or {}
    mcp_row = _run_row(mcp_report, failover)
    mcp_replay = facts.get("mcp_replay") or {}
    mcp_block = {}
    for block in mcp_replay.get("runs") or []:
        if isinstance(block, dict) and str(block.get("run_id") or "") == failover:
            mcp_block = block
    mcp_agrees = (
        bool(mcp_items)
        and int(
            (mcp_items[0] or {}).get("spend_by_backend", {}).get("dead-primary")
            or 0
        )
        > 0
        and int(
            (mcp_row.get("spend_by_backend") or {}).get("dead-primary") or 0
        )
        > 0
        and int(
            (mcp_block.get("spend_by_backend") or {}).get("dead-primary") or 0
        )
        > 0
    )

    return {
        "the ledger splits the spend by backend": split_ok,
        "the dead backend's spend is not booked under the living one": (
            not_booked_on_living
        ),
        "the run's own summary says who paid": summary_says,
        "the report's run detail says it in words": detail_says,
        "a run that never failed over claims no split": healthy_clean,
        "the command line agrees with the report": cli_agrees,
        "the MCP surface agrees with the report": mcp_agrees,
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw rows, not from the pages.

    This pass does not look at what the pages printed. It reads the
    ledger and works out the truth - how much each backend paid for
    the run - and then says what every surface ought to report. If the
    two readings disagree, the gate says so - and the gate cannot be
    satisfied by a surface that merely looks right.
    """
    from mas.report import CHARGE_ROLES, spend_by_backend_of_run

    healthy = str(facts.get("healthy") or "")
    failover = str(facts.get("failover") or "")

    fail_rows = _rows_of(facts, failover)
    healthy_rows = _rows_of(facts, healthy)

    truth_fail = spend_by_backend_of_run(fail_rows)
    truth_healthy = spend_by_backend_of_run(healthy_rows)

    # The truth of the failed-over run: both backends paid, and the
    # charge of the charged rows adds up to the split.
    charged = [
        row
        for row in fail_rows
        if str(row.get("role", "")) in CHARGE_ROLES
    ]
    total_charge = sum(int(row.get("tokens") or 0) for row in charged)
    split_total = sum(int(v or 0) for v in truth_fail.values())
    fail_ok = (
        int(truth_fail.get("dead-primary") or 0) > 0
        and int(truth_fail.get("backup-kimi") or 0) > 0
        and split_total == total_charge
    )

    # The truth of the healthy run: it paid its own backend and
    # nothing else, and the split names that backend.
    healthy_ok = (
        not truth_healthy
        or (
            set(truth_healthy) == {"healthy-primary"}
            and sum(int(v or 0) for v in truth_healthy.values())
            == sum(
                int(row.get("tokens") or 0)
                for row in healthy_rows
                if str(row.get("role", "")) in CHARGE_ROLES
            )
        )
    )

    return {
        "the ledger splits the spend by backend": fail_ok,
        "the dead backend's spend is not booked under the living one": (
            fail_ok
        ),
        "the run's own summary says who paid": fail_ok,
        "the report's run detail says it in words": fail_ok,
        "a run that never failed over claims no split": healthy_ok,
        "the command line agrees with the report": fail_ok,
        "the MCP surface agrees with the report": fail_ok,
    }


def check_a_run_knows_who_paid(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-second gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _facts_of(
                    _make_case(tempfile.mkdtemp(prefix="mas-selftest-paid-"))
                )
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in RUN_KNOWS_WHO_PAID_OBSERVATIONS:
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
            # carrying the split into the ledger. It is not the same
            # as breaking a shared reader - a broken reader makes
            # every surface wrong in the same way, so the surfaces
            # agree and the gate sees nothing. The break has to make
            # the *book* lose the fact: the run can no longer say
            # which backend paid.
            import mas.engine as engine_module

            real_spend_fields = engine_module.LoopEngine._spend_fields

            def _no_spend_fields(usage, _real=real_spend_fields):
                # Exactly what the code did before this release: the
                # split never reached the ledger, so the run could
                # not tell which backend had paid.
                return {}

            try:
                engine_module.LoopEngine._spend_fields = staticmethod(
                    _no_spend_fields
                )
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            _make_case(
                                tempfile.mkdtemp(prefix="mas-selftest-paid-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                # The original was a static method; putting the plain
                # function back would turn it into an instance method
                # and break every later call. The restore has to keep
                # the shape it had.
                engine_module.LoopEngine._spend_fields = staticmethod(
                    real_spend_fields
                )

            if broken.get("the ledger splits the spend by backend"):
                failures.append(
                    "round %d: the gate did not notice that the run stops "
                    "carrying its spend into the ledger - it cannot fail, "
                    "so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "a_run_knows_who_paid",
        "title": "运行自己知道这次的钱是哪个后端花的",
        "skipped": False,
        "invariant": (
            "首选后端挂掉、备用后端接管之后，这次运行必须自己知道："
            "死掉的后端烧了多少、活着的后端烧了多少；账本、运行详情、"
            "命令行与 MCP 得说同一件事，而没有发生切换的运行不能凭空"
            "出现花费拆分"
        ),
        "detail": (
            "切换链把每个后端的花费分开记账：总额不变（运行确实付了"
            "失败那次的錢），归属变真（死掉的钱记在死掉的后端头上）"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_run_knows_who_paid.CHECK_NAME = (  # type: ignore[attr-defined]
    "a_run_knows_who_paid"
)

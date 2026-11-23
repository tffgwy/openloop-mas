# -*- coding: utf-8 -*-
"""The fifty-first gate: a run knows which backend really delivered it.

The gap
------
The project promises a loop that keeps working when one endpoint
breaks - "a loop that dies because one endpoint returned an error is
not a closed loop, it is an outage". The failover machinery was
there: ``ProviderChain`` tries the primary, falls over to the backup
and answers.

But nobody could see it happen.

The ledger wrote ``provider`` from ``self.provider.name``, and for a
chain that name is the **primary** - the endpoint that had just
answered with a 502. So every row of a run that was actually
delivered by the backup said the dead primary served it, and the
report - which had no notion of a backend at all - repeated it.

The reader of the report could not tell that the machine had survived
an outage on its own. Worse, they could not tell that it had *not*:
the same words appeared for a run served by the primary and a run
served by a backup.

The fix
------
The row keeps the *configured* backend (``provider`` / ``model``,
unchanged, so old ledgers keep their meaning) and gains the backend
that actually answered (``provider_used`` / ``model_used``). The
engine reads it from the answer the chain produced; the report reads
it from the rows; the command line and the MCP surface read it from
the same place. One reading, four surfaces.

An old row carries no backend at all, and nothing is guessed for it:
a run from before this release says nothing about its backend rather
than claiming the configured one answered it.

This gate watches:

* the run summary knows which backend delivered it;
* the report's run table shows it;
* the run's own detail says it in words - and says that the primary
  was down when the run fell over;
* a run that never fell over does **not** claim it did;
* the command line agrees with the report;
* the MCP surface agrees with the report;
* and the gate proves it can fail: the **wiring** is broken on
  purpose - the engine stops carrying the answering backend into the
  ledger - and the gate has to notice that the run can no longer tell.

The break is on the wiring, not on a shared helper: a broken helper
makes every surface wrong in the same way, so the surfaces agree and
the gate sees nothing. The break has to make one surface read
something different.

Two runs are needed, and both have to be real. A gate that only ever
sees a failover can be satisfied by a filter that returns *everything*
all the time - so one run must be served by its own backend and must
see no failover, and only the other must claim one.
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
RUN_KNOWS_ITS_BACKEND_OBSERVATIONS = (
    "the run knows which backend delivered it",
    "the run table shows the backend",
    "the run's own detail says it in words",
    "a run that never fell over does not claim it did",
    "the command line agrees with the report",
    "the MCP surface agrees with the report",
)


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

    It answers like a real provider and it says who it is - the name
    the ledger must learn.
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
                        usage={"total_tokens": 5},
                    )
                usage = {"total_tokens": 3}
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


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    healthy = str(facts.get("healthy") or "")
    failover = str(facts.get("failover") or "")
    markdown = str(facts.get("markdown") or "")

    fail_summary = _summary_of(facts, failover)
    healthy_summary = _summary_of(facts, healthy)

    # The run knows which backend delivered it.
    knows = (
        str(fail_summary.get("backend_used") or "") == "backup-kimi"
        and str(fail_summary.get("model_used") or "") == "kimi-k2"
        and str(fail_summary.get("configured_provider") or "")
        == "dead-primary"
    )

    # The table shows it.
    fail_row = _run_row(facts.get("report") or {}, failover)
    table_shows = (
        str(fail_row.get("backend_used") or "") == "backup-kimi"
        and bool(fail_row.get("failed_over"))
        and "后端" in markdown
    )

    # The run's own section says it in words.
    fail_section = _section_of(markdown, failover)
    detail_says = (
        "backup-kimi" in fail_section
        and "实际交付后端" in fail_section
        and "不可用" in fail_section
    )

    # The healthy run never fell over, and must not say it did.
    healthy_section = _section_of(markdown, healthy)
    healthy_row = _run_row(facts.get("report") or {}, healthy)
    healthy_clean = (
        str(healthy_summary.get("backend_used") or "") == "healthy-primary"
        and not healthy_summary.get("failed_over")
        and not healthy_row.get("failed_over")
        and "实际交付后端" not in healthy_section
        and "backup-kimi" not in healthy_section
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
        str((cli_items[0] or {}).get("backend_used") or "") == "backup-kimi"
        and bool((cli_items[0] or {}).get("failed_over"))
        and "backup-kimi" in str(facts.get("status_text") or "")
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
        and str((mcp_items[0] or {}).get("backend_used") or "")
        == "backup-kimi"
        and bool((mcp_items[0] or {}).get("failed_over"))
        and str(mcp_row.get("backend_used") or "") == "backup-kimi"
        and bool(mcp_row.get("failed_over"))
        and str(mcp_block.get("backend_used") or "") == "backup-kimi"
        and bool(mcp_block.get("failed_over"))
    )

    return {
        "the run knows which backend delivered it": knows,
        "the run table shows the backend": table_shows,
        "the run's own detail says it in words": detail_says,
        "a run that never fell over does not claim it did": healthy_clean,
        "the command line agrees with the report": cli_agrees,
        "the MCP surface agrees with the report": mcp_agrees,
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw rows, not from the pages.

    This pass does not look at what the pages printed. It reads the
    ledger and works out the truth - which backend answered each run,
    and which run had to fall over - and then says what every surface
    ought to report. If the two readings disagree, the gate says so -
    and the gate cannot be satisfied by a surface that merely looks
    right.
    """
    from mas.report import backends_of_run

    healthy = str(facts.get("healthy") or "")
    failover = str(facts.get("failover") or "")
    rows = facts.get("rows") or []

    fail_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("run_id") or "") == failover
    ]
    healthy_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("run_id") or "") == healthy
    ]
    fail_facts = backends_of_run(fail_rows)
    healthy_facts = backends_of_run(healthy_rows)

    truth_fail = (
        str(
            (fail_facts.get("primary_backend") or {}).get("provider_used") or ""
        )
        == "backup-kimi"
        and bool(fail_facts.get("failed_over"))
    )
    truth_healthy = (
        str(
            (healthy_facts.get("primary_backend") or {}).get("provider_used")
            or ""
        )
        == "healthy-primary"
        and not healthy_facts.get("failed_over")
    )

    return {
        "the run knows which backend delivered it": truth_fail,
        "the run table shows the backend": truth_fail,
        "the run's own detail says it in words": truth_fail,
        "a run that never fell over does not claim it did": truth_healthy,
        "the command line agrees with the report": truth_fail,
        "the MCP surface agrees with the report": truth_fail,
    }


def check_a_run_knows_its_backend(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-first gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _facts_of(
                    _make_case(tempfile.mkdtemp(prefix="mas-selftest-backend-"))
                )
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in RUN_KNOWS_ITS_BACKEND_OBSERVATIONS:
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
            # carrying the answering backend into the ledger. It is
            # not the same as breaking a shared helper - a broken
            # shared helper makes every surface wrong in the same
            # way, so the surfaces agree and the gate sees nothing.
            # The break has to make one surface read something
            # different: here the run's own summary goes back to
            # saying nothing about the backend that delivered it.
            import mas.engine as engine_module

            real_backend_fields = engine_module.LoopEngine._backend_fields

            def _no_backend(source, _real=real_backend_fields):
                # Exactly what the code did before this release: the
                # answering backend never reached the ledger, so the
                # run could not tell who had delivered it.
                return {}

            try:
                engine_module.LoopEngine._backend_fields = staticmethod(
                    _no_backend
                )
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            _make_case(
                                tempfile.mkdtemp(prefix="mas-selftest-backend-broken-")
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
                engine_module.LoopEngine._backend_fields = staticmethod(
                    real_backend_fields
                )

            if broken.get("the run knows which backend delivered it"):
                failures.append(
                    "round %d: the gate did not notice that the run stops "
                    "carrying its backend into the ledger - it cannot fail, "
                    "so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "a_run_knows_its_backend",
        "title": "运行自己知道是哪个后端真正交付了它",
        "skipped": False,
        "invariant": (
            "首选后端挂掉、备用后端接管之后，这次运行必须自己知道："
            "实际交付它的是哪个后端、首选当时不可用；报告表、运行详情、"
            "命令行与 MCP 得说同一件事，而没有发生切换的运行不能谎称切换过"
        ),
        "detail": (
            "账本每行同时记下「配置的后端」与「真正应答的后端」，"
            "报告、命令行、MCP 共用同一套读法；旧账本没有后端字段时"
            "宁可不说，也不猜"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_run_knows_its_backend.CHECK_NAME = (  # type: ignore[attr-defined]
    "a_run_knows_its_backend"
)

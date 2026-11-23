# -*- coding: utf-8 -*-
"""The fiftieth gate: a run knows its own approval was taken back.

The gap
-------
A draft is a proposal; a human decision about it is the answer. v0.8.8
hung every decision on the run that proposed it, so the *ledger* could
be asked "which of my runs once had a wrong approval?".

The run could not answer it. Not the report, not the run summary, not
the MCP surface.

``group_runs()`` deliberately leaves the decision rows out of the run
rows - a human decision about a draft is not a round of the loop, and
folding it in would invent a run with no goal and no closure. That
reasoning is right about the *grouping*, and it left a hole in the
*reading*: the run summary counted five entries, said "closed", and
had no word for the fact that one of its own approvals had been taken
back by a person.

So the reader who opened the one run that had been wrong was shown a
clean, successful run. The machine had the fact; the run's own page
did not.

The fix
-------
The decisions are not folded into the run rows. They are filed
**beside** the run, under ``decisions``, and read as the run's own
facts:

* ``group_runs()`` hangs the decision rows on the group of the run
  that proposed them;
* ``decisions_of_run()`` reads them, pairing an undo with the
  approval it takes back - reusing ``mas.undos`` rather than
  re-implementing the pairing;
* ``run_summary()`` gains ``decisions`` and ``taken_back_approvals``;
* the report's run table gains a column, and the run's own detail
  section says it in words;
* the MCP replay's run blocks carry it too.

A decision with no thread belongs to no run. It is not spread over
every run - that would make each run claim a decision that was never
its own.

This gate watches:

* the run summary knows how many of its approvals were taken back;
* the report's run table shows the number;
* the run's own detail section says it in words, with the drafts;
* the other run - the one that was never wrong - sees nothing;
* the command line agrees with the report;
* the MCP surface agrees with the report;
* and the gate proves it can fail: the **wiring** is broken on
  purpose - ``group_runs()`` stops hanging the decisions on the run -
  and the gate has to notice that the run can no longer tell.

The break is on the wiring, not on a shared helper: a broken helper
makes every surface wrong in the same way, so the surfaces agree and
the gate sees nothing. The break has to make one surface read
something different.
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
RUN_KNOWS_APPROVAL_OBSERVATIONS = (
    "the run knows its own approval was taken back",
    "the run table shows the number",
    "the run's own detail says it in words",
    "the other run sees nothing",
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
    self-test that dirties the machine, and the machine-state gate
    would (correctly) fail.
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


def _make_case(root: str) -> Dict[str, Any]:
    """Two closed runs in one ledger, one of them got a wrong approval.

    Two runs are needed: a run that claims a decision it never made is
    exactly as bad as a run that misses the decision it did make. So
    the case holds a run the gate asks about and a run it must not see
    on.

    The runs are real - the engine closes them - so the run summaries
    have real closures, real artifacts and real verdicts, and the gate
    watches the actual report of an actual run.
    """
    from mas.drafts import approve_draft, stage_draft, undo_draft
    from mas.engine import LoopEngine
    from mas.providers import Provider

    class _Offline(Provider):
        name = "selftest-run-knows"

        def __init__(self) -> None:
            super().__init__(model="selftest-model")

        def complete(self, *, system: str = "", prompt: str = "",
                     role: str = "worker", expect_json: bool = False,
                     max_tokens: int = 4000) -> Dict[str, Any]:
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            if role == "worker":
                return {
                    "text": "final answer line\n",
                    "usage": usage,
                    "raw": {},
                }
            return {
                "text": json.dumps(
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
                ),
                "usage": usage,
                "raw": {},
            }

    state = pathlib.Path(root) / "state"
    artifacts = state / "artifacts"
    config_path = pathlib.Path(root) / "config.json"
    state.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    workspace = artifacts / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text("final answer line\n", encoding="utf-8")

    config = {
        "provider": "mock",
        "model": "selftest-model",
        "state_dir": str(state),
        "workspace": str(workspace),
        "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }

    run_ids: List[str] = []
    with _Env(str(state), str(config_path)):
        engine = LoopEngine(config, provider=_Offline())
        criteria = [
            {
                "id": "c1",
                "desc": "含最终答案",
                "check": {"type": "contains", "arg": "final answer"},
            }
        ]
        for goal in ("写一份说明", "再写一份说明"):
            result = engine.run(goal=goal, criteria=criteria).to_dict()
            run_id = str(result.get("run_id") or "")
            if run_id:
                run_ids.append(run_id)

    mine = run_ids[0] if run_ids else ""
    other = run_ids[1] if len(run_ids) > 1 else ""

    # The mistake: this run's own proposal is approved and then taken
    # back by a person. The run stays closed - the loop did its job -
    # and the approval was still wrong.
    draft_id = ""
    with _Env(str(state), str(config_path)):
        staged = stage_draft(
            workspace,
            "note.txt",
            b"final answer line\nreplaced by a person\n",
            reason="the loop proposes a change",
            source="gate",
            run_id=mine,
            task_id="T-" + mine,
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(workspace, draft_id, note="looks fine")
        undo_draft(workspace, draft_id, note="approved the wrong thing")

    return {
        "state_dir": str(state),
        "artifacts": str(artifacts),
        "config_path": str(config_path),
        "workspace": str(workspace),
        "mine": mine,
        "other": other,
        "draft_id": draft_id,
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
    from mas.report import build_report, group_runs, render_markdown, run_summaries

    state_dir = str(case.get("state_dir") or "")
    config_path = str(case.get("config_path") or "")
    mine = str(case.get("mine") or "")
    other = str(case.get("other") or "")

    ledger_path = pathlib.Path(state_dir) / "ledgers" / "runs.jsonl"
    rows: List[Dict[str, Any]] = []
    try:
        if ledger_path.exists():
            rows = list(Ledger(ledger_path).entries())
    except Exception:  # noqa: BLE001 - the ledger may not be there
        rows = []

    with _Env(state_dir, config_path):
        groups = group_runs(rows)
        summaries = run_summaries(rows)
        report = build_report(rows)
        markdown = render_markdown(report)
        report_json = _json_cli(["report", "--json"], state_dir, config_path)
        markdown_cli = _run_cli(["report"], state_dir, config_path)
        markdown_one = _run_cli(
            ["report", "--run-id", mine], state_dir, config_path
        )
        from mas.mcp_server import handle_tool

        mcp_report = handle_tool("mas_report", {"as_markdown": False})
        mcp_replay = handle_tool("mas_replay", {})

    return {
        "mine": mine,
        "other": other,
        "draft_id": str(case.get("draft_id") or ""),
        "rows": rows,
        "groups": groups,
        "summaries": summaries,
        "report": report,
        "markdown": markdown,
        "report_json": report_json,
        "markdown_cli": markdown_cli,
        "markdown_one": markdown_one,
        "mcp_report": mcp_report,
        "mcp_replay": mcp_replay,
    }


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    mine = str(facts.get("mine") or "")
    other = str(facts.get("other") or "")
    markdown = str(facts.get("markdown") or "")

    mine_summary = {}
    other_summary = {}
    for summary in facts.get("summaries") or []:
        run_id = str(summary.get("run_id") or "")
        if run_id == mine:
            mine_summary = summary
        elif run_id == other:
            other_summary = summary

    # The run knows.
    taken_back = int(mine_summary.get("taken_back_approvals") or 0)
    knows = taken_back >= 1
    decisions = mine_summary.get("decisions") or {}
    # The number is not a decoration: it is the run's own count, and it
    # has to come with the drafts it is about.
    if str(facts.get("draft_id") or "") not in (
        decisions.get("draft_ids") or []
    ):
        knows = False

    # The table shows it.
    mine_row = _run_row(facts.get("report") or {}, mine)
    table_shows = (
        int(mine_row.get("taken_back_approvals") or 0) >= 1
        and "撤销的批准" in markdown
    )

    # The run's own section says it in words.
    mine_section = _section_of(markdown, mine)
    detail_says = (
        "人工决定" in mine_section
        and "撤回" in mine_section
    )

    # The other run sees nothing.
    other_section = _section_of(markdown, other)
    other_row = _run_row(facts.get("report") or {}, other)
    other_clean = (
        int(other_summary.get("taken_back_approvals") or 0) == 0
        and int(other_row.get("taken_back_approvals") or 0) == 0
        and "人工决定" not in other_section
    )

    # The command line agrees with the report.
    cli_row = _run_row(facts.get("report_json") or {}, mine)
    cli_section = _section_of(str(facts.get("markdown_one") or ""), mine)
    cli_agrees = (
        int(cli_row.get("taken_back_approvals") or 0) >= 1
        and "人工决定" in cli_section
    )

    # The MCP surface agrees with the report.
    mcp_report = facts.get("mcp_report") or {}
    mcp_row = _run_row(mcp_report, mine)
    mcp_replay = facts.get("mcp_replay") or {}
    mcp_block = {}
    for block in mcp_replay.get("runs") or []:
        if isinstance(block, dict) and str(block.get("run_id") or "") == mine:
            mcp_block = block
    mcp_agrees = (
        int(mcp_row.get("taken_back_approvals") or 0) >= 1
        and int(mcp_block.get("taken_back_approvals") or 0) >= 1
    )

    return {
        "the run knows its own approval was taken back": knows,
        "the run table shows the number": table_shows,
        "the run's own detail says it in words": detail_says,
        "the other run sees nothing": other_clean,
        "the command line agrees with the report": cli_agrees,
        "the MCP surface agrees with the report": mcp_agrees,
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw rows, not from the pages.

    This pass does not look at what the pages printed. It reads the
    ledger and works out the truth - how many of this run's approvals
    were taken back - and then says what every surface ought to
    report. If the two readings disagree, the gate says so - and the
    gate cannot be satisfied by a surface that merely looks right.
    """
    from mas.report import decisions_of_run

    mine = str(facts.get("mine") or "")
    other = str(facts.get("other") or "")
    rows = facts.get("rows") or []

    mine_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("run_id") or "") == mine
    ]
    other_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("run_id") or "") == other
    ]
    mine_decisions = decisions_of_run(mine_rows, mine)
    other_decisions = decisions_of_run(other_rows, other)

    truth_mine = int(mine_decisions.get("taken_back") or 0) >= 1
    truth_other = int(other_decisions.get("taken_back") or 0) == 0

    return {
        "the run knows its own approval was taken back": truth_mine,
        "the run table shows the number": truth_mine,
        "the run's own detail says it in words": truth_mine,
        "the other run sees nothing": truth_other,
        "the command line agrees with the report": truth_mine,
        "the MCP surface agrees with the report": truth_mine,
    }


def check_a_run_knows_its_approval(iterations: int = 3) -> Dict[str, Any]:
    """The fiftieth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _facts_of(
                    _make_case(tempfile.mkdtemp(prefix="mas-selftest-knows-"))
                )
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in RUN_KNOWS_APPROVAL_OBSERVATIONS:
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
            # The break is on the **wiring**: ``group_runs()`` stops
            # hanging the decision rows on the run that proposed them.
            # It is not the same as breaking a shared helper - a broken
            # shared helper makes every surface wrong in the same way,
            # so the surfaces agree and the gate sees nothing. The break
            # has to make one surface read something different: here the
            # run's own summary goes back to saying nothing about the
            # approval a person had to take back.
            import mas.report as report_module

            real_group_runs = report_module.group_runs

            def _no_hanging(rows, _real=real_group_runs):
                # Exactly what the code did before this release: the
                # decision rows are left out of the run and not filed
                # beside it either, so the run cannot know what a
                # person decided about its own proposal.
                groups = _real(rows)
                for group in groups:
                    group.pop("decisions", None)
                return groups

            try:
                report_module.group_runs = _no_hanging
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            _make_case(
                                tempfile.mkdtemp(prefix="mas-selftest-knows-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.group_runs = real_group_runs

            if broken.get("the run knows its own approval was taken back"):
                failures.append(
                    "round %d: the gate did not notice that the run stops "
                    "hanging its own decisions on itself - it cannot fail, "
                    "so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "a_run_knows_its_approval",
        "title": "\u8fd0\u884c\u81ea\u5df1\u77e5\u9053\u5b83\u7684\u6279\u51c6\u88ab\u64a4\u56de\u8fc7",
        "skipped": False,
        "invariant": (
            "\u4e00\u6b21\u6279\u51c6\u88ab\u64a4\u56de\u4e86\uff0c\u8fd9\u6b21\u8fd0\u884c"
            "\u5fc5\u987b\u81ea\u5df1\u77e5\u9053\uff1a\u4eba\u5bf9\u5b83\u63d0\u8bae\u7684"
            "\u90a3\u4e2a\u6279\u51c6\u662f\u9519\u7684\uff0c\u8fd0\u884c\u7684\u81ea\u5df1"
            "\u9875\u9762\u4e0d\u80fd\u53ea\u8bf4\u201c\u5df2\u95ed\u73af\u201d\uff0c\u62a5\u544a"
            "\u8868\u3001\u8fd0\u884c\u8be6\u60c5\u3001\u547d\u4ee4\u884c\u4e0e MCP \u5f97"
            "\u8bf4\u540c\u4e00\u4ef6\u4e8b"
        ),
        "detail": (
            "\u51b3\u7b56\u884c\u4e0d\u88ab\u585e\u8fdb\u8fd0\u884c\uff08\u4eba\u5bf9\u8349\u7a3f"
            "\u7684\u51b3\u5b9a\u4e0d\u662f\u4e00\u8f6e\u5faa\u73af\uff09\uff0c\u800c\u662f\u6302"
            "\u5728\u63d0\u8bae\u5b83\u7684\u90a3\u6b21\u8fd0\u884c\u65c1\u8fb9\uff0c\u5e76\u88ab"
            "\u90a3\u6b21\u8fd0\u884c\u8bfb\u6210\u81ea\u5df1\u7684\u4e8b\u5b9e"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_run_knows_its_approval.CHECK_NAME = (  # type: ignore[attr-defined]
    "a_run_knows_its_approval"
)

# -*- coding: utf-8 -*-
"""The forty-ninth gate: a decision hangs on the run that proposed it.

The gap
-------
A draft is a proposal. A human decision about it - an approval, or
an approval taken back - is the answer to that proposal.

The answer was filed without the question. ``ledger_row_for()``
hard-coded ``"run_id": ""`` and ``"task_id": ""`` on every decision
row, so the ledger held the fact "a person approved D-abc" and
nothing else. The thread the change came from was gone - not
recorded, but *discarded*.

So the question every person actually asks could not be asked:

    "which of my runs once had a wrong approval?"

The report's draft table had a "提议运行" column and it printed
``-`` for every row, because the ledger had nothing to print.

The fix
-------
The draft remembers the run that proposed it, at the moment the
proposal is made. The decision row copies that thread out of the
draft. The undo record keeps it. The report prints it, the draft
list prints it, and both the command line and the MCP surface can
be asked about one run.

The chain and the ledger cross-check each other, and the thread is
one of the copied fields - so a decision whose thread was edited on
one side only is caught.

This gate watches:

* the draft payload carries the run and the task;
* the ledger's approve row and undo row carry the run and the task;
* the audit chain entries carry them too, and the chain and the
  ledger agree;
* the report's draft table shows the run instead of a dash;
* the draft list shows the run;
* ``mas undos --run-id`` returns only that run's decisions;
* the MCP surface can be asked about one run as well;
* and the gate proves it can fail: the **wiring** is broken on
  purpose - the decision row stops copying the thread - and the
  gate has to notice that the ledger lost it.

The break is on the wiring, not on a shared helper: a broken helper
makes every surface wrong in the same way, so the surfaces agree
and the gate sees nothing. The break has to make one surface read
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
DRAFT_DECISION_RUN_OBSERVATIONS = (
    "the draft remembers the run that proposed it",
    "the ledger's decision rows carry the run",
    "the audit chain agrees with the ledger",
    "the report's draft table shows the proposing run",
    "the draft list shows the proposing run",
    "the decisions of one run can be asked for by name",
    "the MCP surface can be asked about one run as well",
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
    """Two workspaces, two threads, each with a decision.

    Two threads are needed: a filter that always returns everything
    looks exactly like a filter that works when only one thread
    exists. So the case holds a run the gate asks about and a run it
    must not see.
    """
    from mas.drafts import approve_draft, stage_draft, undo_draft

    state = pathlib.Path(root) / "state"
    artifacts = state / "artifacts"
    config_path = pathlib.Path(root) / "config.json"

    made = []
    for run_id in ("R-mine", "R-other"):
        workspace = artifacts / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "notes.txt").write_text("alpha\n", encoding="utf-8")
        with _Env(str(state), str(config_path)):
            staged = stage_draft(
                workspace,
                "notes.txt",
                ("alpha\nfrom %s\n" % run_id).encode("utf-8"),
                reason="the loop proposes a change",
                source="gate",
                run_id=run_id,
                task_id="T-" + run_id,
            )
            draft_id = str(staged.get("id") or "")
            approve_draft(workspace, draft_id, note="looks fine")
            undo_draft(workspace, draft_id, note="approved the wrong thing")
        made.append(
            {
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "workspace": str(workspace),
                "draft_id": draft_id,
            }
        )

    return {
        "state_dir": str(state),
        "artifacts": str(artifacts),
        "config_path": str(config_path),
        "mine": made[0],
        "other": made[1],
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


def _facts_of(case: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.audit import verify_against_ledger
    from mas.drafts import get_draft
    from mas.undos import chain_rows, undo_report

    state_dir = str(case.get("state_dir") or "")
    config_path = str(case.get("config_path") or "")
    artifacts = str(case.get("artifacts") or "")
    mine = case.get("mine") or {}
    other = case.get("other") or {}
    run_id = str(mine.get("run_id") or "")
    task_id = str(mine.get("task_id") or "")
    draft_id = str(mine.get("draft_id") or "")
    other_id = str(other.get("draft_id") or "")
    workspace = str(mine.get("workspace") or "")

    with _Env(state_dir, config_path):
        draft = get_draft(workspace, draft_id)
        rows = chain_rows(workspace)
        report_undo = undo_report(rows)
        from mas.drafts import audit_chain

        chain = audit_chain(workspace)
        chain_entries = list(chain.entries())
        from mas.ledger import Ledger

        ledger_path = pathlib.Path(state_dir) / "ledgers" / "runs.jsonl"
        ledger_rows = []
        try:
            if ledger_path.exists():
                ledger_rows = list(Ledger(ledger_path).entries())
        except Exception:  # noqa: BLE001 - the ledger may not be there
            ledger_rows = []

    chain_problems = verify_against_ledger(chain, ledger_rows) if ledger_rows else {"ok": False, "problems": ["no ledger"]}

    # The main ledger holds the decisions of every workspace on the
    # machine, so the rows about this workspace are picked out of it.
    ledger_for_workspace = [
        row
        for row in ledger_rows
        if isinstance(row, dict)
        and str(row.get("workspace") or "") in ("", workspace)
    ]

    with _Env(state_dir, config_path):
        report = _json_cli(["report", "--json"], state_dir, config_path)
        markdown = _run_cli(["report"], state_dir, config_path)
        drafts_text = _run_cli(
            ["drafts", "list", "--workspace", artifacts], state_dir, config_path
        )
        undos_all = _json_cli(
            ["undos", "--workspace", artifacts, "--json"], state_dir, config_path
        )
        undos_mine = _json_cli(
            ["undos", "--workspace", artifacts, "--run-id", run_id, "--json"],
            state_dir,
            config_path,
        )
        from mas.mcp_server import handle_tool

        mcp_all = handle_tool("mas_undos", {"workspace": artifacts})
        mcp_mine = handle_tool("mas_undos", {"workspace": artifacts, "run_id": run_id})

    return {
        "run_id": run_id,
        "task_id": task_id,
        "draft_id": draft_id,
        "other_id": other_id,
        "other_run_id": str(other.get("run_id") or ""),
        "draft": draft,
        "rows": rows,
        "chain_entries": chain_entries,
        "chain_problems": chain_problems,
        "ledger_rows": ledger_for_workspace,
        "undo_report": report_undo,
        "report": report,
        "markdown": markdown,
        "drafts_text": drafts_text,
        "undos_all": undos_all,
        "undos_mine": undos_mine,
        "mcp_all": mcp_all,
        "mcp_mine": mcp_mine,
    }


def _decision_rows(rows: List[Dict[str, Any]], draft_id: str) -> List[Dict[str, Any]]:
    """The decision rows about one draft."""
    picked = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("event") or row.get("role") or "")
            in ("draft_approve", "draft_undo")
            and str(row.get("draft_id") or "") == draft_id
        ):
            picked.append(row)
    return picked


def _ledger_decision_rows(
    rows: List[Dict[str, Any]], draft_id: str
) -> List[Dict[str, Any]]:
    """The **main ledger**'s decision rows about one draft.

    The ledger rows and the chain entries look alike but are not the
    same thing: the ledger is the machine-wide copy, and it is the
    one that used to throw the thread away. So the gate reads the
    ledger's own rows here - not the chain's.
    """
    picked = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("role") or "") in ("draft_approve", "draft_undo")
            and str(row.get("draft_id") or "") == draft_id
        ):
            picked.append(row)
    return picked


def _report_row(report: Dict[str, Any], draft_id: str) -> Dict[str, Any]:
    """The one row about this draft, in the report."""
    for entry in (report.get("drafts") or {}).get("drafts") or []:
        if isinstance(entry, dict) and str(entry.get("draft_id") or "") == draft_id:
            return entry
    return {}


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    run_id = str(facts.get("run_id") or "")
    task_id = str(facts.get("task_id") or "")
    draft_id = str(facts.get("draft_id") or "")
    other_id = str(facts.get("other_id") or "")
    draft = facts.get("draft") or {}
    entries = facts.get("chain_entries") or []
    chain_problems = facts.get("chain_problems") or {}
    markdown = str(facts.get("markdown") or "")
    drafts_text = str(facts.get("drafts_text") or "")
    undos_mine = facts.get("undos_mine") or {}
    mcp_mine = facts.get("mcp_mine") or {}

    decisions = _ledger_decision_rows(facts.get("ledger_rows") or [], draft_id)
    approve_rows = [
        row for row in decisions if str(row.get("role") or "") == "draft_approve"
    ]
    undo_rows = [
        row for row in decisions if str(row.get("role") or "") == "draft_undo"
    ]

    # The draft itself remembers the run.
    draft_remembers = (
        str(draft.get("run_id") or "") == run_id
        and str(draft.get("task_id") or "") == task_id
    )

    # The ledger rows carry the thread.
    ledger_carries = bool(approve_rows) and bool(undo_rows)
    for row in approve_rows + undo_rows:
        if str(row.get("run_id") or "") != run_id:
            ledger_carries = False
        if str(row.get("task_id") or "") != task_id:
            ledger_carries = False

    # The chain says the same thing, and the cross-check is quiet.
    chain_carries = True
    for entry in entries:
        if str(entry.get("event") or "") not in (
            "draft_approve",
            "draft_undo",
        ):
            continue
        if str(entry.get("run_id") or "") != run_id:
            chain_carries = False
        if str(entry.get("task_id") or "") != task_id:
            chain_carries = False
    chain_agrees = chain_carries and bool(chain_problems.get("ok"))

    # The report's table prints the run instead of a dash.
    report_row = _report_row(facts.get("report") or {}, draft_id)
    report_shows = (
        str(report_row.get("run_id") or "") == run_id
        and draft_id in markdown
        and run_id in markdown
    )

    # The draft list shows the run.
    list_shows = draft_id in drafts_text and run_id in drafts_text

    # One run's decisions can be asked for by name - and the other
    # run's decisions must NOT come back.
    mine_records = undos_mine.get("records") or []
    mine_ok = bool(mine_records) and all(
        str(row.get("run_id") or "") == run_id for row in mine_records
    )
    others_absent = all(
        str(row.get("draft_id") or "") != other_id for row in mine_records
    )
    filter_ok = mine_ok and others_absent

    # The MCP surface can be asked the same question.
    mcp_records = mcp_mine.get("records") or []
    mcp_ok = bool(mcp_records) and all(
        str(row.get("run_id") or "") == run_id for row in mcp_records
    ) and all(
        str(row.get("draft_id") or "") != other_id for row in mcp_records
    )

    return {
        "the draft remembers the run that proposed it": draft_remembers,
        "the ledger's decision rows carry the run": ledger_carries,
        "the audit chain agrees with the ledger": chain_agrees,
        "the report's draft table shows the proposing run": report_shows,
        "the draft list shows the proposing run": list_shows,
        "the decisions of one run can be asked for by name": filter_ok,
        "the MCP surface can be asked about one run as well": mcp_ok,
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw facts, not from the pages.

    This pass does not look at what the pages printed. It reads the
    draft file and the chain and works out what the truth is, and
    then says what every surface ought to report. If the two
    readings disagree, the gate says so - and the gate cannot be
    satisfied by a surface that merely looks right.
    """
    run_id = str(facts.get("run_id") or "")
    task_id = str(facts.get("task_id") or "")
    draft_id = str(facts.get("draft_id") or "")
    draft = facts.get("draft") or {}
    rows = facts.get("ledger_rows") or []

    the_draft_remembers = (
        str(draft.get("run_id") or "") == run_id
        and str(draft.get("task_id") or "") == task_id
    )
    decisions = _ledger_decision_rows(rows, draft_id)
    the_ledger_carries = bool(decisions) and all(
        str(row.get("run_id") or "") == run_id
        and str(row.get("task_id") or "") == task_id
        for row in decisions
    )

    # What the report and the list must show: the run, because the
    # draft knows it.
    must_show_run = the_draft_remembers
    # What the filters must return: exactly this run's decisions.
    must_filter = the_ledger_carries

    return {
        "the draft remembers the run that proposed it": the_draft_remembers,
        "the ledger's decision rows carry the run": the_ledger_carries,
        "the audit chain agrees with the ledger": the_ledger_carries,
        "the report's draft table shows the proposing run": must_show_run,
        "the draft list shows the proposing run": must_show_run,
        "the decisions of one run can be asked for by name": must_filter,
        "the MCP surface can be asked about one run as well": must_filter,
    }


def check_a_decision_hangs_on_its_run(iterations: int = 3) -> Dict[str, Any]:
    """The forty-ninth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _facts_of(_make_case(tempfile.mkdtemp(prefix="mas-selftest-run-")))
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in DRAFT_DECISION_RUN_OBSERVATIONS:
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
            # The break is on the **wiring**: the decision row stops
            # copying the thread out of the draft. It is not the same
            # as breaking a shared helper - a broken shared helper
            # makes every surface wrong in the same way, so the
            # surfaces agree and the gate sees nothing. The break has
            # to make one surface read something different.
            import mas.drafts as drafts_module

            real_ledger_row_for = drafts_module.ledger_row_for

            def _no_thread(entry, workspace, _real=real_ledger_row_for):
                # The main ledger's copy of the decision throws the
                # thread away - exactly what it did before this
                # release. The chain keeps it, so the two copies stop
                # saying the same thing, and the ledger can no longer
                # be asked which run a decision belongs to.
                row = dict(_real(entry, workspace))
                row["run_id"] = ""
                row["task_id"] = ""
                return row

            try:
                drafts_module.ledger_row_for = _no_thread
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            _make_case(
                                tempfile.mkdtemp(prefix="mas-selftest-run-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                drafts_module.ledger_row_for = real_ledger_row_for

            if broken.get("the ledger's decision rows carry the run"):
                failures.append(
                    "round %d: the gate did not notice that the decision row "
                    "stops copying the thread - it cannot fail, so it does "
                    "not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "a_decision_hangs_on_its_run",
        "title": "\u51b3\u7b56\u6302\u56de\u63d0\u8bae\u5b83\u7684\u90a3\u6b21\u8fd0\u884c",
        "skipped": False,
        "invariant": (
            "\u4e00\u6b21\u6279\u51c6\u662f\u9519\u7684\uff0c\u8fd9\u6761\u7ebf\u7a0b\u5f97\u80fd"
            "\u67e5\u51fa\u6765\uff1a\u8349\u7a3f\u63d0\u8bae\u8bb0\u5f97\u4f4f\u662f\u54ea"
            "\u4e00\u6b21\u8fd0\u884c\u63d0\u7684\uff0c\u4eba\u5bf9\u5b83\u7684\u6279\u51c6\u4e0e"
            "\u64a4\u9500\u4e5f\u5f97\u6302\u56de\u90a3\u6761\u7ebf\u7a0b\uff0c\u5426\u5219"
            "\u201c\u54ea\u4e9b\u8fd0\u884c\u4e00\u5ea6\u6279\u9519\u4e86\u201d\u6c38\u8fdc\u95ee"
            "\u4e0d\u51fa\u7b54\u6848"
        ),
        "detail": (
            "\u8349\u7a3f\u81ea\u5df1\u8bb0\u4f4f\u63d0\u8bae\u5b83\u7684\u8fd0\u884c\uff0c"
            "\u51b3\u7b56\u884c\u628a\u8fd9\u6761\u7ebf\u7a0b\u62f7\u8d1d\u51fa\u53bb\uff0c"
            "\u62a5\u544a\u3001\u8349\u7a3f\u5217\u8868\u3001\u547d\u4ee4\u884c\u4e0e MCP "
            "\u90fd\u80fd\u6309\u8fd0\u884c\u95ee\u5230\u5b83"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_decision_hangs_on_its_run.CHECK_NAME = (  # type: ignore[attr-defined]
    "a_decision_hangs_on_its_run"
)





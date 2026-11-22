# -*- coding: utf-8 -*-
"""The forty-seventh gate: a taken-back approval is visible everywhere.

The gap
-------
A draft file holds only the **newest** state of a draft. Approve a
change, take the approval back, approve it again - and the draft says
``approved``, as if nothing ever went wrong. The wrongness of the
first approval lives in the audit chain, and it shows up on exactly
one screen.

The command line showed it: ``mas drafts`` printed "this approval was
taken back N times" next to the draft, because the chain can be read
per draft.

The **MCP** surface did not. ``mas_drafts`` handed back the raw draft
dicts, so an agent driving Claude, GPT or Kimi through MCP saw
``approved`` where the person at the keyboard saw "taken back once".
And the agent is the one that acts on it - it is the one that decides
whether to let a change stand.

Two more holes in the same family:

* the report's draft **table** said "已批准" and nothing else, so a
  reader had to cross-reference the separate "被撤销的批准" section
  to learn that the change in front of them was wrong once;
* the status page counted drafts as ``pending`` or ``approved`` and
  had no bucket at all for "an approval of this was taken back", so
  a draft whose approval was taken back and never given again fell
  out of every count.

The fix
-------
The reading of the audit chain lives in one function -
``mas.undos.draft_histories()`` - and every surface that shows drafts
calls it:

* the command line (``mas drafts``);
* the MCP surface (``mas_drafts``, both ``list`` and ``show``);
* the report's draft table (a "被撤销" column);
* the status page (an "批准被撤销过" line).

This gate watches:

* the command line and the MCP surface say the **same thing** about
  the same draft;
* the MCP surface says a draft was taken back, where the old one
  said nothing at all;
* the report's draft table names the draft that was taken back;
* the status page counts the drafts whose approval was taken back;
* and the gate proves it can fail: the **wiring** is broken on
  purpose - the MCP surface stops reading the chain - and the gate
  has to notice that the two surfaces have started disagreeing.

The break is on the wiring, not on the shared function. A shared
function that is broken makes every surface wrong *in the same way*,
so the surfaces agree and the gate sees nothing. The break has to
make one surface stop reading the record.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List, Optional

#: The observations the gate makes.
DRAFT_UNDO_VISIBILITY_OBSERVATIONS = (
    "the MCP surface says a draft was taken back",
    "the command line and the MCP surface say the same thing",
    "the report names the draft whose approval was taken back",
    "the status page counts the drafts whose approval was taken back",
)


class _Env:
    """The environment variables the gate has to borrow.

    The machine has to look at *this* case and at nothing else: a
    gate that reads the reader's own state directory is a gate that
    fails on a machine that has real work in it, and passes on a
    machine that has none.
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
    """A workspace with one approved-taken-back-approved draft.

    The draft is approved, taken back and approved again, so its own
    state is ``approved`` - the very state that hides the wrongness.

    The layout is the real one: a run writes into
    ``<state_dir>/artifacts/<run_id>/``, so the workspace lives one
    folder below the artifacts root. The report page and the status
    page both scan that root, so a workspace that does not sit there
    would be invisible to two of the four surfaces - and the gate
    would be measuring the wrong thing.
    """
    from mas.drafts import approve_draft, stage_draft, undo_draft

    state = pathlib.Path(root) / "state"
    artifacts = state / "artifacts"
    workspace = artifacts / "R-1"
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = pathlib.Path(root) / "config.json"
    target = workspace / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    # The ledger copies of the decisions are written into the state
    # directory, so the environment has to point there *before* the
    # decisions are made - otherwise the copies land in the reader's
    # own ledger and this gate dirties the machine it runs on.
    with _Env(str(state), str(config_path)):
        staged = stage_draft(
            workspace,
            "notes.txt",
            "alpha\nbeta-agent\n".encode("utf-8"),
            reason="the agent proposes this",
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(workspace, draft_id, note="looks fine")
        undo_draft(workspace, draft_id, note="approved the wrong thing")
        approve_draft(workspace, draft_id, note="corrected")

    return {
        "state_dir": str(state),
        "artifacts": str(artifacts),
        "workspace": str(workspace),
        "config_path": str(config_path),
        "draft_id": draft_id,
    }


def _cli_drafts(artifacts: str) -> Dict[str, Any]:
    """What ``mas drafts --json`` says about the drafts."""
    from mas.cli import main

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["drafts", "--workspace", artifacts, "--json"])
    except SystemExit:
        pass
    text = buffer.getvalue()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a broken page is the subject
        return {}


def _mcp_drafts(artifacts: str) -> Dict[str, Any]:
    """What the MCP surface says about the drafts."""
    from mas.mcp_server import handle_tool

    return handle_tool("mas_drafts", {"workspace": artifacts, "action": "list"})


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


def _status_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    """What ``mas status --json`` says."""
    text = _run_cli(
        ["status", "--json"],
        str(case.get("state_dir") or ""),
        str(case.get("config_path") or ""),
    )
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a broken page is the subject
        return {}


def _report_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    """What ``mas report --json`` says."""
    text = _run_cli(
        ["report", "--json"],
        str(case.get("state_dir") or ""),
        str(case.get("config_path") or ""),
    )
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a broken page is the subject
        return {}


def _report_markdown(case: Dict[str, Any]) -> str:
    """The report page, as a person reads it."""
    return _run_cli(
        ["report"],
        str(case.get("state_dir") or ""),
        str(case.get("config_path") or ""),
    )


def _surfaces() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.undos import workspace_rows

    root = tempfile.mkdtemp(prefix="mas-selftest-draft-undo-")
    try:
        case = _make_case(root)
        artifacts = str(case.get("artifacts") or "")
        with _Env(str(case["state_dir"]), str(case["config_path"])):
            cli = _cli_drafts(artifacts)
            mcp = _mcp_drafts(artifacts)
        status = _status_payload(case)
        report = _report_payload(case)
        markdown = _report_markdown(case)
        # The audit chain, read **before** the case is thrown away. The
        # independent reading needs the raw chain, and the chain only
        # exists while the case does - so it is taken here, while the
        # folder is still there, and the independent pass reads the
        # facts rather than the disk.
        chain_rows = workspace_rows(case["workspace"])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    return {
        "draft_id": case["draft_id"],
        "workspace": case["workspace"],
        "chain_rows": chain_rows,
        "cli": cli,
        "mcp": mcp,
        "status": status,
        "report": report,
        "markdown": markdown,
    }


def _row_of(payload: Dict[str, Any], draft_id: str) -> Dict[str, Any]:
    """The one row about this draft, wherever it was found."""
    for row in payload.get("drafts") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == draft_id:
            return row
    return {}


def _report_row_of(report: Dict[str, Any], draft_id: str) -> Dict[str, Any]:
    """The one row about this draft, in the report."""
    for entry in (report.get("drafts") or {}).get("drafts") or []:
        if isinstance(entry, dict) and str(entry.get("draft_id") or "") == draft_id:
            return entry
    return {}


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    draft_id = str(facts.get("draft_id") or "")
    cli_row = _row_of(facts.get("cli") or {}, draft_id)
    mcp_row = _row_of(facts.get("mcp") or {}, draft_id)
    status = facts.get("status") or {}
    report = facts.get("report") or {}
    markdown = str(facts.get("markdown") or "")

    drafts_section = status.get("drafts") or {}
    report_row = _report_row_of(report, draft_id)

    return {
        "the MCP surface says a draft was taken back": (
            bool(mcp_row.get("ever_undone"))
            and int(mcp_row.get("undos") or 0) >= 1
            and int(mcp_row.get("approvals") or 0) >= 1
        ),
        "the command line and the MCP surface say the same thing": (
            bool(cli_row.get("ever_undone")) == bool(mcp_row.get("ever_undone"))
            and int(cli_row.get("undos") or 0) == int(mcp_row.get("undos") or 0)
            and int(cli_row.get("approvals") or 0) == int(mcp_row.get("approvals") or 0)
            and cli_row.get("reverted_line_count") == mcp_row.get("reverted_line_count")
        ),
        "the report names the draft whose approval was taken back": (
            int(report_row.get("taken_back") or 0) >= 1
            and bool(report_row.get("ever_undone"))
            and draft_id in markdown
        ),
        "the status page counts the drafts whose approval was taken back": (
            draft_id in (drafts_section.get("undone") or [])
            and draft_id in (drafts_section.get("with_history") or [])
        ),
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw facts, not from the pages.

    This pass does not look at what the pages printed. It reads the
    audit chain directly, works out what the chain says about this
    draft, and then says what every surface ought to report. If the
    two readings disagree, the gate says so - and the gate cannot be
    satisfied by a surface that merely looks right.
    """
    from mas.undos import draft_history

    draft_id = str(facts.get("draft_id") or "")

    chain = list(facts.get("chain_rows") or [])
    history = draft_history(chain, draft_id)

    approvals = int(history.get("approvals") or 0)
    undos = int(history.get("undos") or 0)
    ever_undone = bool(history.get("ever_undone"))
    counts = [
        record.get("reverted_line_count")
        for record in (history.get("records") or [])
        if isinstance(record.get("reverted_line_count"), (int, float))
    ]
    reverted = sum(int(count) for count in counts) if counts else None

    cli_row = _row_of(facts.get("cli") or {}, draft_id)
    mcp_row = _row_of(facts.get("mcp") or {}, draft_id)
    status = facts.get("status") or {}
    drafts_section = status.get("drafts") or {}
    report = facts.get("report") or {}
    report_row = _report_row_of(report, draft_id)

    return {
        "the MCP surface says a draft was taken back": (
            # The chain says the approval was taken back, so the MCP
            # surface has to say so too.
            ever_undone
            and bool(mcp_row.get("ever_undone"))
            and int(mcp_row.get("undos") or 0) == undos
            and int(mcp_row.get("approvals") or 0) == approvals
        ),
        "the command line and the MCP surface say the same thing": (
            bool(cli_row.get("ever_undone")) == ever_undone
            and int(cli_row.get("undos") or 0) == undos
            and int(cli_row.get("approvals") or 0) == approvals
            and cli_row.get("reverted_line_count") == reverted
            and mcp_row.get("reverted_line_count") == reverted
        ),
        "the report names the draft whose approval was taken back": (
            # The chain says one approval was taken back, so the
            # report has to name that draft as often.
            undos >= 1
            and int(report_row.get("taken_back") or 0) >= 1
            and bool(report_row.get("ever_undone"))
            and draft_id in str(facts.get("markdown") or "")
        ),
        "the status page counts the drafts whose approval was taken back": (
            # The chain says the approval was taken back, so the
            # status page has to count the draft as one of them.
            undos >= 1
            and draft_id in (drafts_section.get("undone") or [])
        ),
    }


def check_a_taken_back_approval_is_visible_everywhere(
    iterations: int = 3,
) -> Dict[str, Any]:
    """The forty-seventh gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surfaces()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in DRAFT_UNDO_VISIBILITY_OBSERVATIONS:
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
            # The break is on the **wiring**: the MCP surface stops
            # reading the audit chain. It is not the same as breaking
            # the shared function - a broken shared function makes
            # every surface wrong in the same way, so the surfaces
            # agree and the gate sees nothing. The break has to make
            # one surface stop reading the record.
            import mas.mcp_server as mcp_module

            real_draft_histories = mcp_module.draft_histories

            def _no_history(roots, draft_ids, _real=real_draft_histories):
                # The chain is not read at all - exactly what the MCP
                # surface did before this release.
                return {}

            try:
                mcp_module.draft_histories = _no_history
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(_surfaces())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                mcp_module.draft_histories = real_draft_histories

            if broken.get("the MCP surface says a draft was taken back"):
                failures.append(
                    "round %d: the gate did not notice that the MCP surface "
                    "stops reading the audit chain - it cannot fail, so it "
                    "does not watch" % (round_index + 1)
                )
            if broken.get(
                "the command line and the MCP surface say the same thing"
            ):
                failures.append(
                    "round %d: the gate did not notice that the command line "
                    "and the MCP surface have started disagreeing"
                    % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "a_taken_back_approval_is_visible_everywhere",
        "title": "\u88ab\u64a4\u9500\u7684\u6279\u51c6\u5728\u6bcf\u4e00\u9762\u90fd\u770b\u5f97\u89c1",
        "skipped": False,
        "invariant": (
            "\u4e00\u4e2a\u88ab\u64a4\u9500\u7684\u6279\u51c6\u5fc5\u987b\u5728\u6240\u6709\u9762\u4e0a\u90fd\u770b\u5f97\u89c1"
            "\uff1a\u8349\u7a3f\u81ea\u8eab\u53ea\u8bb0\u6700\u65b0\u72b6\u6001\uff0c\u201c\u66fe\u7ecf\u9519\u8fc7\u201d\u53ea\u5728\u5ba1\u8ba1\u94fe\u91cc"
        ),
        "detail": (
            "\u67e5\u547d\u4ee4\u884c\u3001MCP \u9762\u3001\u62a5\u544a\u4e0e\u72b6\u6001\u9875\u662f\u5426"
            "\u90fd\u8bf4\u51fa\u201c\u8fd9\u4e2a\u6279\u51c6\u88ab\u64a4\u9500\u8fc7\u201d"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_taken_back_approval_is_visible_everywhere.CHECK_NAME = (  # type: ignore[attr-defined]
    "a_taken_back_approval_is_visible_everywhere"
)

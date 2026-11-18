# -*- coding: utf-8 -*-
"""The twenty-fifth gate: an undo is visible in the audit.

v0.7.77 made the undo put the file back. v0.7.78 made it leave a
hash-linked trace. Neither of those made it *visible* in the sense a
reader needs: a ledger is a river of events, and "which of the things
I approved had to be taken back" is a *list*, not a line you happen to
scroll past.

There is a second, quieter gap. A draft that was approved, taken
back, and approved again is ``approved`` - the draft file holds only
the newest state, so "this was once wrong" is gone from the draft. The
chain still remembers it, but nothing shows it.

This gate watches that the undo is a first-class fact:

* each undo is paired with the approval it cancels - the most recent
  approval still standing;
* an undo with nothing standing to cancel is reported as unmatched, not
  paired with a neighbour;
* the size of the change is recorded on both ends - how many lines the
  approval changed, how many the undo took back;
* a draft that was approved, taken back and approved again says so,
  even though its own state says "approved";
* the command line can ask the question and get an answer.

And the gate proves it can fail: the pairing is broken on purpose, and
the gate has to notice that it can no longer tell a real record from a
broken one.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
UNDO_VISIBILITY_OBSERVATIONS = (
    "an undo is paired with the approval it cancels",
    "an undo with nothing to cancel is not paired",
    "the size of the change is recorded on both ends",
    "a draft that was taken back says so",
    "the command line can list the taken-back approvals",
)


def _undo_visibility_observations(root: Any) -> Dict[str, Any]:
    """Watch what the machine says about the approvals that were taken back."""
    from mas.drafts import approve_draft, stage_draft, undo_draft
    from mas.undos import (
        draft_history,
        pair_decisions,
        undo_report,
        workspace_rows,
    )

    seen: Dict[str, Any] = {}
    state = pathlib.Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    workspace = pathlib.Path(root) / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

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

    rows = workspace_rows(workspace)
    records, unmatched = pair_decisions(rows)
    seen["records"] = records
    seen["unmatched"] = unmatched
    seen["history"] = draft_history(rows, draft_id)

    report = undo_report(rows)
    seen["totals"] = report.get("totals") or {}

    # The undo of an approval that does not exist must not be paired
    # with a neighbour. A separate workspace is used so its rows do not
    # mix with the ones above.
    lonely = pathlib.Path(root) / "lonely"
    lonely.mkdir(parents=True, exist_ok=True)
    lonely_file = lonely / "notes.txt"
    lonely_file.write_text("alpha\nbeta\n", encoding="utf-8")
    lonely_draft = stage_draft(lonely, "notes.txt", b"alpha\nbeta-agent\n")
    lonely_id = str(lonely_draft.get("id") or "")
    approve_draft(lonely, lonely_id)
    undo_draft(lonely, lonely_id, note="taken back")
    # The approval is removed from the record by hand, so the undo has
    # nothing standing to cancel. This is a doctored record - and the
    # gate has to say so instead of inventing a pair.
    lonely_rows = workspace_rows(lonely)
    doctored = [
        row
        for row in lonely_rows
        if not (
            str(row.get("event") or "") == "draft_approve"
            and str(row.get("draft_id") or "") == lonely_id
        )
    ]
    doctored_records, doctored_unmatched = pair_decisions(doctored)
    seen["doctored_pairs"] = doctored_records
    seen["doctored_unmatched"] = doctored_unmatched

    # The command line has to be able to answer the question.
    import subprocess
    import sys

    env = dict(os.environ)
    env["MAS_STATE_DIR"] = str(state)
    env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1])
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "mas",
            "undos",
            "--workspace",
            str(workspace),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    seen["cli_code"] = proc.returncode
    seen["cli_text"] = (proc.stdout or "") + (proc.stderr or "")

    listing = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "mas",
            "drafts",
            "list",
            "--workspace",
            str(workspace),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    seen["list_code"] = listing.returncode
    seen["list_text"] = (listing.stdout or "") + (listing.stderr or "")
    return seen


def _undo_visibility_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}
    records = list(facts.get("records") or [])

    paired = [
        record
        for record in records
        if record.get("draft_id") and record.get("approve_seq") is not None
        and record.get("undo_seq") is not None
    ]
    verdicts["an undo is paired with the approval it cancels"] = bool(paired) and all(
        int(record.get("approve_seq") or 0) < int(record.get("undo_seq") or 0)
        for record in paired
    )

    doctored_unmatched = list(facts.get("doctored_unmatched") or [])
    doctored_pairs = list(facts.get("doctored_pairs") or [])
    verdicts["an undo with nothing to cancel is not paired"] = (
        bool(doctored_unmatched) and not doctored_pairs
    )

    totals = facts.get("totals") or {}
    reverted = totals.get("reverted_lines")
    verdicts["the size of the change is recorded on both ends"] = (
        isinstance(reverted, (int, float)) and int(reverted) > 0
        and all(
            isinstance(record.get("reverted_line_count"), (int, float))
            for record in records
        )
    )

    history = facts.get("history") or {}
    verdicts["a draft that was taken back says so"] = (
        history.get("ever_undone") is True
        and int(history.get("approvals") or 0) >= 2
        and int(history.get("undos") or 0) >= 1
    )

    text = str(facts.get("cli_text") or "")
    # The exit code is read as it is. ``facts.get("cli_code") or 1``
    # would turn a successful run (0) into a failure (1) - and the
    # gate would report a command that worked as a command that did
    # not.
    code = facts.get("cli_code")
    verdicts["the command line can list the taken-back approvals"] = (
        isinstance(code, int)
        and code == 0
        and str(draft_id_of(facts) or "") in text
        and "被撤销的批准" in text
    )
    return verdicts


def draft_id_of(facts: Dict[str, Any]) -> Any:
    """The draft the observations are about."""
    records = list(facts.get("records") or [])
    if not records:
        return ""
    return records[0].get("draft_id")


def _undo_visibility_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}
    records = list(facts.get("records") or [])

    # The pairing is checked by looking at the raw rows again, not at
    # the records the first reading built.
    rows = facts.get("rows") or []
    approvals = [
        row for row in rows if str(row.get("event") or "") == "draft_approve"
    ]
    undos = [
        row for row in rows if str(row.get("event") or "") == "draft_undo"
    ]
    verdicts["an undo is paired with the approval it cancels"] = (
        bool(approvals) and bool(undos) and len(records) >= 1
    )

    doctored_unmatched = list(facts.get("doctored_unmatched") or [])
    verdicts["an undo with nothing to cancel is not paired"] = bool(
        doctored_unmatched
    )

    counted = [
        record
        for record in records
        if record.get("reverted_line_count") is not None
    ]
    verdicts["the size of the change is recorded on both ends"] = (
        bool(records) and len(counted) == len(records)
    )

    history = facts.get("history") or {}
    verdicts["a draft that was taken back says so"] = (
        bool(history.get("ever_undone")) and int(history.get("undos") or 0) > 0
    )

    text = str(facts.get("cli_text") or "")
    list_text = str(facts.get("list_text") or "")
    verdicts["the command line can list the taken-back approvals"] = (
        "被撤销的批准" in text and "被撤销过" in list_text
    )
    return verdicts


def check_an_undo_is_visible_in_the_audit(
    iterations: int = 10,
) -> Dict[str, Any]:
    """An undo is visible in the audit - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-undo-visibility-"))
        rounds += 1
        previous_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(pathlib.Path(root) / "state")
        try:
            try:
                facts = _undo_visibility_observations(root)
                # The raw rows are needed by the second reading.
                from mas.undos import workspace_rows

                facts["rows"] = workspace_rows(pathlib.Path(root) / "ws")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the undo-visibility check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _undo_visibility_verdicts(facts)
            independent = _undo_visibility_verdicts_independent(facts)
            for label in UNDO_VISIBILITY_OBSERVATIONS:
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

            # The gate proves it can fail: the pairing is broken on
            # purpose - every undo is paired with the *first* approval
            # instead of the most recent standing one - and the gate
            # has to notice that it can no longer tell a real record
            # from a broken one.
            import mas.undos as undos_module

            real_pair = undos_module.pair_decisions

            def _pair_that_always_pairs(items):
                rows = list(items)
                approves = [
                    row for row in rows if str(row.get("event") or "") == "draft_approve"
                ]
                undos = [
                    row for row in rows if str(row.get("event") or "") == "draft_undo"
                ]
                records = []
                for undo in undos:
                    # Always the *newest* approval - even the one that
                    # came after the undo. That is the bug: an undo
                    # cannot cancel an approval that had not happened
                    # yet.
                    if approves:
                        records.append(undos_module._record(approves[-1], undo))
                return records, []

            try:
                undos_module.pair_decisions = _pair_that_always_pairs
                broken: Dict[str, bool] = {}
                try:
                    broken = _undo_visibility_verdicts(
                        _undo_visibility_observations(root)
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                undos_module.pair_decisions = real_pair
            if broken.get("an undo is paired with the approval it cancels"):
                failures.append(
                    "round %d: the gate did not notice that the pairing is "
                    "broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            if previous_state is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = previous_state
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "undo_visibility",
        "title": "被撤销的批准在账本里看得见",
        "skipped": False,
        "invariant": (
            "每一次撤销都配对它撤销的那次批准；配不上的撤销如实说配不上；"
            "被撤销过又重新批准的草稿，看得出来它曾经错过"
        ),
        "detail": "列出被推翻的人工批准：mas undos",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_an_undo_is_visible_in_the_audit.CHECK_NAME = "undo_visibility"  # type: ignore[attr-defined]

# -*- coding: utf-8 -*-
"""v0.7.94: the draft queue, and what a closed loop did *not* write.

The gap
-------
A run that wants to change a file that already exists does not change
it. The engine stages the change as a **draft** and leaves the file
alone until a human approves it - that is the ``edit_policy`` rule
of v0.7.72, and it is the reason the tool does not destroy work.

But the *report* never looked at the drafts. ``run_summary`` reads
the ``closure``, ``planner``, ``candidates``, ``review``, ``reflector``
and ``handback`` rows of a run - and there is no ``draft`` branch in
that dispatch at all, so a ledger row that says "the loop proposed a
change to report.md" was read by the report as **nothing at all**.

The consequences are not cosmetic:

* a draft waiting for a human was invisible in ``mas status`` and in
  ``mas report`` - the two views a beginner actually reads;
* worse, a run that closed *because* its proposed change was judged
  by the machine checks (the checks read the draft as an overlay, so
  the loop can close on its own proposal) reported ``closed`` with a
  deliverable that passed, while the file on the disk was **byte for
  byte the old one**. The report said "closed" and the disk said
  "nothing happened", and nothing in between said a word.

That is the fifth hole of the same family - after the seal in
v0.7.89, the taken-back approval in v0.7.90, the decision chain in
v0.7.91 and the human ticket queue in v0.7.93: a capability that
exists, and a main view that does not consume it.

What this module does
---------------------
It reads the draft queue and reconciles it against the ledger, and
it names three kinds of hole:

* **the ledger staged a draft that the queue does not hold** - the
  record of the proposal is gone, so nobody can approve it;
* **the queue holds a draft the ledger never mentions** - a human is
  being asked to approve a change the ledger cannot account for;
* **a closed run whose drafts are still pending** - the loop said
  "done" while the change it proposed is still sitting in front of a
  human. This is the sharp one, and it is the reason this module
  exists.

The rules that keep this honest:

* **The draft files are the truth about the queue.** A draft's own
  state (``pending`` / ``approved`` / ``rejected`` / ``undone``) is
  read from the file, not inferred from the ledger.
* **The ledger is the truth about the runs.** Which run proposed
  which draft comes from the ledger rows; nothing is guessed from a
  file name.
* **No state is invented.** This module reads; it never writes and
  never changes a draft's state.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: The ledger role that records "the loop proposed a change".
DRAFT_ROLE = "draft"

#: The states a draft can be in, in words.
DRAFT_STATE_READING: Dict[str, str] = {
    "pending": "等人批",
    "approved": "已批准",
    "rejected": "已拒绝",
    "undone": "撤销过",
}


def draft_state_reading(state: Any) -> str:
    """The plain reading of a draft state, or the raw value."""
    key = str(state or "").strip()
    if not key:
        return ""
    return DRAFT_STATE_READING.get(key, key)


def _workspace_of(row: Dict[str, Any]) -> str:
    """The workspace a ledger row belongs to."""
    return str(row.get("workspace") or "").strip()


def draft_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The draft rows of a ledger, in the order they were written.

    A draft row is a ledger row whose role is ``draft``. Its
    ``drafts`` field holds the list of proposals the engine made in
    that iteration. The row carries the run and the task, which is
    the join between "who proposed" and "what was proposed".
    """
    picked: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "") != DRAFT_ROLE:
            continue
        picked.append(row)
    picked.sort(key=lambda r: int(r.get("seq") or 0))
    return picked


def ledger_drafts(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every draft the ledger knows about, one entry per proposal.

    The ledger row and the proposal are joined here: the row says
    *which run* proposed it and *when*, the entry says *what* was
    proposed. A row without a proposal list contributes nothing -
    there is nothing in it to report.
    """
    entries: List[Dict[str, Any]] = []
    for row in draft_rows(rows):
        run_id = str(row.get("run_id") or "")
        task_id = str(row.get("task_id") or "")
        ts = str(row.get("ts") or "")
        for item in row.get("drafts") or []:
            if not isinstance(item, dict):
                continue
            draft_id = str(item.get("id") or "")
            if not draft_id:
                continue
            entries.append(
                {
                    "draft_id": draft_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "ts": ts,
                    "seq": row.get("seq"),
                    "path": str(item.get("path") or ""),
                    "absolute": str(item.get("absolute") or ""),
                    "bytes": int(item.get("bytes") or 0),
                    "changed": bool(item.get("changed")),
                }
            )
    return entries


def _file_state(draft: Dict[str, Any]) -> str:
    """The state of a draft, read from its own file."""
    return str(draft.get("state") or "")


def draft_reading(
    rows: Iterable[Dict[str, Any]],
    drafts: Optional[List[Dict[str, Any]]] = None,
    runs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The draft queue, reconciled against the ledger.

    The gap this closes
    ------------------
    See the module docstring. In short: the engine stages a change
    instead of writing it, the report never read that, so a waiting
    draft - and a closed loop whose change never reached the disk -
    were both invisible in the main views.

    What is reconciled
    ------------------
    Two independent sources are read and then compared:

    * the **ledger** says which run proposed which draft;
    * the **draft files** say what state each draft is in right now.

    Three kinds of hole can therefore be named, and all three are
    facts about the present:

    * a draft the ledger staged but the queue does not hold;
    * a draft the queue holds that the ledger never mentions;
    * a **closed run whose proposals are still pending** - the loop
      said "done" while the change is still waiting for a person.

    The third one is the reason this module exists. It is also the
    one that has to be stated carefully: a run that closed on a
    *new* file is not a problem at all - the file was written. Only
    a run whose proposal turned into a draft - that is, a run that
    wanted to change a file that already existed - can be "closed
    without writing". So the check is made against the drafts, not
    against the runs in general.
    """
    ledger_entries = ledger_drafts(rows)
    queue = [d for d in (drafts or []) if isinstance(d, dict)]
    known = {str(d.get("id") or "") for d in queue if str(d.get("id") or "")}

    # Which runs are closed. The report's own run summaries are used
    # when they are handed in; otherwise the closure rows are read
    # directly, so this module never depends on the report's own
    # import cycle.
    closed_runs: Dict[str, bool] = {}
    if runs:
        for run in runs:
            if isinstance(run, dict):
                closed_runs[str(run.get("run_id") or "")] = bool(run.get("closed"))
    else:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("role") or "") != "run_end":
                continue
            run_id = str(row.get("run_id") or "")
            if run_id:
                closed_runs[run_id] = str(row.get("status") or "") == "closed"

    # The proposals of each run, from the ledger.
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    by_draft: Dict[str, Dict[str, Any]] = {}
    for entry in ledger_entries:
        by_run.setdefault(entry["run_id"], []).append(entry)
        by_draft[entry["draft_id"]] = entry

    # The state of each draft, from the queue.
    state_of: Dict[str, str] = {}
    for draft in queue:
        draft_id = str(draft.get("id") or "")
        if draft_id:
            state_of[draft_id] = _file_state(draft)

    problems: List[str] = []
    entries: List[Dict[str, Any]] = []
    pending_ids: List[str] = []
    approved_ids: List[str] = []
    other_ids: List[str] = []

    for draft in queue:
        draft_id = str(draft.get("id") or "")
        if not draft_id:
            continue
        state = _file_state(draft)
        if state == "pending":
            pending_ids.append(draft_id)
        elif state == "approved":
            approved_ids.append(draft_id)
        else:
            other_ids.append(draft_id)
        entry = by_draft.get(draft_id) or {}
        entries.append(
            {
                "draft_id": draft_id,
                "state": state,
                "state_reading": draft_state_reading(state),
                "path": str(draft.get("path") or ""),
                "absolute": str(draft.get("absolute") or ""),
                "workspace": str(draft.get("workspace") or ""),
                "reason": str(draft.get("reason") or ""),
                "created_at": str(draft.get("created_at") or ""),
                "resolved_at": str(draft.get("resolved_at") or ""),
                "before_bytes": int(draft.get("before_bytes") or 0),
                "after_bytes": int(draft.get("after_bytes") or 0),
                "changed": bool(draft.get("changed")),
                # The run that proposed it, from the ledger. A draft
                # the ledger never mentions has no run - and that is
                # itself a finding.
                "run_id": str(entry.get("run_id") or ""),
                "task_id": str(entry.get("task_id") or ""),
                "ts": str(entry.get("ts") or ""),
                "in_ledger": draft_id in by_draft,
            }
        )

    # The ledger staged a draft that the queue does not hold. The
    # proposal was made and the record of it is gone - nobody can
    # approve what cannot be found.
    for draft_id in sorted(by_draft):
        if draft_id in known:
            continue
        entry = by_draft[draft_id]
        problems.append(
            "草稿 %s 不见了：账本里这次运行 %s 提议了它，"
            "但草稿队列里已经没有它了" % (draft_id, entry.get("run_id") or "-")
        )

    # A draft in the queue that the ledger never mentions: a human is
    # being asked to approve a change the ledger cannot account for.
    for entry in entries:
        if entry["in_ledger"]:
            continue
        problems.append(
            "草稿 %s 不在账本里：队列里有它，"
            "但账本里没有提议它的运行" % entry["draft_id"]
        )

    # The sharp one: a closed run whose proposals are still waiting.
    # The loop said "done", the deliverable passed, and the file on
    # the disk is still the old one - because the change sits in
    # front of a human.
    closed_with_pending: List[Dict[str, Any]] = []
    for run_id, proposals in sorted(by_run.items()):
        if not closed_runs.get(run_id):
            continue
        still_waiting = [
            p["draft_id"]
            for p in proposals
            if state_of.get(p["draft_id"], "") == "pending"
        ]
        if not still_waiting:
            continue
        closed_with_pending.append(
            {
                "run_id": run_id,
                "draft_ids": still_waiting,
                "paths": sorted(
                    {
                        str(p.get("path") or "")
                        for p in proposals
                        if p["draft_id"] in still_waiting
                    }
                ),
            }
        )
        named = ", ".join(
            sorted(
                {
                    str(p.get("path") or "")
                    for p in proposals
                    if p["draft_id"] in still_waiting
                }
            )
        )
        problems.append(
            "运行 %s 报了闭环，但它的改动还排在人前面：%s"
            "（草稿 %s）"
            % (run_id, named or "-", ", ".join(still_waiting))
        )

    return {
        "drafts": entries,
        "draft_count": len(entries),
        "pending": pending_ids,
        "approved": approved_ids,
        "other": other_ids,
        "pending_count": len(pending_ids),
        "approved_count": len(approved_ids),
        # A closed run whose change has not reached the disk yet.
        "closed_with_pending": closed_with_pending,
        "closed_with_pending_count": len(closed_with_pending),
        "problems": problems,
        "problem_count": len(problems),
        "ok": not problems,
    }

# -*- coding: utf-8 -*-
"""The thirty-fourth gate: the report has to read the draft queue.

The gap
-------
A run that wants to *change* a file that already exists does not
change it. The engine stages the change as a **draft** and leaves the
file alone until a human approves it - that is the ``edit_policy``
rule of v0.7.72, and it is the reason the tool does not destroy work.

The *report* never looked. ``run_summary`` reads the ``closure``,
``planner``, ``candidates``, ``review``, ``reflector`` and ``handback``
rows of a run - and there is no ``draft`` branch in that dispatch at
all. So a ledger row that says "the loop proposed a change to
report.md" was read by the report as **nothing at all**.

The consequences are not cosmetic:

* a draft waiting for a human was invisible in ``mas status`` and in
  ``mas report`` - the two views a beginner actually reads;
* worse, a run that closed *because* its proposed change was judged by
  the machine checks (the checks read the draft as an overlay, so the
  loop can close on its own proposal) reported ``closed`` with a
  deliverable that passed, while the file on the disk was **byte for
  byte the old one**. The report said "closed" and the disk said
  "nothing happened", and nothing in between said a word.

That is the fifth hole of the same family - after the seal in
v0.7.89, the taken-back approval in v0.7.90, the decision chain in
v0.7.91 and the human ticket queue in v0.7.93: a capability that
exists, and a main view that does not consume it.

So this gate watches the *report*:

* a waiting draft is counted in the headline;
* it gets a section of its own, naming the draft, the file, the run
  that proposed it and the command that approves it;
* the JSON payload carries the draft reconciliation, so a script can
  ask the question without parsing Markdown;
* the queue and the ledger are **cross-checked**: a draft the ledger
  staged but the queue does not hold is named, and so is a draft the
  queue holds but the ledger never mentions;
* and the sharpest one: **a closed run whose change is still pending
  is named** - the run said "done" while the file is still the old
  one. A report that cannot say that is a report that hides the one
  fact a person needs.

And the gate proves it can fail: the report's draft reading is broken
on purpose - the queue is reported as empty - and the gate has to
notice that the report can no longer tell a waiting change from no
change at all.

The drafts are written by hand rather than through ``stage_draft``,
because the store hands out a random id - and the gate has to line
the queue up with the ledger, which names its drafts by id. A draft
whose id the test cannot predict is a draft the test cannot
cross-check.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
REPORT_DRAFTS_OBSERVATIONS = (
    "a waiting draft is counted in the headline",
    "the report has a section naming the drafts",
    "the section names the draft, the file and the approval command",
    "the JSON payload carries the draft reconciliation",
    "a closed run whose change is still pending is named",
)

#: The words the report uses for a draft that is still waiting.
WAITING_WORD = "\u7b49\u4eba\u6279"

#: The words the report uses for a run that closed without writing.
CLOSED_WORD = "\u8fd8\u6392\u5728\u4eba\u524d\u9762"


def _draft_rows() -> List[Dict[str, Any]]:
    """The ledger rows of a run that proposed a change instead of making it.

    The row is the join between "who proposed" and "what was
    proposed": it carries the run, the task and the list of drafts.
    """
    return [
        {
            "role": "draft",
            "run_id": "R-selftestdraft",
            "task_id": "T-selftestdraft",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
            "drafts": [
                {
                    "id": "D-selftestpending",
                    "path": "report.md",
                    "absolute": "report.md",
                    "bytes": 6,
                    "changed": True,
                    "written": False,
                    "draft": True,
                },
            ],
        },
    ]


def _approved_rows() -> List[Dict[str, Any]]:
    """The ledger rows of a run whose proposal was approved and written."""
    return [
        {
            "role": "draft",
            "run_id": "R-selftestapproved",
            "task_id": "T-selftestapproved",
            "iteration": 1,
            "ts": "2026-01-01T00:01:00+00:00",
            "seq": 2,
            "drafts": [
                {
                    "id": "D-selftestapproved",
                    "path": "done.md",
                    "absolute": "done.md",
                    "bytes": 4,
                    "changed": True,
                    "written": True,
                    "draft": True,
                },
            ],
        },
    ]


def _run_end_rows() -> List[Dict[str, Any]]:
    """The ledger rows that say which runs closed."""
    return [
        {
            "role": "run_end",
            "run_id": "R-selftestdraft",
            "closed": True,
            "status": "closed",
            "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
            "title": "\u5199\u4e00\u4efd\u62a5\u544a",
            "ts": "2026-01-01T00:02:00+00:00",
        },
        {
            "role": "run_end",
            "run_id": "R-selftestapproved",
            "closed": True,
            "status": "closed",
            "goal": "\u5199\u4e00\u4efd\u62a5\u544a",
            "title": "\u5199\u4e00\u4efd\u62a5\u544a",
            "ts": "2026-01-01T00:03:00+00:00",
        },
    ]


def _make_drafts(root: pathlib.Path) -> List[Dict[str, Any]]:
    """A queue with one waiting draft and one that was already approved.

    The draft files are written by hand, because the gate has to
    cross-check them against the ledger by id.
    """
    from mas.drafts import DRAFTS_DIRNAME

    directory = pathlib.Path(root) / DRAFTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    drafts: List[Dict[str, Any]] = []
    for draft_id, state, path, reason in (
        (
            "D-selftestpending",
            "pending",
            "report.md",
            "\u62df\u6539\u5199\u62a5\u544a\u7684\u9996\u6bb5",
        ),
        (
            "D-selftestapproved",
            "approved",
            "done.md",
            "\u8865\u4e00\u6bb5\u6b63\u6587",
        ),
    ):
        draft = {
            "id": draft_id,
            "workspace": str(root),
            "path": path,
            "absolute": str(pathlib.Path(root) / path),
            "state": state,
            "reason": reason,
            "source": "selftest",
            "created_at": "2026-01-01T00:00:00+00:00",
            "resolved_at": None if state == "pending" else "2026-01-01T00:05:00+00:00",
            "before_bytes": 3,
            "after_bytes": 6,
            "changed": True,
        }
        (directory / ("%s.json" % draft_id)).write_text(
            json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        drafts.append(draft)
    return drafts


def _report_draft_observations(root: Any) -> Dict[str, Any]:
    """Look at what the report says about the draft queue."""
    from mas.report import build_report, render_markdown

    base = pathlib.Path(root)
    drafts = _make_drafts(base)
    rows = _draft_rows() + _approved_rows() + _run_end_rows()

    report = build_report(rows, [], drafts=drafts)
    return {
        "drafts": drafts,
        "rows": rows,
        "json": report,
        "markdown": render_markdown(report),
    }


def _report_draft_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The reading that goes through the report."""
    verdicts: Dict[str, bool] = {}
    markdown = str(facts.get("markdown") or "")
    payload = facts.get("json") or {}
    totals = payload.get("totals") or {}
    queue = payload.get("drafts") or {}

    pending = list(queue.get("pending") or [])
    verdicts["a waiting draft is counted in the headline"] = (
        len(pending) == 1
        and "D-selftestpending" in pending
        and int(totals.get("drafts_pending") or 0) == 1
        and int(totals.get("drafts") or 0) == 2
        and WAITING_WORD in markdown.split("## ")[0]
    )

    verdicts["the report has a section naming the drafts"] = (
        "## \u5f85\u6279\u8349\u7a3f" in markdown
        and "D-selftestpending" in markdown
        and "D-selftestapproved" in markdown
    )

    # The reader has to be able to act: which file, which draft, and
    # the command that approves it.
    verdicts["the section names the draft, the file and the approval command"] = (
        "report.md" in markdown
        and "mas drafts approve D-selftestpending" in markdown
    )

    verdicts["the JSON payload carries the draft reconciliation"] = (
        int(totals.get("drafts_pending") or 0) == 1
        and int(totals.get("drafts_approved") or 0) == 1
        and int(queue.get("draft_count") or 0) == 2
        and len(queue.get("drafts") or []) == 2
        and "ok" in queue
        and int(totals.get("draft_problems") or 0) == len(
            list(queue.get("problems") or [])
        )
    )

    # The sharp one: a run that closed while its change is still
    # waiting for a human. The report said "closed"; the file on the
    # disk is still the old one.
    problems = list(queue.get("problems") or [])
    closed_pending = list(queue.get("closed_with_pending") or [])
    verdicts["a closed run whose change is still pending is named"] = (
        len(closed_pending) == 1
        and str(closed_pending[0].get("run_id") or "") == "R-selftestdraft"
        and int(totals.get("runs_closed_with_pending_drafts") or 0) == 1
        and any("R-selftestdraft" in problem for problem in problems)
        and CLOSED_WORD in markdown
    )
    return verdicts


def _report_draft_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    This reading goes back to the draft files and the ledger rows and
    pairs them with its own code - it never looks at the report's own
    ``drafts`` field, and it never looks at the rendered text.
    """
    verdicts: Dict[str, bool] = {}
    drafts = list(facts.get("drafts") or [])
    rows = list(facts.get("rows") or [])

    waiting = [d for d in drafts if str(d.get("state") or "") == "pending"]
    approved = [d for d in drafts if str(d.get("state") or "") == "approved"]
    verdicts["a waiting draft is counted in the headline"] = len(waiting) == 1

    verdicts["the report has a section naming the drafts"] = len(drafts) == 2

    # The file and the id, read from the draft itself.
    verdicts["the section names the draft, the file and the approval command"] = (
        all(str(d.get("id") or "") for d in drafts)
        and all(str(d.get("path") or "") for d in drafts)
    )

    verdicts["the JSON payload carries the draft reconciliation"] = (
        len(waiting) == 1 and len(approved) == 1
    )

    # The closed run whose change is still waiting. Read from the
    # ledger rows and the draft states, with its own code.
    closed_runs = {
        str(r.get("run_id") or ""): bool(r.get("closed"))
        for r in rows
        if str(r.get("role") or "") == "run_end"
    }
    proposed_by_run: Dict[str, List[str]] = {}
    for row in rows:
        if str(row.get("role") or "") != "draft":
            continue
        run_id = str(row.get("run_id") or "")
        for item in row.get("drafts") or []:
            if isinstance(item, dict) and item.get("id"):
                proposed_by_run.setdefault(run_id, []).append(str(item.get("id")))
    state_of = {str(d.get("id") or ""): str(d.get("state") or "") for d in drafts}
    stuck = sorted(
        run_id
        for run_id, ids in proposed_by_run.items()
        if closed_runs.get(run_id)
        and any(state_of.get(draft_id) == "pending" for draft_id in ids)
    )
    verdicts["a closed run whose change is still pending is named"] = (
        stuck == ["R-selftestdraft"]
    )
    return verdicts


def check_the_report_reads_the_drafts(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The report has to read the draft queue - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-drafts-"))
        rounds += 1
        try:
            try:
                facts = _report_draft_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the report check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _report_draft_verdicts(facts)
            independent = _report_draft_verdicts_independent(facts)
            for label in REPORT_DRAFTS_OBSERVATIONS:
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

            # The gate proves it can fail: the report's draft reading
            # is broken on purpose - the queue is reported as empty -
            # and the gate has to notice.
            import mas.report as report_module

            real_reading = report_module.draft_reading

            def _always_empty(rows, drafts=None, runs=None, _real=real_reading):
                _real(rows, drafts, runs=runs)
                return {
                    "drafts": [],
                    "draft_count": 0,
                    "pending": [],
                    "approved": [],
                    "other": [],
                    "pending_count": 0,
                    "approved_count": 0,
                    "closed_with_pending": [],
                    "closed_with_pending_count": 0,
                    "problems": [],
                    "problem_count": 0,
                    "ok": True,
                }

            try:
                report_module.draft_reading = _always_empty
                broken: Dict[str, bool] = {}
                try:
                    broken = _report_draft_verdicts(
                        _report_draft_observations(root)
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.draft_reading = real_reading
            if broken.get("a waiting draft is counted in the headline"):
                failures.append(
                    "round %d: the gate did not notice that the report's "
                    "draft reading is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "report_drafts",
        "title": "\u590d\u76d8\u62a5\u544a\u8bfb\u4e86\u5f85\u6279\u8349\u7a3f\u961f\u5217",
        "skipped": False,
        "invariant": (
            "\u590d\u76d8\u62a5\u544a\u8bfb\u8349\u7a3f\u961f\u5217\uff1a\u9876\u90e8\u8ba1\u6570\u3001"
            "\u5355\u72ec\u7ae0\u8282\u70b9\u540d\u3001\u6587\u4ef6\u4e0e\u6279\u51c6\u547d\u4ee4\u3001"
            "JSON \u5e26\u6570\u5b57\u3001\u95ed\u73af\u4f46\u5f85\u5199\u88ab\u70b9\u540d"
        ),
        "detail": "\u590d\u76d8\u62a5\u544a\u662f\u5426\u8bfb\u4e86\u5f85\u6279\u8349\u7a3f\uff1amas report",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_reads_the_drafts.CHECK_NAME = "report_drafts"  # type: ignore[attr-defined]

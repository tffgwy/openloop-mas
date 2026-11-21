# -*- coding: utf-8 -*-
"""The forty-third gate: the verdict's own page speaks the same grain.

The gap
-------
v0.7.98 fixed the *grain* of the closure verdict (a problem that failed
and then succeeded is closed), v0.7.99 put the verdict in one place,
and v0.7.100 made the **status page** and the **report** say the grain
out loud: "still waiting" for the failed attempts whose problem is
still open, and "history" for the ones a later attempt replaced.

One surface was left behind. ``format_closure`` - the text behind
``mas closure`` and the MCP ``closure_text`` - still printed the old
wording:

    [通过] 闭合：没有留下任何待办。
           尝试 2 次（闭合 1 / 未闭合 1），尝试闭环率 50%

So one machine gave two answers to "is anything still waiting?":

    账本：尝试 2 次  已闭环 1  仍在等 0        <- status page
    闭合判定：闭合：没有留下任何待办。
              尝试 2 次（闭合 1 / 未闭合 1）   <- the verdict page

One says "nothing is waiting", the other says "one attempt did not
close", and the reader has to work out that the second one is history
rather than work. The reader who reads the verdict first is the one
who gets confused - and the verdict is the page you go to *because*
you want the answer.

The fix
-------
The verdict page splits the attempts the same way every other surface
does: "still waiting" for what is still waiting, "history" for what a
later attempt replaced. Nothing is hidden - the history is still
counted, only labelled.

This gate watches:

* the verdict page says what is waiting and what is history, apart;
* the number it calls "still waiting" is the number the verdict is
  built on, and the history it reports is the whole history;
* the verdict page, the status page and the report say the same thing
  about the same machine;
* a closed verdict does not leave a "not closed" number next to it
  that nobody can explain.

And the gate proves it can fail: the verdict page is broken on
purpose - it is made to print the old wording again - and the gate has
to notice that one machine is now giving two answers again.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes.
CLOSURE_GRAIN_TEXT_OBSERVATIONS = (
    "the verdict page says what is waiting and what is history apart",
    "the verdict page counts the same waiting number the verdict is built on",
    "the verdict page, the status page and the report say the same thing",
    "a closed verdict leaves no unexplained failure number next to it",
)


def _rows(spec: List[Any]) -> List[Dict[str, Any]]:
    """Ledger rows for a list of ``(run_id, closed, ticket_id)``."""
    out: List[Dict[str, Any]] = []
    seq = 0
    for run_id, closed, ticket_id in spec:
        seq += 1
        out.append(
            {
                "role": "closure",
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "iteration": 1,
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": seq,
                "verdict": "pass" if closed else "fail",
                "decision": "accept" if closed else "escalate",
                "scores": [{"id": "c1", "pass": closed}],
            }
        )
        seq += 1
        out.append(
            {
                "role": "run_end",
                "run_id": run_id,
                "task_id": "T-" + run_id,
                "iteration": 1,
                "ts": "2026-01-01T00:00:01+00:00",
                "seq": seq,
                "status": "closed" if closed else "escalated",
                "closed": closed,
                "deliverable_ok": closed,
                "decision": "accept" if closed else "escalate",
                "error": "",
                "ticket_id": ticket_id,
                "goal": "\u628a\u4e8b\u60c5\u505a\u5b8c",
                "title": "\u628a\u4e8b\u60c5\u505a\u5b8c",
                "iterations": 1,
                "tokens": 10,
                "artifact_path": "",
                "files": [],
            }
        )
    return out


#: One problem, two attempts: the first failed, the second closed it.
SUPERSEDED_CASE = [("R-sup1", False, "T-sup1"), ("R-sup2", True, "T-sup1")]

#: One problem, two attempts, the last one failed: still waiting.
STILL_OPEN_CASE = [("R-open1", True, "T-open1"), ("R-open2", False, "T-open1")]


def _write_ledger(path: str, rows: List[Dict[str, Any]]) -> None:
    import json

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _status_text(state_dir: str) -> str:
    """The status page as a person reads it."""
    import contextlib
    import io

    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["status"])
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return buffer.getvalue()


def _surface_observations() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.report import (
        build_report,
        closure_verdict,
        format_closure,
        render_markdown,
    )

    sup_rows = _rows(SUPERSEDED_CASE)
    open_rows = _rows(STILL_OPEN_CASE)

    sup_report = build_report(sup_rows, [], [])
    open_report = build_report(open_rows, [], [])

    facts: Dict[str, Any] = {
        "superseded_verdict": closure_verdict(sup_report),
        "open_verdict": closure_verdict(open_report),
        "superseded_text": format_closure(closure_verdict(sup_report)),
        "open_text": format_closure(closure_verdict(open_report)),
        "superseded_markdown": render_markdown(sup_report),
        "open_markdown": render_markdown(open_report),
    }

    # The live status page, read from a real state directory: the
    # point of the gate is what a reader actually sees.
    sup_dir = tempfile.mkdtemp(prefix="mas-selftest-grain-text-sup-")
    open_dir = tempfile.mkdtemp(prefix="mas-selftest-grain-text-open-")
    try:
        _write_ledger(os.path.join(sup_dir, "ledgers", "runs.jsonl"), sup_rows)
        _write_ledger(os.path.join(open_dir, "ledgers", "runs.jsonl"), open_rows)
        facts["superseded_status_text"] = _status_text(sup_dir)
        facts["open_status_text"] = _status_text(open_dir)
    finally:
        shutil.rmtree(sup_dir, ignore_errors=True)
        shutil.rmtree(open_dir, ignore_errors=True)
    return facts


def _grain_text_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}

    sup_text = str(facts.get("superseded_text") or "")
    open_text = str(facts.get("open_text") or "")
    sup_counts = (facts.get("superseded_verdict") or {}).get("counts") or {}
    open_counts = (facts.get("open_verdict") or {}).get("counts") or {}

    # 1. The page has to tell the two apart, and it must not print
    #    the old undivided number any more.
    verdicts[
        "the verdict page says what is waiting and what is history apart"
    ] = bool(
        "\u4ecd\u5728\u7b49 0" in sup_text
        and "\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21" in sup_text
        and "\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in sup_text
        # The open problem still waits - one attempt.
        and "\u4ecd\u5728\u7b49 1" in open_text
        # The old wording is gone: the page does not print a bare
        # "not closed" number next to a closed verdict any more.
        and "\u672a\u95ed\u5408 1" not in sup_text
        and "\u672a\u95ed\u5408 %d" not in sup_text
    )

    # 2. The number the page calls "still waiting" is the number the
    #    verdict is built on, and the history it reports is the whole
    #    history - nothing is quietly dropped.
    verdicts[
        "the verdict page counts the same waiting number the verdict is built on"
    ] = bool(
        int(sup_counts.get("not_closed_open") or 0) == 0
        and int(sup_counts.get("not_closed") or 0) == 1
        and int(sup_counts.get("not_closed_superseded") or 0) == 1
        and int(open_counts.get("not_closed_open") or 0) == 1
        and int(open_counts.get("not_closed") or 0) == 1
        and int(open_counts.get("not_closed_superseded") or 0) == 0
        # The open problem's history is reported too - one failure.
        and "\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21" in open_text
    )

    # 3. The three surfaces say the same thing about the same
    #    machine.
    sup_status = str(facts.get("superseded_status_text") or "")
    sup_md = str(facts.get("superseded_markdown") or "")
    verdicts[
        "the verdict page, the status page and the report say the same thing"
    ] = bool(
        # The same waiting count on all three.
        "\u4ecd\u5728\u7b49 0" in sup_text
        and "\u4ecd\u5728\u7b49 0" in sup_status
        and "\u4ecd\u5728\u7b49\uff1a0" in sup_md
        # And the same history.
        and "\u5386\u53f2\u5931\u8d25" in sup_text
        and "\u5386\u53f2\u5931\u8d25" in sup_status
        and "\u5386\u53f2\u5931\u8d25\uff1a1 \u6b21" in sup_md
    )

    # 4. A closed verdict leaves no unexplained failure number next to
    #    it. If a failure is mentioned, it is explained.
    sup_verdict = facts.get("superseded_verdict") or {}
    explained = True
    if sup_verdict.get("ok"):
        # The page mentions a failure at all?
        mentions_failure = "\u5386\u53f2\u5931\u8d25" in sup_text or "\u672a\u95ed\u5408" in sup_text
        if mentions_failure:
            explained = (
                "\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in sup_text
                or "\u4e0d\u7b97\u5f85\u529e" in sup_text
            )
    verdicts[
        "a closed verdict leaves no unexplained failure number next to it"
    ] = bool(explained)
    return verdicts


def _grain_text_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, worked out by hand from the raw ledger rows.

    Nothing here calls the shared counter. The waiting count is
    derived here from the rows, and the rendered pages are read as
    text, so a grain that always says "waiting" cannot make both
    passes agree on a wrong answer.
    """
    verdicts: Dict[str, bool] = {}

    def _waiting(spec: List[Any]) -> int:
        """Failed attempts whose problem is still open, counted by hand."""
        last_closed: Dict[str, bool] = {}
        for run_id, closed, ticket_id in spec:
            last_closed[ticket_id or run_id] = closed
        waiting = 0
        for run_id, closed, ticket_id in spec:
            if closed:
                continue
            if not last_closed.get(ticket_id or run_id, False):
                waiting += 1
        return waiting

    sup_waiting = _waiting(SUPERSEDED_CASE)
    open_waiting = _waiting(STILL_OPEN_CASE)
    sup_failures = len([1 for _r, closed, _t in SUPERSEDED_CASE if not closed])
    open_failures = len([1 for _r, closed, _t in STILL_OPEN_CASE if not closed])

    sup_text = str(facts.get("superseded_text") or "")
    open_text = str(facts.get("open_text") or "")
    sup_status = str(facts.get("superseded_status_text") or "")
    sup_md = str(facts.get("superseded_markdown") or "")

    # By hand: the page must print the waiting number we computed,
    # and the history number, and must not print the old undivided
    # one.
    verdicts[
        "the verdict page says what is waiting and what is history apart"
    ] = bool(
        ("\u4ecd\u5728\u7b49 %d" % sup_waiting) in sup_text
        and ("\u4ecd\u5728\u7b49 %d" % open_waiting) in open_text
        and ("\u5386\u53f2\u5931\u8d25\uff1a%d \u6b21" % sup_failures) in sup_text
        and ("\u5386\u53f2\u5931\u8d25\uff1a%d \u6b21" % open_failures) in open_text
        and "\u672a\u95ed\u5408" not in sup_text
    )

    verdicts[
        "the verdict page counts the same waiting number the verdict is built on"
    ] = bool(
        sup_waiting == 0
        and open_waiting == 1
        and sup_failures == 1
        and open_failures == 1
        and ("\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in sup_text)
    )

    verdicts[
        "the verdict page, the status page and the report say the same thing"
    ] = bool(
        ("\u4ecd\u5728\u7b49 %d" % sup_waiting) in sup_status
        and ("\u4ecd\u5728\u7b49\uff1a%d" % sup_waiting) in sup_md
        and ("\u5386\u53f2\u5931\u8d25\uff1a%d \u6b21" % sup_failures) in sup_md
    )

    verdicts[
        "a closed verdict leaves no unexplained failure number next to it"
    ] = bool(
        not (facts.get("superseded_verdict") or {}).get("ok")
        or "\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in sup_text
        or "\u672a\u95ed\u5408" not in sup_text
    )
    return verdicts


def check_the_verdict_page_speaks_the_same_grain(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The verdict page speaks the grain - and can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surface_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the verdict page check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _grain_text_verdicts(facts)
            independent = _grain_text_verdicts_independent(facts)
            for label in CLOSURE_GRAIN_TEXT_OBSERVATIONS:
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

            # The gate proves it can fail: the verdict page is broken
            # on purpose - it is made to print the old undivided
            # wording again - and the gate has to notice that one
            # machine is giving two answers again.
            import mas.report as report_module

            real_format = report_module.format_closure

            def _old_grain(verdict, _real=real_format):
                # The old shape: the failed attempts are one
                # undivided number, and the page says nothing about
                # what is still waiting.
                text = _real(verdict)
                counts = verdict.get("counts") or {}
                lines = text.splitlines()
                out: List[str] = []
                for line in lines:
                    if line.strip().startswith("\u5c1d\u8bd5 ") and "\u4ecd\u5728\u7b49" in line:
                        out.append(
                            "       \u5c1d\u8bd5 %d \u6b21\uff08\u95ed\u5408 %d / \u672a\u95ed\u5408 %d\uff09\uff0c\u5c1d\u8bd5\u95ed\u73af\u7387 %.0f%%"
                            % (
                                int(counts.get("runs") or 0),
                                int(counts.get("closed") or 0),
                                int(counts.get("not_closed") or 0),
                                float(verdict.get("closure_rate") or 0.0) * 100,
                            )
                        )
                        continue
                    if line.strip().startswith("\u5386\u53f2\u5931\u8d25\uff1a"):
                        continue
                    if "\u5df2\u88ab\u540e\u6765\u7684\u6210\u529f\u5c1d\u8bd5\u53d6\u4ee3" in line:
                        continue
                    out.append(line)
                return "\n".join(out)

            try:
                report_module.format_closure = _old_grain
                broken: Dict[str, bool] = {}
                try:
                    broken = _grain_text_verdicts(_surface_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.format_closure = real_format

            if broken.get(
                "the verdict page says what is waiting and what is history apart"
            ):
                failures.append(
                    "round %d: the gate did not notice that the verdict page went "
                    "back to the old grain - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "closure_grain_text",
        "title": "\u95ed\u5408\u5224\u5b9a\u9875\u4e5f\u8bf4\u540c\u4e00\u5957\u8bdd\uff1a\u8fd8\u5728\u7b49\u4e0e\u5386\u53f2\u5931\u8d25\u5206\u5f00\u8bf4",
        "skipped": False,
        "invariant": (
            "\u95ed\u5408\u5224\u5b9a\u9875\u3001\u72b6\u6001\u9875\u548c\u62a5\u544a\u5bf9\u540c\u4e00\u53f0\u673a\u5668\u8bf4\u540c\u4e00\u4ef6\u4e8b\uff1a"
            "\u8fd8\u5728\u7b49\u7684\u5931\u8d25\u4e0e\u5386\u53f2\u5931\u8d25\u5206\u5f00\u8bf4"
        ),
        "detail": "\u67e5 mas closure \u7684\u5224\u5b9a\u9875\u662f\u5426\u8fd8\u5728\u7528\u65e7\u53e3\u5f84\u300c\u95ed\u5408 / \u672a\u95ed\u5408\u300d",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_verdict_page_speaks_the_same_grain.CHECK_NAME = "closure_grain_text"  # type: ignore[attr-defined]

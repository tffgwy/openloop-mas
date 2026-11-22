# -*- coding: utf-8 -*-
"""The forty-sixth gate: the replay listens to the queue too.

The gap
-------
v0.8.4 made the **report** listen to the human ticket queue: when a
report was about a single run, the attempt that actually closed the
problem happened later and was not in that ledger, so the report read
the queue to find out whether the failure was still work in progress.

Two surfaces were left behind, and they are the two a reader and an
agent reach for first.

The **replay** (``mas replay``) is the screen that shows what the loop
*did*, step by step. It built its threads from the ledger alone. The
command line had been handing it the queue since v0.8.4 - and the
replay threw it away. So the replay said:

    按问题分组：
      [待处理] 把事情做完　1 次尝试　1 轮

while the report said, about the same problem:

    | 把事情做完 | 已解决 |

Two answers from one machine, about one problem, on two screens. And
the replay is where the reader goes to see *what happened* - so the
replay is where the wrong answer does the most damage.

The **MCP** replay was worse: it did not read the queue at all. Not
"read it and got it wrong" - never opened it. An agent driving Claude,
GPT or Kimi through MCP got a replay that could not tell a finished
problem from an open one, while the human at the same terminal saw
the right answer.

And there was a third answer hiding in the same place. The report
kept a failed run's ``mas resume`` pointer when the queue had **not
been read** - because "nobody looked" is not "there is nothing to do".
The replay dropped it. So the replay told a reader to walk away from
an open problem whose state was merely unknown, in the direction that
hides work.

The fix
-------
The reading of the queue lives in one function -
``mas.report.queue_state()`` - and the four states of a ticket live in
one function - ``mas.report.ticket_state_of()``:

* ``waiting``  - the queue holds it and a person still has it;
* ``resolved`` - the queue holds it and it is finished work;
* ``lost``     - the queue was read and does not hold it;
* ``unknown``  - the queue was never read, so nothing is known.

The report, the replay and the MCP server all read them from there,
so they cannot describe one ticket three different ways.

This gate watches:

* the replay and the report agree about how many problems are open;
* the replay says **the same thing** about a problem the queue says
  a later attempt closed;
* the MCP replay reads the queue - the tool's answer and the
  command line's answer are the same answer;
* a replay whose queue was **never read** keeps its resume pointer,
  the same way the report does;
* a ticket the queue has **lost** gets no resume command, in either
  surface - ``mas resume`` would raise "unknown ticket";
* and the gate proves it can fail: the replay's thread building is
  broken on purpose - made to ignore the queue again - and the gate
  has to notice that the replay and the report have started arguing
  again.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

#: The observations the gate makes.
REPLAY_QUEUE_OBSERVATIONS = (
    "the replay and the report agree about how many problems are open",
    "the replay says the same thing about a problem the queue closed",
    "the MCP replay reads the queue",
    "a replay whose queue was never read keeps its resume pointer",
    "a ticket the queue has lost gets no resume command",
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
                "error": ""
                if closed
                else "stagnation: the deliverable did not change for 3 consecutive attempts",
                "ticket_id": ticket_id,
                "goal": "\u628a\u4e8b\u60c5\u5059\u505a\u5b8c",
                "title": "\u628a\u4e8b\u60c5\u5059\u505a\u5b8c",
                "iterations": 1,
                "tokens": 10,
                "artifact_path": "",
                "files": [],
            }
        )
    return out


#: One failed attempt, on a ticket a later attempt closed. Only the
#: failed attempt is in this ledger - that is the whole point.
REPLAY_QUEUE_CASE = [("R-fail", False, "T-one")]


def _ticket(
    ticket_id: str,
    state: str,
    closed_run_id: str = "",
    runs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """One ticket in the queue."""
    payload: Dict[str, Any] = {
        "goal": "\u628a\u4e8b\u60c5\u5059\u505a\u5b8c",
        "title": "\u628a\u4e8b\u60c5\u5059\u505a\u5b8c",
        "reason": "stagnation: the deliverable did not change",
        "runs": list(runs or []),
    }
    if closed_run_id:
        payload["closed_run_id"] = closed_run_id
    entry: Dict[str, Any] = {
        "id": ticket_id,
        "kind": "loop",
        "state": state,
        "created_at": "2026-01-01T00:00:00+00:00",
        "payload": payload,
    }
    if state == "resolved":
        entry["resolved_at"] = "2026-01-01T02:00:00+00:00"
        entry["decision"] = "accept"
        entry["note"] = "done"
    return entry


def _write_ledger(path: str, rows: List[Dict[str, Any]]) -> None:
    """Write the rows where the engine reads them from."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_ticket(directory: str, ticket: Dict[str, Any]) -> None:
    """Write one ticket where the engine reads it from."""
    os.makedirs(directory, exist_ok=True)
    with open(
        os.path.join(directory, "%s.json" % ticket.get("id")), "w", encoding="utf-8"
    ) as handle:
        handle.write(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n")


class _FakeLedger:
    """A ledger that holds exactly the rows the gate wrote."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = list(rows)

    def entries(self) -> List[Dict[str, Any]]:
        return list(self._rows)


class _FakeTickets:
    """The queue the MCP tool reads."""

    def __init__(self, tickets: List[Dict[str, Any]]) -> None:
        self._tickets = list(tickets)

    def list(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        if state is None:
            return list(self._tickets)
        return [t for t in self._tickets if str(t.get("state") or "") == state]


class _FakeEngine:
    """The smallest thing the MCP tool needs from an engine."""

    def __init__(
        self, rows: List[Dict[str, Any]], tickets: List[Dict[str, Any]]
    ) -> None:
        self.ledger = _FakeLedger(rows)
        self.tickets = _FakeTickets(tickets)


def _call_mcp(
    rows: List[Dict[str, Any]], tickets: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Call the MCP replay the way a caller would."""
    import mas.mcp_server as mcp_module

    real_engine = mcp_module._engine

    def _fake(_config, team=None):
        return _FakeEngine(rows, tickets)

    try:
        mcp_module._engine = _fake
        return mcp_module.handle_tool("mas_replay", {})
    finally:
        mcp_module._engine = real_engine


def _replay_text(state_dir: str) -> str:
    """The replay page, as a person reads it."""
    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["replay"])
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return buffer.getvalue()


def _surfaces() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.report import build_report, replay_trace

    rows = _rows(REPLAY_QUEUE_CASE)

    # The queue says a later attempt closed the problem.
    closed_ticket = _ticket("T-one", "resolved", closed_run_id="R-later", runs=["R-later"])
    # The ticket is still waiting: an ordinary pending problem.
    waiting_ticket = _ticket("T-one", "waiting_human", runs=["R-fail"])

    report = build_report(rows, [closed_ticket])
    trace = replay_trace(rows, [closed_ticket])
    unread_trace = replay_trace(rows)
    waiting_trace = replay_trace(rows, [waiting_ticket])

    # The live surfaces, read from a real state directory.
    state_dir = tempfile.mkdtemp(prefix="mas-selftest-replay-queue-")
    try:
        _write_ledger(os.path.join(state_dir, "ledgers", "runs.jsonl"), rows)
        _write_ticket(os.path.join(state_dir, "tickets"), closed_ticket)
        replay_text = _replay_text(state_dir)
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)

    return {
        "rows": rows,
        "report": report,
        "trace": trace,
        "unread_trace": unread_trace,
        "waiting_trace": waiting_trace,
        "replay_text": replay_text,
        "mcp": _call_mcp(rows, [closed_ticket]),
        "mcp_unread": _call_mcp(rows, []),
        "closed_ticket": closed_ticket,
    }


def _replay_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    report = facts.get("report") or {}
    trace = facts.get("trace") or {}
    unread = facts.get("unread_trace") or {}
    waiting = facts.get("waiting_trace") or {}
    mcp = facts.get("mcp") or {}
    mcp_unread = facts.get("mcp_unread") or {}
    text = str(facts.get("replay_text") or "")

    report_totals = report.get("totals") or {}
    threads = trace.get("threads") or []
    runs = trace.get("runs") or []

    return {
        "the replay and the report agree about how many problems are open": (
            int(trace.get("problems_open") or 0)
            == int(report_totals.get("problems_open") or 0)
        ),
        "the replay says the same thing about a problem the queue closed": (
            len(threads) == 1
            and bool(threads[0].get("closed"))
            and str(threads[0].get("closed_by") or "") == "R-later"
            and int(trace.get("problems_open") or 0) == 0
            and "\u5df2\u89e3\u51b3" in text
            and "\u5f85\u5904\u7406" not in text
        ),
        "the MCP replay reads the queue": (
            int(mcp.get("problems_open") or 0) == 0
            and len(mcp.get("threads") or []) == 1
            and bool((mcp.get("threads") or [{}])[0].get("closed"))
        ),
        "a replay whose queue was never read keeps its resume pointer": (
            len(unread.get("runs") or []) == 1
            and bool((unread.get("runs") or [{}])[0].get("ticket_resumable"))
            and str((unread.get("runs") or [{}])[0].get("ticket_state") or "") == "unknown"
        ),
        "a ticket the queue has lost gets no resume command": (
            # The queue was read and does not hold the ticket: the
            # ticket is lost, and `mas resume` would raise on it.
            len(mcp_unread.get("runs") or []) == 1
            and not bool((mcp_unread.get("runs") or [{}])[0].get("ticket_resumable"))
            and str((mcp_unread.get("runs") or [{}])[0].get("ticket_state") or "") == "lost"
            # And a ticket that is still waiting does get one.
            and len(waiting.get("runs") or []) == 1
            and bool((waiting.get("runs") or [{}])[0].get("ticket_resumable"))
            and str((waiting.get("runs") or [{}])[0].get("ticket_state") or "") == "waiting"
            and all(not r.get("ticket_resumable") for r in runs)
        ),
    }


def _replay_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw facts, not from the page.

    This pass does not look at the rendered text. It reads the report's
    own counts, the queue and the replay payload, and works out what
    the replay *should* say. If the two readings disagree, the gate
    says so - and the gate cannot be satisfied by a replay that merely
    looks right.
    """
    rows = facts.get("rows") or []
    ticket = facts.get("closed_ticket") or {}

    # What the queue says, read by hand: this ticket is resolved and
    # it names the attempt that closed the problem.
    queue_says_closed = (
        str(ticket.get("state") or "") == "resolved"
        and str((ticket.get("payload") or {}).get("closed_run_id") or "") == "R-later"
    )
    # And the ledger says: one failed attempt on that ticket.
    failed_attempts = [
        row
        for row in rows
        if str(row.get("role") or "") == "run_end" and not row.get("closed")
    ]
    ticket_id = str(failed_attempts[0].get("ticket_id") or "") if failed_attempts else ""

    trace = facts.get("trace") or {}
    report = facts.get("report") or {}
    unread = facts.get("unread_trace") or {}

    expected_open = 0 if queue_says_closed else 1

    return {
        "the replay and the report agree about how many problems are open": (
            int(trace.get("problems_open") or 0) == expected_open
            and int(trace.get("problems_open") or 0)
            == int((report.get("totals") or {}).get("problems_open") or 0)
        ),
        "the replay says the same thing about a problem the queue closed": (
            queue_says_closed
            and int(trace.get("problems_open") or 0) == 0
            and len(trace.get("threads") or []) == 1
            and bool((trace.get("threads") or [{}])[0].get("closed"))
            and str((trace.get("threads") or [{}])[0].get("closed_by") or "") == "R-later"
        ),
        "the MCP replay reads the queue": (
            int((facts.get("mcp") or {}).get("problems_open") or 0) == expected_open
            and len((facts.get("mcp") or {}).get("threads") or []) == 1
        ),
        "a replay whose queue was never read keeps its resume pointer": (
            # No queue was handed in, so the state of the ticket is
            # unknown - and unknown must keep the pointer.
            bool(ticket_id)
            and int(unread.get("problems_open") or 0) == 1
            and len(unread.get("runs") or []) == 1
            and bool((unread.get("runs") or [{}])[0].get("ticket_resumable"))
        ),
        "a ticket the queue has lost gets no resume command": (
            # The MCP call handed in an empty queue: it was read, and it
            # does not hold the ticket. The ticket is lost, not
            # finished, and `mas resume` would raise on it.
            bool(ticket_id)
            and len((facts.get("mcp_unread") or {}).get("runs") or []) == 1
            and not bool(
                ((facts.get("mcp_unread") or {}).get("runs") or [{}])[0].get(
                    "ticket_resumable"
                )
            )
            and int((facts.get("mcp_unread") or {}).get("problems_open") or 0) == 1
        ),
    }


def check_the_replay_listens_to_the_queue_too(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The forty-sixth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surfaces()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the replay check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _replay_verdicts(facts)
            independent = _replay_verdicts_independent(facts)
            for label in REPLAY_QUEUE_OBSERVATIONS:
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
            # The break is the **old bug itself**: the replay throws
            # the queue away, and the MCP tool never reads it. That is
            # not the same as breaking the shared function - the
            # shared function is read by the report *and* the replay,
            # so breaking it would make the two agree on a wrong
            # answer and the gate would see nothing. The break has to
            # be on the **wiring**: the queue must stop reaching the
            # replay, and then the two surfaces must be seen to argue.
            import mas.cli as cli_module
            import mas.mcp_server as mcp_module
            import mas.report as report_module

            real_report_replay = report_module.replay_trace
            real_mcp_replay = mcp_module.replay_trace
            real_cli_replay = cli_module.replay_trace

            def _drop_the_queue(rows, tickets=None, _real=real_report_replay):
                # The queue is read and thrown away - exactly what the
                # replay did before this release.
                return _real(rows)

            try:
                report_module.replay_trace = _drop_the_queue
                mcp_module.replay_trace = _drop_the_queue
                cli_module.replay_trace = _drop_the_queue
                broken: Dict[str, bool] = {}
                try:
                    broken = _replay_verdicts(_surfaces())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.replay_trace = real_report_replay
                mcp_module.replay_trace = real_mcp_replay
                cli_module.replay_trace = real_cli_replay

            if broken.get(
                "the replay and the report agree about how many problems are open"
            ):
                failures.append(
                    "round %d: the gate did not notice that the queue's word is "
                    "ignored - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
            if broken.get("the replay says the same thing about a problem the queue closed"):
                failures.append(
                    "round %d: the gate did not notice that the replay and the "
                    "report are arguing again" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "replay_listens_to_the_queue",
        "title": "\u56de\u653e\u4e5f\u542c\u5de5\u5355\u961f\u5217\u7684",
        "skipped": False,
        "invariant": (
            "\u5de5\u5355\u961f\u5217\u624d\u662f\u201c\u8fd8\u5728\u7b49\u201d\u7684\u6743\u5a01\uff1a"
            "\u62a5\u544a\u9762\u3001\u56de\u653e\u9762\u548c MCP \u9762\u8bfb\u540c\u4e00\u4e2a\u961f\u5217"
        ),
        "detail": (
            "\u67e5\u56de\u653e\u9762\u3001\u62a5\u544a\u9762\u4e0e MCP \u9762\u662f\u5426\u8bf4\u540c\u4e00\u4e2a\u53e3\u5f84"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_replay_listens_to_the_queue_too.CHECK_NAME = "replay_listens_to_the_queue"  # type: ignore[attr-defined]

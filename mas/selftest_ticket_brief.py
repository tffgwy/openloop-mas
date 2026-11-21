# -*- coding: utf-8 -*-
"""The forty-first gate: a ticket that a human can act on, an agent can too.

The gap
-------
``mas tickets`` prints what a ticket is. The goal it was working on,
the check it failed, the reason the loop gave up, the exact command
that continues it, the folder to edit. It was built that way on
purpose - an id and a kind is a note, not a handover.

The MCP ``mas_status`` tool printed the **ids** of the waiting tickets
and stopped there:

    "open_tickets": ["T-20261121-104405-7a81"]

An agent asking the status tool "what needs me?" got a list of
handles. It could not learn what any of them meant, what was stuck,
or how to continue - unless it went and called a second tool, and the
agent might not have had one.

That is the same failure this project has been fixing surface by
surface since v0.7.89: a capability that reaches the command line but
not the agent. The report learned to read the audit chain; the MCP
status learned to report closure; here the ticket queue learns to
speak for itself.

The fix is not a second copy of the reading. It is **one** function -
``mas.ticket_brief.ticket_brief`` - and the command line and the MCP
tool both read it.

So this gate watches:

* the MCP status tool carries the waiting tickets as briefs, not just
  ids;
* a brief says what the ticket is *about* (title, goal, the run), what
  it is *stuck on* (the unmet check, in words), *why* the loop stopped
  (the reason, in words), and *what to do next* (the command that
  continues it);
* the command line and the MCP tool describe the same ticket with the
  same words - one ticket, one reading;
* the old ``open_tickets`` id list is still there, because it is the
  handle every other tool takes.

And the gate proves it can fail: the brief is broken on purpose - it
is made to drop the "what is it stuck on" and the "how to continue" -
and the gate has to notice that an agent reading the status tool could
no longer tell what any of the tickets meant.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes.
TICKET_BRIEF_OBSERVATIONS = (
    "the MCP status tool carries the waiting tickets, not only their ids",
    "a brief says what the ticket is stuck on and what to do next",
    "the command line and the MCP tool read the ticket the same way",
    "the id list every other tool takes is still there",
)


def _ticket(
    ticket_id: str = "T-20261121-104405-7a81",
    state: str = "waiting_human",
    kind: str = "loop_escalation",
    goal: str = "写一份说明文档",
    issues: Any = None,
) -> Dict[str, Any]:
    """A ticket the way the engine leaves one behind."""
    return {
        "id": ticket_id,
        "state": state,
        "kind": kind,
        "payload": {
            "run_id": "R-285d764b0c",
            "goal": goal,
            "title": goal,
            "criteria": [{"kind": "contains", "value": "关键词"}],
            "issues": list(issues or ["文件里没有出现要求的字符串：关键词"]),
            "attempts": 5,
            "iterations_total": 5,
            "runs": ["R-285d764b0c"],
            "reason": "stagnation: the deliverable did not change for 3 consecutive attempts",
            "status": "escalated",
            "budget": {"max_iterations": 8},
            "workspace": "",
        },
    }


def _write_ledger(path: str, rows: List[Dict[str, Any]]) -> None:
    import json as _json
    import os as _os

    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(_json.dumps(row, ensure_ascii=False) + "\n")


def _seed_tickets(state_dir: str, tickets: List[Dict[str, Any]]) -> None:
    """Write a ticket queue the engine and the tools can both read.

    The ticket store keeps **one file per ticket**, named after the
    ticket, so the seed writes the same shape the store writes.
    """
    import json as _json
    import os as _os

    folder = _os.path.join(state_dir, "tickets")
    _os.makedirs(folder, exist_ok=True)
    for ticket in tickets:
        path = _os.path.join(folder, "%s.json" % ticket.get("id"))
        with open(path, "w", encoding="utf-8") as handle:
            _json.dump(ticket, handle, ensure_ascii=False, indent=2)


def _status_payload(state_dir: str) -> Dict[str, Any]:
    """The status page's JSON payload."""
    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["status", "--json"])
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return json.loads(buffer.getvalue())


def _tickets_text(state_dir: str) -> str:
    """The ticket queue as a person reads it."""
    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(["tickets"])
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return buffer.getvalue()


def _mcp_status_payload(state_dir: str) -> Dict[str, Any]:
    """The MCP status tool's payload."""
    from mas.mcp_server import handle_tool

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    try:
        return handle_tool("mas_status", {})
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved


def _surface_observations() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    tickets = [_ticket()]

    state_dir = tempfile.mkdtemp(prefix="mas-selftest-brief-")
    try:
        _write_ledger(
            os.path.join(state_dir, "ledgers", "runs.jsonl"),
            [
                {
                    "role": "closure",
                    "run_id": "R-285d764b0c",
                    "task_id": "T-1",
                    "ok": False,
                    "artifact_path": "",
                    "criteria": [],
                    "checks": [],
                    "ts": "2026-11-21T10:44:05Z",
                }
            ],
        )
        _seed_tickets(state_dir, tickets)
        facts: Dict[str, Any] = {
            "status": _status_payload(state_dir),
            "tickets_text": _tickets_text(state_dir),
            "mcp": _mcp_status_payload(state_dir),
            "tickets": tickets,
        }
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
    return facts


def _brief_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}

    mcp = facts.get("mcp") or {}
    briefs = list(mcp.get("waiting_tickets") or [])
    ids = list(mcp.get("open_tickets") or [])

    # 1. The MCP status tool carries the tickets, not only their ids.
    verdicts[
        "the MCP status tool carries the waiting tickets, not only their ids"
    ] = bool(briefs) and len(briefs) == len(ids) and bool(briefs[0].get("id"))

    brief = briefs[0] if briefs else {}

    # 2. A brief says what it is stuck on and what to do next.
    verdicts[
        "a brief says what the ticket is stuck on and what to do next"
    ] = bool(
        brief.get("blocked_on")
        and brief.get("reason_reading")
        and brief.get("next_command")
        and brief.get("title")
    )

    # 3. The command line and the MCP tool read the ticket the same way.
    text = str(facts.get("tickets_text") or "")
    same = False
    if brief.get("blocked_on"):
        same = brief["blocked_on"] in text
    if brief.get("reason_reading"):
        same = same and brief["reason_reading"] in text
    if brief.get("next_command"):
        same = same and brief["next_command"] in text
    verdicts["the command line and the MCP tool read the ticket the same way"] = same

    # 4. The id list every other tool takes is still there.
    verdicts["the id list every other tool takes is still there"] = bool(ids) and (
        ids[0] == (brief.get("id") or "")
    )
    return verdicts


def _brief_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, worked out by hand from the ticket itself.

    Nothing here calls the shared brief. The verdict is derived from
    the raw ticket and the two rendered surfaces, so the gate does not
    agree with the code just because the code agrees with itself.
    """
    verdicts: Dict[str, bool] = {}
    tickets = list(facts.get("tickets") or [])
    ticket = tickets[0] if tickets else {}
    payload = ticket.get("payload") or {}

    mcp = facts.get("mcp") or {}
    briefs = list(mcp.get("waiting_tickets") or [])
    ids = list(mcp.get("open_tickets") or [])
    brief = briefs[0] if briefs else {}
    text = str(facts.get("tickets_text") or "")

    # By hand: the id the ticket carries must be the id the status
    # tool lists.
    verdicts["the id list every other tool takes is still there"] = (
        bool(ids)
        and ids[0] == ticket.get("id")
        and ids[0] == brief.get("id")
    )

    # By hand: the ticket names a goal, an unmet check and a reason.
    # A brief that leaves any of those out cannot be acted on.
    goal = payload.get("goal") or ""
    issues = payload.get("issues") or []
    reason = payload.get("reason") or ""
    verdicts[
        "a brief says what the ticket is stuck on and what to do next"
    ] = bool(
        goal
        and issues
        and reason
        and brief.get("title")
        and brief.get("blocked_on")
        and brief.get("reason_reading")
        and str(brief.get("next_command") or "").startswith("mas resume ")
    )

    # By hand: the brief has to be there at all, and there has to be
    # one brief per id.
    verdicts[
        "the MCP status tool carries the waiting tickets, not only their ids"
    ] = len(briefs) == len(ids) and bool(briefs) and all(
        b.get("id") for b in briefs
    )

    # By hand: the two screens have to show the same words. The words
    # are taken from the ticket, so the check does not borrow the
    # brief's own rendering.
    shows = bool(text)
    for needle in (goal, str(issues[0]) if issues else "", "mas resume "):
        if needle and needle not in text:
            shows = False
    if brief.get("blocked_on") and brief["blocked_on"] not in text:
        shows = False
    verdicts["the command line and the MCP tool read the ticket the same way"] = shows
    return verdicts


def check_a_ticket_a_human_can_act_on_an_agent_can_too(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The ticket queue speaks for itself on every surface - and can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surface_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the ticket brief check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _brief_verdicts(facts)
            independent = _brief_verdicts_independent(facts)
            for label in TICKET_BRIEF_OBSERVATIONS:
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

            # The gate proves it can fail: the brief is broken on
            # purpose - it is made to drop the "what is it stuck on"
            # and the "how to continue" - and the gate has to notice
            # that an agent reading the status tool could no longer
            # tell what any of the waiting tickets meant.
            import mas.ticket_brief as brief_module

            real_brief = brief_module.ticket_brief

            def _bare_brief(ticket, _real=real_brief):
                # The old shape: a handle and nothing else.
                return {"id": (ticket or {}).get("id") or ""}

            try:
                brief_module.ticket_brief = _bare_brief
                broken: Dict[str, bool] = {}
                try:
                    broken = _brief_verdicts(_surface_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                brief_module.ticket_brief = real_brief

            if broken.get(
                "a brief says what the ticket is stuck on and what to do next"
            ):
                failures.append(
                    "round %d: the gate did not notice that the brief lost what "
                    "the ticket is stuck on and what to do next - it cannot fail, "
                    "so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "ticket_brief",
        "title": "工单在每一面都说得清：人看得懂，智能体也看得懂",
        "skipped": False,
        "invariant": (
            "工单不只是一串 ID：命令行和 MCP 读同一份工单摘要，"
            "说清卡在哪、为什么停、接着怎么跑"
        ),
        "detail": "查 MCP 状态工具给的工单是不是只有 ID，命令行和它是不是说的是同一件事",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_ticket_a_human_can_act_on_an_agent_can_too.CHECK_NAME = "ticket_brief"  # type: ignore[attr-defined]

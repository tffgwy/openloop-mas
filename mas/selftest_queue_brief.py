# -*- coding: utf-8 -*-
"""The forty-second gate: the ticket queue itself is a handover, on every surface.

The gap
-------
v0.8.0 taught the **status** tool to carry ticket briefs. But the queue
had a second home, and it was still raw:

    $ mas tickets --json
    {"tickets": [{"id": "T-...", "state": "waiting_human",
                  "payload": {"reason": "stagnation: ...", "budget": {...}}}]}

    $ mas_tickets (MCP)
    {"tickets": [{"id": "T-...", "state": "waiting_human",
                  "payload": {"reason": "stagnation: ...", "budget": {...}}}]}

Both surfaces handed back the engine's **raw record**. An agent or a
script reading that record could see the title and the raw reason
string, but:

* *why* the loop stopped was only in the raw reason text, which the
  reader has to interpret - the same interpretation the command line
  already does in words;
* *how to continue* was nowhere at all. The continuation command
  (with the widened budget that keeps the loop from hitting the same
  ceiling again) existed only in the human text layout;
* *what it is stuck on* - the first unmet check, in words - existed
  only in the human text layout.

So the one tool whose whole job is "list the tickets" was the one
surface where a ticket could not be acted on. A status tool can be
polled; a queue can be polled too, and polling it is what an agent
does when it asks "what is waiting for me?".

The fix
-------
The queue reads the **same** brief function as the status tool and
the command line - one reading of a ticket, three surfaces. The raw
tickets are still there, under the key they have always lived under
(``tickets``), because they are the engine's own record and anything
already reading them must keep working. The briefs are added beside
them, never in place of them.

This gate watches:

* the MCP queue tool carries briefs next to the raw tickets;
* the command line's JSON queue carries briefs next to the raw
  tickets;
* a brief in the queue says what it is stuck on, why it stopped and
  what to do next - the queue is a handover, not a listing;
* the queue surfaces and the status tool describe the same ticket
  with the same words;
* the raw tickets are still there, so nothing that already read them
  breaks.

And the gate proves it can fail: the brief is broken on purpose - it
is made to drop the "how to continue" - and the gate has to notice
that a reader of the queue could no longer pick the ticket up.
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
QUEUE_BRIEF_OBSERVATIONS = (
    "the MCP queue tool carries briefs next to the raw tickets",
    "the command line's JSON queue carries briefs next to the raw tickets",
    "a queued brief says what to do next, so the queue is a handover",
    "the queue and the status tool describe the same ticket the same way",
    "the raw tickets are still there, so nothing that read them breaks",
)


def _ticket(
    ticket_id: str = "T-20261121-104405-7a81",
    state: str = "waiting_human",
    kind: str = "loop_escalation",
    goal: str = "写一份说明文档",
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
            "issues": ["文件里没有出现要求的字符串：关键词"],
            "attempts": 5,
            "iterations_total": 5,
            "runs": ["R-285d764b0c"],
            "reason": "stagnation: the deliverable did not change for 3 consecutive attempts",
            "status": "escalated",
            "budget": {"max_iterations": 8},
            "workspace": "",
        },
    }


def _seed_tickets(state_dir: str, tickets: List[Dict[str, Any]]) -> None:
    """Write a ticket queue the engine and the tools can both read."""
    import json as _json
    import os as _os

    folder = _os.path.join(state_dir, "tickets")
    _os.makedirs(folder, exist_ok=True)
    for ticket in tickets:
        path = _os.path.join(folder, "%s.json" % ticket.get("id"))
        with open(path, "w", encoding="utf-8") as handle:
            _json.dump(ticket, handle, ensure_ascii=False, indent=2)


def _with_state_dir(state_dir: str, fn: Any) -> Any:
    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    try:
        return fn()
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved


def _cli_tickets_json(state_dir: str) -> Dict[str, Any]:
    """``mas tickets --json``."""
    from mas.cli import main

    def _run() -> Dict[str, Any]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            main(["tickets", "--json"])
        return json.loads(buffer.getvalue())

    return _with_state_dir(state_dir, _run)


def _mcp_tickets(state_dir: str) -> Dict[str, Any]:
    """The MCP ``mas_tickets`` tool."""
    from mas.mcp_server import handle_tool

    def _run() -> Dict[str, Any]:
        return handle_tool("mas_tickets", {})

    return _with_state_dir(state_dir, _run)


def _mcp_status(state_dir: str) -> Dict[str, Any]:
    """The MCP ``mas_status`` tool."""
    from mas.mcp_server import handle_tool

    def _run() -> Dict[str, Any]:
        return handle_tool("mas_status", {})

    return _with_state_dir(state_dir, _run)


def _surface_observations() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    tickets = [_ticket()]
    state_dir = tempfile.mkdtemp(prefix="mas-selftest-queue-")
    try:
        _seed_tickets(state_dir, tickets)
        facts: Dict[str, Any] = {
            "cli": _cli_tickets_json(state_dir),
            "mcp_queue": _mcp_tickets(state_dir),
            "mcp_status": _mcp_status(state_dir),
            "raw": tickets,
        }
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
    return facts


def _is_brief(item: Any) -> bool:
    """A brief, not a raw ticket.

    The tell is the flat field set: a raw ticket nests everything
    under ``payload`` and carries no reading of its own.
    """
    if not isinstance(item, dict):
        return False
    if isinstance(item.get("payload"), dict):
        return False
    return bool(item.get("id")) and bool(item.get("title"))


def _queue_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}

    cli = facts.get("cli") or {}
    mcp = facts.get("mcp_queue") or {}

    cli_briefs = list(cli.get("briefs") or [])
    mcp_briefs = list(mcp.get("briefs") or [])

    # 1. The MCP queue tool carries briefs next to the raw tickets.
    verdicts[
        "the MCP queue tool carries briefs next to the raw tickets"
    ] = bool(mcp_briefs) and all(_is_brief(b) for b in mcp_briefs)

    # 2. The command line's JSON queue carries briefs next to the raw
    #    tickets.
    verdicts[
        "the command line's JSON queue carries briefs next to the raw tickets"
    ] = bool(cli_briefs) and all(_is_brief(b) for b in cli_briefs)

    brief = (mcp_briefs or cli_briefs or [{}])[0]

    # 3. A queued brief says what to do next.
    verdicts[
        "a queued brief says what to do next, so the queue is a handover"
    ] = bool(
        brief.get("blocked_on")
        and brief.get("reason_reading")
        and str(brief.get("next_command") or "").startswith("mas resume ")
    )

    # 4. The queue and the status tool describe the same ticket the
    #    same way.
    status_briefs = list((facts.get("mcp_status") or {}).get("waiting_tickets") or [])
    status_brief = status_briefs[0] if status_briefs else {}
    same = bool(status_brief)
    for key in ("id", "title", "blocked_on", "reason_reading", "next_command"):
        if brief.get(key) != status_brief.get(key):
            same = False
    verdicts[
        "the queue and the status tool describe the same ticket the same way"
    ] = same

    # 5. The raw tickets are still there, under the same key they have
    #    always been under.
    raw_cli = cli.get("tickets")
    raw_mcp = mcp.get("tickets")
    verdicts[
        "the raw tickets are still there, so nothing that read them breaks"
    ] = bool(raw_cli) and bool(raw_mcp) and all(
        isinstance(t, dict) and isinstance(t.get("payload"), dict)
        for t in list(raw_cli) + list(raw_mcp)
    )
    return verdicts


def _queue_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, worked out by hand from the raw ticket.

    Nothing here calls the shared brief. The verdict is derived from
    the raw ticket and the two rendered payloads, so the gate does not
    agree with the code just because the code agrees with itself.
    """
    verdicts: Dict[str, bool] = {}
    raw = list(facts.get("raw") or [])
    ticket = raw[0] if raw else {}
    payload = ticket.get("payload") or {}

    cli = facts.get("cli") or {}
    mcp = facts.get("mcp_queue") or {}
    cli_briefs = list(cli.get("briefs") or [])
    mcp_briefs = list(mcp.get("briefs") or [])
    brief = (mcp_briefs or cli_briefs or [{}])[0]

    # By hand: a raw ticket nests under "payload"; a brief is flat and
    # carries the reading the raw ticket does not.
    def _flat(item: Any) -> bool:
        return bool(
            isinstance(item, dict)
            and not isinstance(item.get("payload"), dict)
            and item.get("id")
            and item.get("title")
        )

    verdicts["the MCP queue tool carries briefs next to the raw tickets"] = bool(
        mcp_briefs
    ) and all(_flat(b) for b in mcp_briefs)
    verdicts[
        "the command line's JSON queue carries briefs next to the raw tickets"
    ] = bool(cli_briefs) and all(_flat(b) for b in cli_briefs)

    # By hand: the ticket names a goal, an unmet check and a reason,
    # and a queued brief has to carry the reading and the way on.
    goal = payload.get("goal") or ""
    issues = payload.get("issues") or []
    reason = payload.get("reason") or ""
    verdicts[
        "a queued brief says what to do next, so the queue is a handover"
    ] = bool(
        goal
        and issues
        and reason
        and brief.get("title")
        and brief.get("blocked_on")
        and brief.get("reason_reading")
        and str(brief.get("next_command") or "").startswith("mas resume ")
    )

    # By hand: the queue and the status tool have to agree, field by
    # field, about the same ticket.
    status_briefs = list((facts.get("mcp_status") or {}).get("waiting_tickets") or [])
    status_brief = status_briefs[0] if status_briefs else {}
    same = bool(status_brief)
    for key in ("id", "title", "blocked_on", "reason_reading", "next_command"):
        if brief.get(key) != status_brief.get(key):
            same = False
    verdicts[
        "the queue and the status tool describe the same ticket the same way"
    ] = same

    # By hand: the raw record has to survive next to the brief, under
    # the key it has always lived under.
    verdicts[
        "the raw tickets are still there, so nothing that read them breaks"
    ] = (
        bool(cli.get("tickets"))
        and bool(mcp.get("tickets"))
        and isinstance((cli.get("tickets") or [{}])[0].get("payload"), dict)
        and isinstance((mcp.get("tickets") or [{}])[0].get("payload"), dict)
    )
    return verdicts


def check_the_ticket_queue_is_a_handover_everywhere(
    iterations: int = 5,
) -> Dict[str, Any]:
    """The queue speaks for itself on every surface - and can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surface_observations()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the queue brief check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _queue_verdicts(facts)
            independent = _queue_verdicts_independent(facts)
            for label in QUEUE_BRIEF_OBSERVATIONS:
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
            # purpose - it is made to drop the "how to continue" - and
            # the gate has to notice that a reader of the queue could
            # no longer pick the ticket up.
            import mas.ticket_brief as brief_module

            real_brief = brief_module.ticket_brief

            def _no_way_on(ticket, _real=real_brief):
                # The old shape: what the ticket is about, but no way
                # to continue it.
                out = dict(_real(ticket))
                out.pop("next_command", None)
                out.pop("handback_command", None)
                out.pop("handback_workspace_command", None)
                return out

            try:
                brief_module.ticket_brief = _no_way_on
                broken: Dict[str, bool] = {}
                try:
                    broken = _queue_verdicts(_surface_observations())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                brief_module.ticket_brief = real_brief

            if broken.get(
                "a queued brief says what to do next, so the queue is a handover"
            ):
                failures.append(
                    "round %d: the gate did not notice that a queued ticket lost "
                    "the way to continue it - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "queue_brief",
        "title": "工单队列在每一面都是交接：MCP 和命令行 JSON 都带得动下一步",
        "skipped": False,
        "invariant": (
            "工单队列不只是一份原始记录：MCP 与命令行 JSON 读同一份摘要，"
            "原始记录仍然保留"
        ),
        "detail": "查 MCP 工单队列工具和命令行 JSON 队列是不是只给原始 payload",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_ticket_queue_is_a_handover_everywhere.CHECK_NAME = "queue_brief"  # type: ignore[attr-defined]

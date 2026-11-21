"""Turn a ticket into something a reader can act on.

The gap this closes: ``mas tickets`` prints what a ticket is - the goal
it was working on, the check it failed, the reason the loop gave up,
the command that continues it. The MCP ``mas_status`` tool printed the
**ids** of the waiting tickets and nothing else.

That is the same failure the project has been fixing surface by
surface: a capability that reaches the command line but not the agent.
An agent reading the status tool saw ``["T-2026...-a1b2"]`` and had to
call a second tool to learn what any of it meant - and if the agent
only had the status tool, it had no way to learn anything at all.

So the reading of a ticket lives in one place, here, and both surfaces
read it. The command line keeps its own richer layout (it can print
blank lines and indentation); what it borrows from here is the *facts*,
so the two cannot say different things about the same ticket.

What a brief carries:

* ``id`` / ``state`` / ``kind`` - which ticket, in what condition;
* ``title`` / ``goal`` - what it was working on;
* ``run_id`` / ``runs`` - which attempts belong to this one problem;
* ``blocked_on`` - the first unmet criterion, in words;
* ``reason`` / ``reason_reading`` - why the loop stopped, in words;
* ``next_command`` - the command that continues it and does not
  dead-end, plus the handback command;
* ``workspace`` - where the files are, if a human would edit them;
* ``attempts`` / ``rounds_total`` - how much this problem has cost.

The rule: a ticket that a human can act on must also be a ticket an
agent can act on. An id is a handle, not a handover.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mas.reason_text import continuation_command, reason_hint, reason_text
from mas.verdict_text import issue_reading


def ticket_brief(ticket: Dict[str, Any]) -> Dict[str, Any]:
    """A ticket, compressed into the facts a reader needs to act.

    The ticket dict comes from ``engine.tickets.list()``. Every field
    here is read from the ticket itself - this function never looks
    anywhere else, so a brief cannot disagree with the ticket it came
    from.
    """
    ticket = ticket or {}
    payload = ticket.get("payload") or {}
    ticket_id = str(ticket.get("id") or "")
    state = str(ticket.get("state") or "")

    title = str(payload.get("title") or payload.get("goal") or "(无标题)")
    goal = str(payload.get("goal") or "")
    criteria = payload.get("criteria") or []
    issues = payload.get("issues") or []
    reason = str(payload.get("reason") or "")

    # The first unmet check, in words. A ticket that says "it failed"
    # without saying "on what" sends the reader back to the ledger to
    # find out.
    blocked_on = ""
    if issues:
        blocked_on = issue_reading(issues[0], criteria)

    runs = [str(r) for r in (payload.get("runs") or []) if r]
    workspace = str(payload.get("workspace") or "").strip()

    brief: Dict[str, Any] = {
        "id": ticket_id,
        "state": state,
        "kind": str(ticket.get("kind") or ""),
        "title": title,
        "goal": goal,
        "run_id": str(payload.get("run_id") or ""),
        "runs": runs,
        "attempts": int(payload.get("attempts") or 0),
        "rounds_total": int(
            payload.get("iterations_total") or payload.get("attempts_total") or 0
        ),
        "blocked_on": blocked_on,
        "reason": reason,
        "reason_reading": reason_text(reason) if reason else "",
        "reason_hint": reason_hint(reason) if reason else "",
        "workspace": workspace,
        "criteria_count": len(criteria),
        "unmet_count": len(issues),
    }

    if state == "waiting_human":
        # The command that continues the work. It is the same command
        # the command line prints, built by the same function - so an
        # agent that runs it gets the widened budget too.
        brief["next_command"] = continuation_command(
            ticket_id, reason, payload.get("budget")
        )
        brief["handback_command"] = (
            "mas handback %s --file <your-file>" % ticket_id
        )
        if workspace:
            brief["handback_workspace_command"] = (
                'mas handback %s --workspace "%s"' % (ticket_id, workspace)
            )

    note = str(ticket.get("note") or "")
    if note:
        brief["human_note"] = note
    decision = str(ticket.get("decision") or "")
    if decision:
        brief["decision"] = decision
    return brief


def ticket_briefs(tickets: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Briefs for a list of tickets, in the order they were given."""
    return [ticket_brief(t) for t in (tickets or [])]

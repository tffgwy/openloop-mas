# -*- coding: utf-8 -*-
"""v0.8.0: a ticket that a human can act on, an agent can act on too.

The new capability
------------------
``mas tickets`` prints what a ticket is: the goal, the check it failed,
the reason the loop gave up, the command that continues it. The MCP
``mas_status`` tool printed the **ids** of the waiting tickets and
nothing else.

An agent asking the status tool "what needs me?" got a list of handles
and no way to learn what any of them meant. That is the same failure
the project has been fixing surface by surface since v0.7.89: a
capability that reaches the command line but not the agent.

Now both surfaces read one function, ``mas.ticket_brief.ticket_brief``.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


from mas.ticket_brief import ticket_brief, ticket_briefs


def _ticket(ticket_id="T-20261121-104405-7a81", state="waiting_human"):
    return {
        "id": ticket_id,
        "kind": "loop_escalation",
        "state": state,
        "note": "",
        "decision": None,
        "payload": {
            "run_id": "R-285d764b0c",
            "goal": "写一份说明文档",
            "title": "写一份说明文档",
            "criteria": [{"kind": "contains", "value": "关键词"}],
            "issues": ["正文里没有出现要求的字符串：关键词"],
            "attempts": 5,
            "iterations_total": 7,
            "runs": ["R-285d764b0c", "R-2"],
            "reason": "stagnation: the deliverable did not change for 3 consecutive attempts",
            "status": "escalated",
            "budget": {"max_iterations": 8},
            "workspace": "/tmp/work",
        },
    }


def _state_dir(tickets):
    """A state directory the engine, the CLI and the MCP tool can read."""
    folder = tempfile.mkdtemp(prefix="mas-test-brief-")
    ledgers = os.path.join(folder, "ledgers")
    os.makedirs(ledgers, exist_ok=True)
    with open(os.path.join(ledgers, "runs.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "role": "closure",
                    "run_id": "R-285d764b0c",
                    "task_id": "T-1",
                    "ok": False,
                    "artifact_path": "",
                    "criteria": [],
                    "checks": [],
                    "ts": "2026-11-21T10:44:05Z",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    queue = os.path.join(folder, "tickets")
    os.makedirs(queue, exist_ok=True)
    for ticket in tickets:
        with open(
            os.path.join(queue, "%s.json" % ticket["id"]), "w", encoding="utf-8"
        ) as handle:
            json.dump(ticket, handle, ensure_ascii=False, indent=2)
    return folder


def _run_cli(state_dir, argv):
    from mas.cli import main

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            main(argv)
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved
    return buffer.getvalue()


def _mcp_status(state_dir):
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


class TicketBriefTest(unittest.TestCase):
    def setUp(self):
        self.ticket = _ticket()

    def test_it_names_the_ticket(self):
        brief = ticket_brief(self.ticket)
        self.assertEqual(brief["id"], "T-20261121-104405-7a81")
        self.assertEqual(brief["state"], "waiting_human")
        self.assertEqual(brief["kind"], "loop_escalation")
        self.assertEqual(brief["title"], "写一份说明文档")

    def test_it_says_what_the_ticket_is_stuck_on(self):
        brief = ticket_brief(self.ticket)
        self.assertTrue(brief["blocked_on"])
        self.assertIn("关键词", brief["blocked_on"])

    def test_it_says_why_the_loop_stopped_in_words(self):
        brief = ticket_brief(self.ticket)
        self.assertTrue(brief["reason_reading"])
        # The machine reason stays as data, the reading is for a human.
        self.assertEqual(
            brief["reason"],
            "stagnation: the deliverable did not change for 3 consecutive attempts",
        )

    def test_it_says_how_to_continue(self):
        brief = ticket_brief(self.ticket)
        self.assertTrue(brief["next_command"].startswith("mas resume "))
        self.assertIn("T-20261121-104405-7a81", brief["next_command"])
        self.assertIn("mas handback ", brief["handback_command"])

    def test_it_names_the_folder_to_edit(self):
        brief = ticket_brief(self.ticket)
        self.assertEqual(brief["workspace"], "/tmp/work")
        self.assertIn("--workspace", brief["handback_workspace_command"])

    def test_it_counts_the_cost_of_the_problem(self):
        brief = ticket_brief(self.ticket)
        self.assertEqual(brief["attempts"], 5)
        self.assertEqual(brief["rounds_total"], 7)
        self.assertEqual(brief["runs"], ["R-285d764b0c", "R-2"])

    def test_a_resolved_ticket_carries_no_continuation(self):
        brief = ticket_brief(_ticket(state="resolved"))
        self.assertNotIn("next_command", brief)
        self.assertNotIn("handback_command", brief)

    def test_a_ticket_with_nothing_stuck_still_says_something(self):
        ticket = _ticket()
        ticket["payload"]["issues"] = []
        brief = ticket_brief(ticket)
        self.assertEqual(brief["blocked_on"], "")
        self.assertEqual(brief["unmet_count"], 0)
        # The goal and the title are still there: a reader knows what
        # the ticket is about even when nothing is unmet.
        self.assertEqual(brief["title"], "写一份说明文档")

    def test_briefs_keep_the_order(self):
        one = _ticket("T-1")
        two = _ticket("T-2")
        briefs = ticket_briefs([one, two])
        self.assertEqual([b["id"] for b in briefs], ["T-1", "T-2"])

    def test_an_empty_queue_is_an_empty_list(self):
        self.assertEqual(ticket_briefs([]), [])
        self.assertEqual(ticket_briefs(None), [])


class McpStatusCarriesTheTicketsTest(unittest.TestCase):
    def setUp(self):
        self.state = _state_dir([_ticket()])
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def test_the_status_tool_carries_the_briefs(self):
        payload = _mcp_status(self.state)
        briefs = payload.get("waiting_tickets")
        self.assertTrue(briefs)
        self.assertEqual(briefs[0]["id"], "T-20261121-104405-7a81")
        self.assertTrue(briefs[0]["blocked_on"])
        self.assertTrue(briefs[0]["next_command"])

    def test_the_status_tool_still_carries_the_ids(self):
        payload = _mcp_status(self.state)
        self.assertEqual(
            payload.get("open_tickets"), ["T-20261121-104405-7a81"]
        )
        self.assertEqual(payload.get("waiting_tickets_count"), 1)

    def test_the_status_tool_gives_a_readable_line(self):
        payload = _mcp_status(self.state)
        text = payload.get("waiting_tickets_text") or ""
        self.assertIn("T-20261121-104405-7a81", text)

    def test_the_two_surfaces_say_the_same_words(self):
        payload = _mcp_status(self.state)
        brief = payload["waiting_tickets"][0]
        screen = _run_cli(self.state, ["tickets"])
        self.assertIn(brief["blocked_on"], screen)
        self.assertIn(brief["reason_reading"], screen)
        self.assertIn(brief["next_command"], screen)

    def test_the_status_page_carries_the_briefs_too(self):
        payload = json.loads(_run_cli(self.state, ["status", "--json"]))
        briefs = payload["tickets"]["waiting_briefs"]
        self.assertTrue(briefs)
        self.assertEqual(briefs[0]["id"], "T-20261121-104405-7a81")

    def test_an_empty_queue_says_so_rather_than_crashing(self):
        state = _state_dir([])
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        payload = _mcp_status(state)
        self.assertEqual(payload.get("waiting_tickets"), [])
        self.assertEqual(payload.get("waiting_tickets_count"), 0)


if __name__ == "__main__":
    unittest.main()

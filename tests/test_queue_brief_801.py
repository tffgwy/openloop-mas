# -*- coding: utf-8 -*-
"""v0.8.1: the ticket queue is a handover on every surface.

The new capability
------------------
v0.8.0 taught the **status** tool to carry ticket briefs. The queue had
a second home, and it was still raw:

    $ mas tickets --json
    {"tickets": [{"id": "T-...", "payload": {...}}]}

    $ mas_tickets (MCP)
    {"tickets": [{"id": "T-...", "payload": {...}}]}

Both handed back the engine's raw record. A reader could see the title
and the raw reason string, but *why* the loop stopped and *how to
continue* only existed in the command line's human text layout.

Now the queue reads the same brief function as the status tool and the
command line - one reading of a ticket, three surfaces - and the raw
tickets stay exactly where they were, under ``tickets``.
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
    """A state directory the CLI and the MCP tools can read."""
    folder = tempfile.mkdtemp(prefix="mas-test-queue-")
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
    tickets_dir = os.path.join(folder, "tickets")
    os.makedirs(tickets_dir, exist_ok=True)
    for ticket in tickets:
        path = os.path.join(tickets_dir, "%s.json" % ticket["id"])
        with open(path, "w", encoding="utf-8") as handle:
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


def _mcp(state_dir, tool, arguments=None):
    from mas.mcp_server import handle_tool

    saved = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = state_dir
    try:
        return handle_tool(tool, arguments or {})
    finally:
        if saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = saved


def _is_brief(item):
    return (
        isinstance(item, dict)
        and not isinstance(item.get("payload"), dict)
        and bool(item.get("id"))
        and bool(item.get("title"))
    )


class CliQueueCarriesBriefsTest(unittest.TestCase):
    def setUp(self):
        self.state = _state_dir([_ticket()])
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def test_the_json_queue_carries_briefs(self):
        payload = json.loads(_run_cli(self.state, ["tickets", "--json"]))
        briefs = payload.get("briefs")
        self.assertTrue(briefs)
        self.assertTrue(_is_brief(briefs[0]))
        self.assertEqual(briefs[0]["id"], "T-20261121-104405-7a81")

    def test_a_queued_brief_says_what_to_do_next(self):
        payload = json.loads(_run_cli(self.state, ["tickets", "--json"]))
        brief = payload["briefs"][0]
        self.assertTrue(brief["blocked_on"])
        self.assertTrue(brief["reason_reading"])
        self.assertTrue(brief["next_command"].startswith("mas resume "))

    def test_the_raw_tickets_stay_beside_the_briefs(self):
        payload = json.loads(_run_cli(self.state, ["tickets", "--json"]))
        # The raw record stays exactly where it always was.
        raw = payload.get("tickets")
        self.assertTrue(raw)
        self.assertEqual(raw[0]["id"], "T-20261121-104405-7a81")
        self.assertTrue(raw[0]["payload"]["goal"])

    def test_the_human_screen_and_the_json_say_the_same_words(self):
        payload = json.loads(_run_cli(self.state, ["tickets", "--json"]))
        brief = payload["briefs"][0]
        screen = _run_cli(self.state, ["tickets"])
        self.assertIn(brief["blocked_on"], screen)
        self.assertIn(brief["reason_reading"], screen)
        self.assertIn(brief["next_command"], screen)

    def test_an_empty_queue_is_an_empty_list(self):
        state = _state_dir([])
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        payload = json.loads(_run_cli(state, ["tickets", "--json"]))
        self.assertEqual(payload.get("tickets"), [])
        self.assertEqual(payload.get("briefs"), [])


class McpQueueCarriesBriefsTest(unittest.TestCase):
    def setUp(self):
        self.state = _state_dir([_ticket()])
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def test_the_queue_tool_carries_briefs(self):
        payload = _mcp(self.state, "mas_tickets")
        briefs = payload.get("briefs")
        self.assertTrue(briefs)
        self.assertTrue(_is_brief(briefs[0]))

    def test_a_queued_brief_says_what_to_do_next(self):
        payload = _mcp(self.state, "mas_tickets")
        brief = payload["briefs"][0]
        self.assertTrue(brief["blocked_on"])
        self.assertTrue(brief["reason_reading"])
        self.assertTrue(brief["next_command"].startswith("mas resume "))

    def test_the_queue_tool_still_carries_the_raw_tickets(self):
        payload = _mcp(self.state, "mas_tickets")
        # The raw record stays exactly where it always was.
        raw = payload.get("tickets")
        self.assertTrue(raw)
        self.assertTrue(raw[0]["payload"]["goal"])
        self.assertEqual(payload.get("briefs_count"), 1)

    def test_the_queue_tool_gives_a_readable_line(self):
        payload = _mcp(self.state, "mas_tickets")
        text = payload.get("briefs_text") or ""
        self.assertIn("T-20261121-104405-7a81", text)

    def test_the_queue_and_the_status_tool_say_the_same_words(self):
        queue = _mcp(self.state, "mas_tickets")["briefs"][0]
        status = _mcp(self.state, "mas_status")["waiting_tickets"][0]
        for key in ("id", "title", "blocked_on", "reason_reading", "next_command"):
            self.assertEqual(queue.get(key), status.get(key))

    def test_the_queue_can_still_be_filtered_by_state(self):
        payload = _mcp(self.state, "mas_tickets", {"state": "resolved"})
        self.assertEqual(payload.get("tickets"), [])
        self.assertEqual(payload.get("briefs"), [])

    def test_the_tool_description_advertises_the_brief(self):
        from mas.mcp_server import TOOLS

        entry = [t for t in TOOLS if t.get("name") == "mas_tickets"]
        self.assertTrue(entry)
        self.assertIn("brief", (entry[0].get("description") or "").lower())


if __name__ == "__main__":
    unittest.main()

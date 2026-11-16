# -*- coding: utf-8 -*-
"""v0.7.74 tests for the one-shot approval ticket.

v0.7.73 gave the human a standing token: paste it to an agent and the
agent can approve **every** draft, forever. That is the wrong shape of
trust for a team of agents.

v0.7.74 replaces it with a ticket: a human signs one ticket for one
draft, it works once, it expires, and only its hash is kept on disk.
These tests pin that shape down - from the library, from the command
line, and through MCP.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import os
import pathlib
import tempfile
import unittest

from mas.drafts import (
    TICKET_ACTION_APPROVE,
    TICKET_ACTION_REJECT,
    TICKET_TTL_SECONDS,
    ensure_human_token,
    issue_ticket,
    redeem_ticket,
    ticket_store_path,
)


class TicketSigningTest(unittest.TestCase):
    """Signing a ticket needs the human, and needs a draft."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self._tmp.name) / "state"
        self.token = ensure_human_token(self.state)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_ticket_is_signed_for_the_draft_it_names(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        self.assertTrue(ticket["id"].startswith("T-"))
        self.assertEqual(ticket["draft_id"], "D-abc")
        self.assertEqual(ticket["action"], TICKET_ACTION_APPROVE)
        self.assertTrue(ticket["secret"])

    def test_a_wrong_token_signs_nothing(self) -> None:
        with self.assertRaises(ValueError):
            issue_ticket(self.state, "D-abc", human_token="not-the-token")
        self.assertFalse(ticket_store_path(self.state).exists())

    def test_no_token_signs_nothing(self) -> None:
        with self.assertRaises(ValueError):
            issue_ticket(self.state, "D-abc", human_token="")
        self.assertFalse(ticket_store_path(self.state).exists())

    def test_a_ticket_needs_a_draft(self) -> None:
        with self.assertRaises(ValueError):
            issue_ticket(self.state, "", human_token=self.token)

    def test_a_ticket_cannot_authorize_an_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            issue_ticket(self.state, "D-abc", action="delete", human_token=self.token)

    def test_the_store_keeps_the_hash_and_not_the_ticket(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        raw = ticket_store_path(self.state).read_text(encoding="utf-8")
        self.assertNotIn(ticket["secret"], raw)
        data = json.loads(raw)
        record = data[ticket["id"]]
        self.assertNotEqual(record.get("digest"), ticket["secret"])
        self.assertEqual(len(str(record.get("digest") or "")), 64)
        self.assertFalse(record.get("used"))

    def test_two_tickets_are_two_secrets(self) -> None:
        one = issue_ticket(self.state, "D-abc", human_token=self.token)
        two = issue_ticket(self.state, "D-abc", human_token=self.token)
        self.assertNotEqual(one["secret"], two["secret"])
        self.assertNotEqual(one["id"], two["id"])


class TicketRedeemingTest(unittest.TestCase):
    """A ticket opens one door, once."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self._tmp.name) / "state"
        self.token = ensure_human_token(self.state)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_right_draft_opens_the_door(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        result = redeem_ticket(self.state, "D-abc", ticket["secret"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["draft_id"], "D-abc")

    def test_another_draft_is_refused(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        result = redeem_ticket(self.state, "D-other", ticket["secret"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ticket_bound_to_another_draft")

    def test_a_ticket_works_only_once(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        self.assertTrue(redeem_ticket(self.state, "D-abc", ticket["secret"])["ok"])
        again = redeem_ticket(self.state, "D-abc", ticket["secret"])
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason"], "ticket_already_used")

    def test_a_used_ticket_is_marked_as_spent_on_disk(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        redeem_ticket(self.state, "D-abc", ticket["secret"])
        data = json.loads(ticket_store_path(self.state).read_text(encoding="utf-8"))
        self.assertTrue(data[ticket["id"]]["used"])
        self.assertTrue(data[ticket["id"]]["used_at"])

    def test_an_unknown_ticket_is_refused(self) -> None:
        result = redeem_ticket(self.state, "D-abc", "T-nope")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown_ticket")

    def test_an_empty_ticket_is_refused(self) -> None:
        result = redeem_ticket(self.state, "D-abc", "")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_ticket")

    def test_an_empty_draft_is_refused(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        result = redeem_ticket(self.state, "", ticket["secret"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_draft")

    def test_a_ticket_for_a_rejection_cannot_approve(self) -> None:
        ticket = issue_ticket(
            self.state, "D-abc", action=TICKET_ACTION_REJECT, human_token=self.token
        )
        result = redeem_ticket(self.state, "D-abc", ticket["secret"], action="approve")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ticket_action_mismatch")

    def test_a_ticket_for_a_rejection_can_reject(self) -> None:
        ticket = issue_ticket(
            self.state, "D-abc", action=TICKET_ACTION_REJECT, human_token=self.token
        )
        result = redeem_ticket(self.state, "D-abc", ticket["secret"], action="reject")
        self.assertTrue(result["ok"])

    def test_an_old_ticket_stops_working(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        path = ticket_store_path(self.state)
        data = json.loads(path.read_text(encoding="utf-8"))
        import datetime

        stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=TICKET_TTL_SECONDS + 60
        )
        data[ticket["id"]]["created_at"] = stamp.isoformat(timespec="seconds")
        path.write_text(json.dumps(data), encoding="utf-8")
        result = redeem_ticket(self.state, "D-abc", ticket["secret"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ticket_expired")

    def test_a_broken_timestamp_is_treated_as_expired(self) -> None:
        ticket = issue_ticket(self.state, "D-abc", human_token=self.token)
        path = ticket_store_path(self.state)
        data = json.loads(path.read_text(encoding="utf-8"))
        data[ticket["id"]]["created_at"] = "not-a-timestamp"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = redeem_ticket(self.state, "D-abc", ticket["secret"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ticket_expired")


class TicketThroughMcpTest(unittest.TestCase):
    """The ticket works through the door an agent actually uses."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.state = self.root / "state"
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.target = self.workspace / "notes.txt"
        self.target.write_text("the human's own words", encoding="utf-8")
        self._saved = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(self.state)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved
        self._tmp.cleanup()

    def _stage(self, content: str) -> str:
        from mas.mcp_server import handle_tool

        staged = handle_tool(
            "mas_draft_stage",
            {
                "path": "notes.txt",
                "content": content,
                "workspace": str(self.workspace),
            },
        )
        return str((staged.get("draft") or {}).get("id") or "")

    def test_a_ticket_approves_through_mcp_and_writes_the_file(self) -> None:
        from mas.mcp_server import handle_tool

        draft_id = self._stage("the agent proposes this")
        ticket = issue_ticket(self.state, draft_id, human_token=ensure_human_token(self.state))
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "ticket": ticket["secret"],
                "workspace": str(self.workspace),
            },
        )
        self.assertNotIn("error", result)
        self.assertTrue(result.get("message"))
        self.assertEqual(
            self.target.read_text(encoding="utf-8"), "the agent proposes this"
        )

    def test_the_same_ticket_does_not_work_twice_through_mcp(self) -> None:
        from mas.mcp_server import handle_tool

        draft_id = self._stage("the agent proposes this")
        ticket = issue_ticket(self.state, draft_id, human_token=ensure_human_token(self.state))
        handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "ticket": ticket["secret"],
                "workspace": str(self.workspace),
            },
        )
        second = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "ticket": ticket["secret"],
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(second.get("error"), "ticket_refused")
        self.assertEqual(second.get("reason"), "ticket_already_used")

    def test_a_ticket_for_another_draft_does_not_move_the_file(self) -> None:
        from mas.mcp_server import handle_tool

        first = self._stage("one change")
        second = self._stage("another change")
        ticket = issue_ticket(self.state, first, human_token=ensure_human_token(self.state))
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": second,
                "ticket": ticket["secret"],
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(result.get("error"), "ticket_refused")
        self.assertEqual(result.get("reason"), "ticket_bound_to_another_draft")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "the human's own words")

    def test_no_token_and_no_ticket_still_means_no(self) -> None:
        from mas.mcp_server import handle_tool

        draft_id = self._stage("the agent proposes this")
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(result.get("error"), "human_token_required")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "the human's own words")

    def test_a_wrong_token_still_means_no(self) -> None:
        from mas.mcp_server import handle_tool

        draft_id = self._stage("the agent proposes this")
        result = handle_tool(
            "mas_draft_resolve",
            {
                "action": "approve",
                "draft_id": draft_id,
                "token": "not-the-token",
                "workspace": str(self.workspace),
            },
        )
        self.assertEqual(result.get("error"), "human_token_required")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "the human's own words")


class TicketCommandLineTest(unittest.TestCase):
    """``mas drafts ticket`` is the door a human walks through."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.state = self.root / "state"
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        self._saved_config = os.environ.get("MAS_CONFIG_PATH")
        os.environ["MAS_STATE_DIR"] = str(self.state)
        os.environ["MAS_CONFIG_PATH"] = str(self.root / "config.json")

    def tearDown(self) -> None:
        for name, value in (
            ("MAS_STATE_DIR", self._saved_state),
            ("MAS_CONFIG_PATH", self._saved_config),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()

    def _run(self, argv):
        from mas.cli import main

        return main(argv)

    def test_the_command_signs_a_ticket(self) -> None:
        token = ensure_human_token(self.state)
        code = self._run(
            ["drafts", "ticket", "D-abc", "--token", token, "--json"]
        )
        self.assertEqual(code, 0)

    def test_the_command_refuses_without_the_token(self) -> None:
        code = self._run(["drafts", "ticket", "D-abc", "--token", "nope", "--json"])
        self.assertEqual(code, 2)

    def test_the_command_refuses_without_a_draft_id(self) -> None:
        token = ensure_human_token(self.state)
        code = self._run(["drafts", "ticket", "--token", token, "--json"])
        self.assertEqual(code, 2)

    def test_the_command_refuses_an_unknown_action(self) -> None:
        token = ensure_human_token(self.state)
        with self.assertRaises(SystemExit):
            self._run(
                ["drafts", "ticket", "D-abc", "--token", token, "--ticket-action", "delete"]
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

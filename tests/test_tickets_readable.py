# -*- coding: utf-8 -*-
"""v0.7.48 regression tests: the ticket queue must answer "what do I do?".

`mas tickets` printed one line per ticket - an id, a state, a kind
and a title. A human who had just watched a loop escalate could not
learn which check failed, why the loop gave up, how many rounds it
spent, or how to continue. `mas ledger` and `mas tickets` printed
*nothing at all* on an empty state, which reads as a broken command
rather than an empty one.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import tempfile
import unittest

from mas.cli import main


def _invoke(argv, state_dir):
    """Run the CLI and capture the exit code and the output.

    A run that cannot meet its criteria exits non-zero on purpose;
    that is one of the situations this file has to observe.
    """
    os.environ["MAS_STATE_DIR"] = state_dir
    os.environ["MAS_PROVIDER"] = "mock"
    os.environ.pop("MAS_API_KEY", None)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class EmptyQueueIsReadableTest(unittest.TestCase):
    """An empty command must say it is empty, not print nothing."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-tickets-")

    def test_the_empty_ticket_queue_says_so(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("工单队列是空的", out)
        self.assertIn("mas demo", out)

    def test_the_empty_ledger_says_so(self):
        _code, out = _invoke(["ledger"], self.state_dir)
        self.assertIn("账本还是空的", out)
        self.assertIn("mas run", out)

    def test_the_empty_ledger_names_the_ledger_file(self):
        _code, out = _invoke(["ledger"], self.state_dir)
        self.assertIn("runs.jsonl", out)

    def test_the_empty_ledger_still_prints_nothing_on_stdout_json(self):
        code, out = _invoke(["ledger", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["entries"], [])


class TicketExplainsItselfTest(unittest.TestCase):
    """A waiting ticket must say what is blocked and how to continue."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-tickets-")
        _invoke(
            [
                "run",
                "--goal",
                "不可能达成的目标",
                "--criteria",
                "regex:^NEVER_MATCHES$",
            ],
            self.state_dir,
        )

    def test_the_ticket_is_listed(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("待处理工单：1 个", out)

    def test_the_ticket_names_the_task(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("不可能达成的目标", out)

    def test_the_ticket_names_the_run(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("运行：R-", out)

    def test_the_ticket_names_the_failing_check(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("卡在：", out)
        self.assertIn("NEVER_MATCHES", out)

    def test_the_ticket_names_the_reason(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("原因：", out)

    def test_the_ticket_names_the_attempt_count(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("已尝试：", out)

    def test_the_ticket_names_the_last_deliverable_size(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("最后产物：", out)

    def test_the_ticket_gives_the_resume_command(self):
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("接着干：mas resume T-", out)
        self.assertIn('--note "你的意见"', out)

    def test_the_json_output_is_unchanged(self):
        _code, out = _invoke(["tickets", "--json"], self.state_dir)
        tickets = json.loads(out)["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["state"], "waiting_human")
        self.assertIn("payload", tickets[0])


class StatusNamesTheTicketTest(unittest.TestCase):
    """The status page must name the problem, not just its serial number."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-tickets-")
        _invoke(
            [
                "run",
                "--goal",
                "不可能达成的目标",
                "--criteria",
                "regex:^NEVER_MATCHES$",
            ],
            self.state_dir,
        )

    def test_the_status_names_the_waiting_problem(self):
        _code, out = _invoke(["status"], self.state_dir)
        self.assertIn("待处理工单：1 个", out)
        # The goal must sit on the *ticket line*, next to the id. It
        # also appears in the deliverable listing, so a plain
        # substring check would pass even if the ticket line went
        # back to showing nothing but a serial number.
        ticket_lines = [
            line for line in out.splitlines()
            if "T-" in line and "mas resume" not in line
        ]
        self.assertTrue(ticket_lines, out)
        self.assertTrue(
            any("不可能达成的目标" in line for line in ticket_lines),
            out,
        )

    def test_the_status_gives_the_resume_command(self):
        _code, out = _invoke(["status"], self.state_dir)
        self.assertIn("mas resume T-", out)


class ResolvedTicketIsReadableTest(unittest.TestCase):
    """A resolved ticket must show what the human decided."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-tickets-")
        _invoke(
            [
                "run",
                "--goal",
                "不可能达成的目标",
                "--criteria",
                "regex:^NEVER_MATCHES$",
            ],
            self.state_dir,
        )
        _code, out = _invoke(["tickets", "--json"], self.state_dir)
        self.ticket_id = json.loads(out)["tickets"][0]["id"]

    def _resolve(self):
        from mas.config import load_config
        from mas.tickets import TicketStore

        config = load_config()
        store = TicketStore(os.path.join(config["state_dir"], "tickets"))
        store.resolve(self.ticket_id, "resumed", "就这样吧")

    def test_the_resolved_ticket_is_shown_separately(self):
        self._resolve()
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("已解决工单：1 个", out)

    def test_the_resolved_ticket_shows_the_note(self):
        self._resolve()
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("人工意见：就这样吧", out)

    def test_the_resolved_ticket_has_no_resume_hint(self):
        self._resolve()
        _code, out = _invoke(["tickets"], self.state_dir)
        # A resolved ticket must not tell the user to resume it.
        self.assertNotIn("接着干：mas resume", out)

    def test_the_resolved_ticket_is_not_in_the_waiting_queue(self):
        self._resolve()
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertNotIn("待处理工单：", out)

    def test_the_state_filter_can_pick_one_queue(self):
        self._resolve()
        _code, out = _invoke(["tickets", "--state", "resolved"], self.state_dir)
        self.assertIn("已解决工单：1 个", out)
        self.assertNotIn("待处理工单：", out)


class ResumeThatDoesNotCloseTest(unittest.TestCase):
    """A resume that fails to close must keep the ticket in the queue.

    Marking the ticket resolved on a resume that did not close would
    empty the human queue while the problem it was opened for is still
    open. The escalation disappearing is worse than the loop failing.
    """

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-tickets-")
        _invoke(
            [
                "run",
                "--goal",
                "不可能达成的目标",
                "--criteria",
                "regex:^NEVER_MATCHES$",
            ],
            self.state_dir,
        )
        _code, out = _invoke(["tickets", "--json"], self.state_dir)
        self.ticket_id = json.loads(out)["tickets"][0]["id"]

    def test_the_ticket_stays_waiting_after_a_failed_resume(self):
        _invoke(["resume", self.ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("待处理工单：1 个", out)

    def test_the_new_reason_is_recorded(self):
        _invoke(["resume", self.ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("resume did not close the loop", out)

    def test_the_human_note_is_kept(self):
        _invoke(["resume", self.ticket_id, "--note", "再试一次"], self.state_dir)
        _code, out = _invoke(["tickets"], self.state_dir)
        self.assertIn("人工意见：再试一次", out)


if __name__ == "__main__":
    unittest.main()

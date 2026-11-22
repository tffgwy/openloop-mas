# -*- coding: utf-8 -*-
"""v0.8.5: the replay listens to the queue too.

The hole
--------
v0.8.4 made the **report** read the human ticket queue. Two surfaces
were left behind.

The **replay** (``mas replay``) is the screen that shows what the loop
did, step by step - and it built its threads from the ledger alone.
The command line had been handing it the queue since v0.8.4 and the
replay threw it away. So the replay said "待处理" for a problem the
report called "已解决". Two answers from one machine about one
problem, and the replay is where a reader goes to see what happened.

The **MCP** replay was worse: it never opened the queue at all. An
agent driving Claude, GPT or Kimi through MCP got a replay that could
not tell a finished problem from an open one, while the human at the
same terminal saw the right answer.

And a third answer hid in the same place. The report kept a failed
run's ``mas resume`` pointer when the queue had **not been read** -
because "nobody looked" is not "there is nothing to do". The replay
dropped it, so the replay told a reader to walk away from an open
problem whose state was merely unknown.

The fix
-------
The reading of the queue lives in one place -
``mas.report.queue_state()`` - and the four states of a ticket live in
``mas.report.ticket_state_of()``: ``waiting``, ``resolved``, ``lost``
and ``unknown``. The report, the replay and the MCP server all read
them from there.
"""

import os
import sys
import unittest

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

from mas.report import (
    build_report,
    queue_state,
    replay_trace,
    ticket_resumable,
    ticket_state_of,
)
from mas.selftest_replay_queue import (
    _rows,
    _surfaces,
    _ticket,
)


class TheReplayListensToTheQueueTest(unittest.TestCase):
    """The replay and the report must not argue about one problem."""

    def setUp(self):
        self.rows = _rows([("R-fail", False, "T-one")])
        self.closed = _ticket(
            "T-one", "resolved", closed_run_id="R-later", runs=["R-later"]
        )
        self.waiting = _ticket("T-one", "waiting_human", runs=["R-fail"])

    # -- the replay counts the same problems as the report ----------------

    def test_the_replay_and_the_report_agree_about_open_problems(self):
        report = build_report(self.rows, [self.closed])
        trace = replay_trace(self.rows, [self.closed])
        self.assertEqual(
            trace["problems_open"], report["totals"]["problems_open"]
        )
        self.assertEqual(trace["problems_open"], 0)

    def test_the_replay_calls_a_queue_closed_problem_closed(self):
        trace = replay_trace(self.rows, [self.closed])
        self.assertEqual(len(trace["threads"]), 1)
        self.assertTrue(trace["threads"][0]["closed"])
        self.assertEqual(trace["threads"][0]["closed_by"], "R-later")

    def test_the_replay_still_calls_a_waiting_problem_open(self):
        trace = replay_trace(self.rows, [self.waiting])
        self.assertEqual(trace["problems_open"], 1)
        self.assertFalse(trace["threads"][0]["closed"])

    def test_a_hand_closed_ticket_keeps_the_problem_open(self):
        # A person closed the ticket by hand: no successful attempt,
        # so the machine still has nothing to show for it.
        hand = _ticket("T-one", "resolved", runs=["R-later"])
        trace = replay_trace(self.rows, [hand])
        self.assertEqual(trace["problems_open"], 1)
        self.assertFalse(trace["threads"][0]["closed"])

    # -- the MCP replay reads the queue -----------------------------------

    def test_the_mcp_replay_reads_the_queue(self):
        from mas.selftest_replay_queue import _call_mcp

        payload = _call_mcp(self.rows, [self.closed])
        self.assertEqual(payload["problems_open"], 0)
        self.assertTrue(payload["threads"][0]["closed"])

        unread = _call_mcp(self.rows, [])
        self.assertEqual(unread["problems_open"], 1)

    # -- the handover pointer ---------------------------------------------

    def test_a_replay_whose_queue_was_never_read_keeps_its_pointer(self):
        trace = replay_trace(self.rows)
        self.assertEqual(trace["problems_open"], 1)
        run = trace["runs"][0]
        self.assertEqual(run["ticket_state"], "unknown")
        self.assertTrue(run["ticket_resumable"])

    def test_a_lost_ticket_gets_no_pointer(self):
        # The queue was read and does not hold the ticket: `mas resume`
        # would raise on it, so pointing there sends the reader into an
        # error.
        state = queue_state([])
        self.assertEqual(ticket_state_of("T-one", state), "lost")
        self.assertFalse(ticket_resumable("T-one", state))

    def test_a_waiting_ticket_gets_the_pointer(self):
        state = queue_state([self.waiting])
        self.assertEqual(ticket_state_of("T-one", state), "waiting")
        self.assertTrue(ticket_resumable("T-one", state))

    def test_a_resolved_ticket_gets_no_pointer(self):
        state = queue_state([self.closed])
        self.assertEqual(ticket_state_of("T-one", state), "resolved")
        self.assertFalse(ticket_resumable("T-one", state))

    def test_a_closed_run_never_gets_a_pointer(self):
        state = queue_state([self.waiting])
        self.assertFalse(ticket_resumable("T-one", state, closed=True))

    # -- the helper itself ------------------------------------------------

    def test_an_unread_queue_is_not_an_empty_queue(self):
        state = queue_state(None)
        self.assertFalse(state["read"])
        self.assertEqual(state["waiting"], set())
        self.assertEqual(state["known"], set())

    def test_an_empty_queue_was_read(self):
        state = queue_state([])
        self.assertTrue(state["read"])

    def test_the_queue_state_separates_waiting_from_known(self):
        state = queue_state([self.waiting, self.closed])
        self.assertEqual(state["waiting"], {"T-one"})
        self.assertEqual(state["known"], {"T-one"})


class TheGateItselfTest(unittest.TestCase):
    """The gate has to be able to fail."""

    def test_the_gate_catches_the_old_bug(self):
        import mas.cli as cli_module
        import mas.mcp_server as mcp_module
        import mas.report as report_module
        from mas.selftest_replay_queue import _replay_verdicts

        real = report_module.replay_trace

        def _drop(rows, tickets=None, _real=real):
            # The old bug: the queue is read and thrown away.
            return _real(rows)

        report_module.replay_trace = _drop
        mcp_module.replay_trace = _drop
        cli_module.replay_trace = _drop
        try:
            verdicts = _replay_verdicts(_surfaces())
        finally:
            report_module.replay_trace = real
            mcp_module.replay_trace = real
            cli_module.replay_trace = real

        self.assertFalse(
            verdicts["the replay and the report agree about how many problems are open"]
        )
        self.assertFalse(
            verdicts["the replay says the same thing about a problem the queue closed"]
        )
        self.assertFalse(verdicts["the MCP replay reads the queue"])

    def test_the_gate_passes_on_the_fixed_code(self):
        from mas.selftest_replay_queue import _replay_verdicts

        verdicts = _replay_verdicts(_surfaces())
        for label, ok in verdicts.items():
            self.assertTrue(ok, label)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

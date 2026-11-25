# -*- coding: utf-8 -*-
"""The gate suite must not lose a gate silently.

``EXPECTED_CHECKS`` reads the count from the suite, so it cannot catch a
gate that disappears - the count shrinks with it. Something has to hold
the number from the outside, or the whole idea of "the gate suite" is
unfalsifiable.

This file is that something: it pins the count, and it pins the *names*
of the gates, so a gate that is renamed or dropped is noticed.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import unittest

from mas.selftest import CHECKS, EXPECTED_CHECKS

#: The gates the suite is expected to hold, in order.
#:
#: This list is deliberately written out by hand. A count derived from
#: the suite cannot catch a gate that vanishes; a hand-written list can.
EXPECTED_GATE_NAMES = (
    "relay_refusal",
    "provider_usage",
    "workspace_keeps_files",
    "mcp_handshake",
    "stream_folds",
    "parallel_pick",
    "handover_ticket",
    "takeover_handover",
    "handback_closes",
    "handback_report",
    "folder_handoff",
    "content_check",
    "binary_write",
    "resource_index",
    "draft_approval",
    "mcp_draft_approval",
    "ticket_approval",
    "conflict_guard",
    "force_cost",
    "undo_guard",
    "audit_chain",
    "shadow_guard",
    "onboarding_path",
    "packaged_docs",
    "undo_visibility",
    "bootstrap_path",
    "bootstrap_execution",
    "closure_seal",
    "report_seal",
    "report_undo",
    "report_audit",
    "report_tickets",
    "report_drafts",
    "report_trace_roles",
    "mcp_artifacts",
    "closure_verdict",
    "closure_supersede",
    "status_grain",
    "ticket_brief",
    "queue_brief",
    "closure_grain_text",
    "run_detail_grain",
    "single_run_supersede",
    "replay_listens_to_the_queue",
    "a_taken_back_approval_is_visible_everywhere",
    "an_undone_draft_is_still_waiting_everywhere",
    "a_decision_hangs_on_its_run",
    "a_run_knows_its_approval",
    "a_run_knows_its_backend",
    "a_run_knows_who_paid",
    "a_run_knows_its_ceiling",
    "doc_text_integrity",
    "selftest_isolation",
    "seal_takeover",
    "seal_takeover_everywhere",
)


class GateCountIsPinnedTest(unittest.TestCase):
    def test_the_count_is_the_one_written_down_here(self):
        self.assertEqual(len(CHECKS), len(EXPECTED_GATE_NAMES))

    def test_the_exported_count_agrees_with_the_suite(self):
        self.assertEqual(EXPECTED_CHECKS, len(CHECKS))

    def test_every_gate_is_present_and_named(self):
        names = [getattr(c, "CHECK_NAME", "") for c in CHECKS]
        for expected in EXPECTED_GATE_NAMES:
            self.assertIn(expected, names)

    def test_the_gates_are_in_the_written_order(self):
        names = [getattr(c, "CHECK_NAME", "") for c in CHECKS]
        self.assertEqual(names, list(EXPECTED_GATE_NAMES))

    def test_no_gate_is_nameless(self):
        for check in CHECKS:
            self.assertTrue(
                getattr(check, "CHECK_NAME", ""),
                "%s has no name" % getattr(check, "__name__", check),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Tests for role prompt construction (v0.7.11).

The prompt is what a real model reads.  A machine check that could not
decide anything (no rule, a broken rule) must not be rendered as FAIL -
that made a reviewer read "the machine check failed" for a criterion that
was never checked, which is exactly the fake-closure pattern this
project exists to kill.
"""
try:  # pragma: no cover
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import unittest

from mas.roles import Roles


def _state(checks):
    return {
        "goal": "ship the thing",
        "criteria": [
            {"id": "c1", "desc": "must mention alpha"},
            {"id": "c2", "desc": "must mention beta"},
        ],
        "candidate_artifact": "alpha beta",
        "checks": checks,
        "attempt": 1,
        "max_iterations": 3,
    }


class RolesMachineCheckStates(unittest.TestCase):
    def setUp(self):
        self.roles = Roles({"language": "en"})

    def _render(self, checks):
        return self.roles.prompt("verifier", _state(checks))

    def test_a_passing_check_is_shown_as_pass(self):
        text = self._render([
            {"id": "c1", "pass": True, "note": "all required strings present"},
        ])
        self.assertIn("[c1] PASS", text)

    def test_a_failing_check_is_shown_as_fail(self):
        text = self._render([
            {"id": "c1", "pass": False, "note": "missing: ['alpha']"},
        ])
        self.assertIn("[c1] FAIL", text)

    def test_an_undecided_check_is_shown_as_undecided_not_fail(self):
        text = self._render([
            {"id": "c1", "pass": None, "note": "no machine check"},
        ])
        self.assertIn("[c1] UNDECIDED", text)
        self.assertNotIn("[c1] FAIL", text)

    def test_an_invalid_rule_is_shown_as_undecided(self):
        text = self._render([
            {"id": "c2", "pass": None, "note": "unknown check type: bogus"},
        ])
        self.assertIn("[c2] UNDECIDED", text)
        self.assertNotIn("[c2] FAIL", text)

    def test_the_three_states_coexist_in_one_prompt(self):
        text = self._render([
            {"id": "c1", "pass": True, "note": "ok"},
            {"id": "c2", "pass": False, "note": "bad"},
            {"id": "c3", "pass": None, "note": "no machine check"},
        ])
        self.assertIn("[c1] PASS", text)
        self.assertIn("[c2] FAIL", text)
        self.assertIn("[c3] UNDECIDED", text)

    def test_a_missing_pass_key_is_treated_as_undecided(self):
        text = self._render([{"id": "c1", "note": "no machine check"}])
        self.assertIn("[c1] UNDECIDED", text)
        self.assertNotIn("[c1] FAIL", text)


if __name__ == "__main__":
    unittest.main()

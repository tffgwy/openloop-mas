# -*- coding: utf-8 -*-
"""The onboarding gate must stay honest on its own terms.

The gate in ``mas/selftest_onboarding.py`` is what watches the front
door. This file makes sure the *gate itself* is well formed: the
observations it claims to make are the ones it actually makes, the two
readings agree, and the invariant it reports is the one it holds.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import unittest

from mas.selftest_onboarding import (
    ONBOARDING_OBSERVATIONS,
    _onboarding_observations,
    _onboarding_verdicts,
    _onboarding_verdicts_independent,
    check_a_beginner_can_walk_the_docs_and_close_a_loop,
)


class OnboardingGateTest(unittest.TestCase):
    def setUp(self):
        self.facts = _onboarding_observations(".")

    def test_the_gate_passes_on_the_real_docs(self):
        verdicts = _onboarding_verdicts(self.facts)
        for label in ONBOARDING_OBSERVATIONS:
            self.assertTrue(verdicts.get(label), "%s does not hold" % label)

    def test_every_observation_is_actually_observed(self):
        verdicts = _onboarding_verdicts(self.facts)
        for label in ONBOARDING_OBSERVATIONS:
            self.assertIn(label, verdicts)

    def test_the_two_readings_agree(self):
        verdicts = _onboarding_verdicts(self.facts)
        independent = _onboarding_verdicts_independent(self.facts)
        for label in ONBOARDING_OBSERVATIONS:
            self.assertEqual(
                verdicts.get(label),
                independent.get(label),
                "%s: the two readings disagree" % label,
            )

    def test_the_docs_pin_the_running_version(self):
        from mas import __version__ as running

        pinned = self.facts.get("pinned_versions") or {}
        flat = set()
        for values in pinned.values():
            flat.update(str(v) for v in values)
        self.assertEqual(flat, {running})

    def test_no_documented_command_is_a_ghost(self):
        self.assertEqual(self.facts.get("unknown_commands") or {}, {})

    def test_the_gate_is_named(self):
        self.assertEqual(
            getattr(
                check_a_beginner_can_walk_the_docs_and_close_a_loop,
                "CHECK_NAME",
                "",
            ),
            "onboarding_path",
        )

    def test_the_gate_reports_its_own_invariant(self):
        result = check_a_beginner_can_walk_the_docs_and_close_a_loop(iterations=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "onboarding_path")
        self.assertTrue(result["invariant"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# -*- coding: utf-8 -*-
"""The twenty-second gate: the program the user types is the program
that was checked.

The gate has to be able to fail. These tests prove it can: blind the
shadow detection and the gate has to go red.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import unittest

from mas.selftest_shadow import (
    SHADOW_OBSERVATIONS,
    _shadow_observations,
    _shadow_verdicts,
    _shadow_verdicts_independent,
    check_the_program_you_type_is_the_program_that_was_checked,
)


class ShadowGateObservationTest(unittest.TestCase):
    def test_the_gate_sees_a_real_shadow(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            facts = _shadow_observations(pathlib.Path(tmp))
        verdicts = _shadow_verdicts(facts)
        for label in SHADOW_OBSERVATIONS:
            self.assertIn(label, verdicts)
            self.assertTrue(verdicts[label], "%s should hold" % label)

    def test_the_two_readings_agree(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            facts = _shadow_observations(pathlib.Path(tmp))
        first = _shadow_verdicts(facts)
        second = _shadow_verdicts_independent(facts)
        self.assertEqual(first, second)


class ShadowGateCanFailTest(unittest.TestCase):
    """A gate that cannot fail does not watch anything."""

    def test_blinding_the_detection_makes_the_gate_red(self):
        import mas.shadow as shadow_module

        real_status = shadow_module.shadow_status

        def _blind(env=None, **kwargs):
            return {
                "running_version": "0.0.0",
                "launchers": [],
                "winner": None,
                "shadowed": False,
                "shadow_version": "",
                "distinct_versions": [],
                "count": 0,
            }

        shadow_module.shadow_status = _blind
        try:
            result = check_the_program_you_type_is_the_program_that_was_checked(
                iterations=1
            )
        finally:
            shadow_module.shadow_status = real_status
        self.assertFalse(result["ok"])
        self.assertTrue(result["failures"])

    def test_the_gate_passes_when_nothing_is_broken(self):
        result = check_the_program_you_type_is_the_program_that_was_checked(
            iterations=1
        )
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual(result["name"], "shadow_guard")


class ShadowGateIsWiredTest(unittest.TestCase):
    def test_the_gate_is_in_the_selftest_suite(self):
        from mas.selftest import CHECKS

        names = [getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS]
        self.assertIn("shadow_guard", names)

    def test_the_gate_can_be_run_on_its_own(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["shadow_guard"])
        self.assertTrue(result["ok"], result.get("failed"))
        self.assertEqual(len(result["checks"]), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

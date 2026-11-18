# -*- coding: utf-8 -*-
"""The twenty-fourth gate must stay honest on its own terms.

The gate in ``mas/selftest_packaged_docs.py`` watches the packaging of
the onboarding documents. This file makes sure the *gate itself* is
well formed: the observations it claims to make are the ones it
actually makes, the two readings agree, and it can still fail.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import pathlib
import shutil
import unittest

import mas.selftest_packaged_docs as gate_module
from mas.selftest_packaged_docs import (
    PACKAGED_DOCS_OBSERVATIONS,
    _packaged_docs_observations,
    _packaged_docs_verdicts,
    _packaged_docs_verdicts_independent,
    check_the_documents_the_beginner_reads_travel_with_the_wheel,
)


class PackagedDocsGateTest(unittest.TestCase):
    def setUp(self):
        self.facts = _packaged_docs_observations(".")

    def test_the_gate_passes_on_the_real_package(self):
        verdicts = _packaged_docs_verdicts(self.facts)
        for label in PACKAGED_DOCS_OBSERVATIONS:
            self.assertTrue(verdicts.get(label), "%s does not hold" % label)

    def test_every_observation_is_actually_observed(self):
        verdicts = _packaged_docs_verdicts(self.facts)
        for label in PACKAGED_DOCS_OBSERVATIONS:
            self.assertIn(label, verdicts)

    def test_the_two_readings_agree(self):
        verdicts = _packaged_docs_verdicts(self.facts)
        independent = _packaged_docs_verdicts_independent(self.facts)
        for label in PACKAGED_DOCS_OBSERVATIONS:
            self.assertEqual(
                verdicts.get(label),
                independent.get(label),
                "%s: the two readings disagree" % label,
            )

    def test_the_gate_is_named(self):
        self.assertEqual(
            getattr(
                check_the_documents_the_beginner_reads_travel_with_the_wheel,
                "CHECK_NAME",
                "",
            ),
            "packaged_docs",
        )

    def test_the_gate_reports_its_own_invariant(self):
        result = check_the_documents_the_beginner_reads_travel_with_the_wheel(
            iterations=1
        )
        self.assertTrue(result["ok"], result.get("failures"))
        self.assertEqual(result["name"], "packaged_docs")
        self.assertTrue(result["invariant"])

    def test_the_gate_notices_missing_documents(self):
        """Hide the packaged documents: the gate has to go red."""
        saved = pathlib.Path(gate_module.PACKAGED_DOCS)
        backup = pathlib.Path(str(saved) + ".testbak")
        if backup.exists():
            shutil.rmtree(backup)
        shutil.move(str(saved), str(backup))
        try:
            result = check_the_documents_the_beginner_reads_travel_with_the_wheel(
                iterations=1
            )
            self.assertFalse(result["ok"], "the gate did not notice")
            self.assertTrue(result["failures"])
        finally:
            shutil.move(str(backup), str(saved))
            self.assertTrue(saved.exists())

    def test_the_gate_notices_a_drifted_copy(self):
        """A drifted copy is worse than a missing one: the gate would
        check prose nobody is shipped."""
        target = pathlib.Path(gate_module.PACKAGED_DOCS) / "README.md"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n<!-- drift -->\n")
            result = check_the_documents_the_beginner_reads_travel_with_the_wheel(
                iterations=1
            )
            self.assertFalse(result["ok"], "the gate did not notice the drift")
            self.assertTrue(
                any("matches the repository" in f for f in result["failures"]),
                result["failures"],
            )
        finally:
            target.write_bytes(original)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

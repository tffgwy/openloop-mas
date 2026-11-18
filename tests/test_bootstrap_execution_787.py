# -*- coding: utf-8 -*-
"""The twenty-seventh gate: the bootstrap path is executed, not described.

The gap this version closes
---------------------------
The twenty-sixth gate compares the bootstrap rules in the module with
the copies inside the two installers, and fails when they drift. That
is a real check - but it checks the *text*.

It never runs the bootstrap. Neither does the CI: every install job
starts with ``actions/setup-python``, so the runner has a Python before
the installer looks for one. The branch that says "this machine has no
Python" was therefore executed **nowhere**.

These tests hold that:

* the Store-stub rule is right in both directions;
* the gate is registered and passes on this tree;
* the gate can fail - it notices a lookup that finds nothing.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import unittest

from mas.bootstrap import is_store_stub
from mas.selftest import CHECKS
from mas.selftest_bootstrap_execution import (
    BOOTSTRAP_EXECUTION_OBSERVATIONS,
    check_the_bootstrap_path_is_executed_and_not_just_described,
)


class StoreStubTest(unittest.TestCase):
    """The Store alias is not an interpreter."""

    def test_a_store_alias_is_recognised(self):
        self.assertTrue(
            is_store_stub(
                r"C:\Users\Someone\AppData\Local\Microsoft\WindowsApps\python3.exe"
            )
        )
        self.assertTrue(
            is_store_stub(
                r"C:\Users\Someone\AppData\Local\Microsoft\WindowsApps\python.exe"
            )
        )

    def test_a_real_interpreter_is_not_a_stub(self):
        self.assertFalse(is_store_stub(r"C:\Python312\python.exe"))
        self.assertFalse(is_store_stub(r"C:\Users\Someone\Python312\python.exe"))

    def test_a_store_installed_python_is_not_a_stub(self):
        # The Store installs real interpreters under its own Programs
        # folder. Those are real, and calling them stubs would send
        # the installer off to download a second copy.
        self.assertFalse(
            is_store_stub(
                r"C:\Users\Someone\AppData\Local\Microsoft\WindowsApps\Programs\Python312\python.exe"
            )
        )

    def test_an_empty_path_is_not_a_stub(self):
        self.assertFalse(is_store_stub(""))
        self.assertFalse(is_store_stub(None))


class TheGateTest(unittest.TestCase):
    """The twenty-seventh gate itself."""

    def test_the_gate_is_registered(self):
        names = [getattr(c, "CHECK_NAME", "") for c in CHECKS]
        self.assertIn("bootstrap_execution", names)

    def test_the_gate_reports_every_observation(self):
        self.assertEqual(len(BOOTSTRAP_EXECUTION_OBSERVATIONS), 7)

    def test_the_gate_passes_on_this_tree(self):
        result = check_the_bootstrap_path_is_executed_and_not_just_described()
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual(result["name"], "bootstrap_execution")
        self.assertFalse(result["skipped"])

    def test_the_path_only_observation_is_watched(self):
        # The gate has to watch for an installer that cannot find a
        # Python which is sitting right there on PATH. That is the
        # defect this round found: the shell's own command resolution
        # was asked, and it reported "no such command" for a file that
        # existed.
        import mas.selftest_bootstrap_execution as gate_module

        broken = gate_module._bootstrap_verdicts(
            {"output": "", "result": {"returncode": 0}}
        )
        self.assertFalse(
            broken["the installer finds a Python that is only on PATH"]
        )

    def test_the_gate_can_fail(self):
        # A gate that cannot fail is not watching. The verdict for the
        # counter-proof has to be false when the lookup finds nothing.
        import mas.selftest_bootstrap_execution as gate_module

        broken = gate_module._bootstrap_verdicts(
            {"output": "", "result": {"returncode": 0}}
        )
        self.assertFalse(broken["the interpreter is found by the production lookup"])
        self.assertFalse(broken["the bootstrap branch is really entered"])
        self.assertFalse(broken["the installation that follows is complete"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

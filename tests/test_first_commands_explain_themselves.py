# -*- coding: utf-8 -*-
"""The first commands a beginner runs must explain themselves.

``mas status``, ``mas demo`` and ``mas report`` are the three commands a
new user meets first - often before they have run anything at all. They
used to answer in machine words:

* ``mas status`` printed "provider: mock" and a column of zeroes. "mock"
  is not a word a newcomer knows, and zeroes do not say whether the
  tool is broken or simply unused.
* ``mas demo`` printed three lines of "expect=closed actual=closed".
  That is the engine talking to itself, not to the person watching.
* ``mas report`` ended with "账本为空，暂无运行记录。" - true, and a
  dead end: it names no next step.

These tests pin down the human-facing half of each command.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import contextlib
import io
import os
import pathlib
import tempfile
import unittest

from mas.cli import DEMO_SCENARIO_MEANING, main


class _Capture:
    def cap(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()


class _EmptyLedgerTestCase(unittest.TestCase):
    """A machine on which nothing has ever run.

    The session-wide isolation gives every test the same state
    directory, and another test in the same session may have run a
    task in it. A test that checks "the report on an *empty* ledger"
    then reads a ledger with two runs in it and fails for a reason that
    has nothing to do with the code under test. These tests own their
    own empty state directory instead.
    """

    def setUp(self):
        self._saved = {
            name: os.environ.get(name)
            for name in ("MAS_CONFIG_PATH", "MAS_STATE_DIR")
        }
        self._root = tempfile.mkdtemp(prefix="mas-empty-ledger-")
        self._state = pathlib.Path(self._root) / "state"
        self._state.mkdir(parents=True, exist_ok=True)
        os.environ["MAS_STATE_DIR"] = str(self._state)
        os.environ["MAS_CONFIG_PATH"] = str(pathlib.Path(self._root) / "config.json")

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class StatusExplainsItselfTest(_Capture, _EmptyLedgerTestCase):
    def test_the_offline_mode_is_named_in_words(self):
        code, out, _err = self.cap(["status"])
        self.assertEqual(code, 0)
        self.assertIn("离线演示模式", out)
        self.assertIn("不联网", out)

    def test_the_offline_mode_says_how_to_switch_to_a_real_model(self):
        _code, out, _err = self.cap(["status"])
        self.assertIn("mas init", out)

    def test_an_unused_machine_says_zero_is_not_a_failure(self):
        _code, out, _err = self.cap(["status"])
        self.assertIn("这不是故障", out)
        self.assertIn("mas demo", out)


class DemoExplainsItselfTest(_Capture, unittest.TestCase):
    def test_each_scenario_says_what_it_proves(self):
        code, out, _err = self.cap(["demo"])
        self.assertEqual(code, 0)
        for scenario, meaning in DEMO_SCENARIO_MEANING.items():
            # An empty meaning would make ``assertIn(meaning, out)``
            # succeed on any output - the assertion would be vacuous.
            # Check the sentence itself before looking for it.
            self.assertTrue(meaning.strip(), scenario)
            self.assertIn(meaning, out, scenario)

    def test_all_three_scenarios_are_covered(self):
        self.assertEqual(
            sorted(DEMO_SCENARIO_MEANING),
            ["budget", "closure", "stagnation"],
        )

    def test_the_demo_says_it_is_offline(self):
        _code, out, _err = self.cap(["demo"])
        self.assertIn("离线演示", out)

    def test_the_demo_ends_with_a_next_step(self):
        _code, out, _err = self.cap(["demo"])
        self.assertIn("下一步", out)


class ReportOnAnEmptyLedgerTest(_Capture, _EmptyLedgerTestCase):
    def test_the_report_names_the_next_command(self):
        code, out, _err = self.cap(["report"])
        self.assertEqual(code, 0)
        self.assertIn("这不是故障", out)
        self.assertIn("mas demo", out)

    def test_the_report_still_says_the_ledger_is_empty(self):
        _code, out, _err = self.cap(["report"])
        self.assertIn("账本为空", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

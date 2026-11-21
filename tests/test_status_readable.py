# -*- coding: utf-8 -*-
"""v0.7.47 regression tests: `mas status` must be readable by a person.

The status command printed a raw JSON payload and nothing else. That
is fine for a machine and useless for the beginner the project is
written for: the JSON says "entries: 27" and "waiting_human: [T-...]"
and never says "you have 2 deliverables and 1 ticket waiting for you".
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

    No assertion here: `mas run` exits non-zero when the criteria are
    not met, and that is a normal outcome this file needs to observe.
    """
    os.environ["MAS_STATE_DIR"] = state_dir
    os.environ["MAS_PROVIDER"] = "mock"
    os.environ.pop("MAS_API_KEY", None)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


def _run_cli(argv, state_dir):
    """Run a command that is expected to succeed."""
    code, out = _invoke(argv, state_dir)
    if code != 0:
        raise AssertionError(f"mas {argv} exited {code}: {out}")
    return code, out


class StatusIsReadableTest(unittest.TestCase):
    """`mas status` must talk to a human, not dump a payload."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-status-")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        self._saved_provider = os.environ.get("MAS_PROVIDER")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        if self._saved_provider is None:
            os.environ.pop("MAS_PROVIDER", None)
        else:
            os.environ["MAS_PROVIDER"] = self._saved_provider

    def _run(self, goal="写一份上手指南", criteria="contains:交付物"):
        # A run that cannot meet its criteria exits non-zero on
        # purpose; the return value is not the point of this helper.
        return _invoke(
            ["run", "--goal", goal, "--criteria", criteria], self.state_dir
        )

    def test_the_default_output_is_not_json(self):
        code, out = _run_cli(["status"], self.state_dir)
        self.assertEqual(code, 0)
        # A raw JSON dump starts with a brace; the readable form does
        # not.
        self.assertFalse(out.lstrip().startswith("{"), out[:80])

    def test_the_default_output_says_the_provider(self):
        _code, out = _run_cli(["status"], self.state_dir)
        self.assertIn("provider", out)

    def test_the_default_output_counts_the_runs(self):
        self._run()
        _code, out = _run_cli(["status"], self.state_dir)
        # "尝试" and "问题" are two grains. The page has to name
        # the grain, or the reader cannot tell which number means
        # "there is work for me".
        self.assertIn("尝试 1 次", out)
        self.assertIn("已闭环 1", out)
        self.assertIn("仍在等 0", out)

    def test_the_default_output_lists_the_deliverables(self):
        self._run(goal="写一份上手指南")
        _code, out = _run_cli(["status"], self.state_dir)
        self.assertIn("交付物：1 份", out)
        self.assertIn("写一份上手指南", out)

    def test_the_default_output_names_the_waiting_tickets(self):
        self._run(goal="不可能达成", criteria="regex:^NEVER_MATCHES$")
        _code, out = _run_cli(["status"], self.state_dir)
        self.assertIn("待处理工单：1 个", out)
        self.assertIn("mas resume", out)

    def test_the_default_output_says_when_there_is_no_ticket(self):
        self._run()
        _code, out = _run_cli(["status"], self.state_dir)
        self.assertIn("待处理工单：0 个", out)

    def test_the_default_output_says_the_budget(self):
        _code, out = _run_cli(["status"], self.state_dir)
        self.assertIn("预算：", out)

    def test_a_missing_deliverable_is_called_out(self):
        self._run()
        _code, out = _run_cli(["status", "--json"], self.state_dir)
        row = json.loads(out)["artifacts"]["items"][0]
        os.remove(row["path"])
        _code, out = _run_cli(["status"], self.state_dir)
        self.assertIn("缺失", out)
        self.assertIn("已不在原处", out)

    def test_the_json_output_carries_the_new_sections(self):
        self._run()
        code, out = _run_cli(["status", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["runs"]["total"], 1)
        self.assertEqual(payload["runs"]["closed"], 1)
        self.assertEqual(payload["runs"]["open"], 0)
        self.assertEqual(payload["artifacts"]["count"], 1)
        self.assertEqual(payload["artifacts"]["missing"], 0)
        self.assertEqual(payload["artifacts"]["items"][0]["title"], "写一份上手指南")

    def test_the_json_output_still_has_the_old_sections(self):
        _code, out = _run_cli(["status", "--json"], self.state_dir)
        payload = json.loads(out)
        self.assertIn("config", payload)
        self.assertIn("ledger", payload)
        self.assertIn("tickets", payload)

    def test_the_empty_state_is_readable_too(self):
        code, out = _run_cli(["status"], self.state_dir)
        self.assertEqual(code, 0)
        self.assertIn("账本：0 条", out)
        self.assertIn("交付物：0 份", out)


if __name__ == "__main__":
    unittest.main()

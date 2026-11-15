# -*- coding: utf-8 -*-
"""The ticket queue names the standard, not the serial number.

``mas tickets`` showed the blocking failure as::

    \u5361\u5728\uff1ac1: unjudged: \u8be5\u9a8c\u6536\u6807\u51c6\u6ca1\u6709\u673a\u68c0\u89c4\u5219\uff0cmock \u6a21\u5f0f\u65e0\u6cd5\u5224\u5b9a

``c1`` is a serial number the engine made up, and the note is in the
engine's vocabulary. The ticket is the one screen a person reads when
they come back to pick the work up, so it has to say *which of their
standards* is blocked.
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.cli import _issue_reading


class IssueReadingTest(unittest.TestCase):
    def test_the_serial_number_is_replaced_by_the_standard(self):
        criteria = [{"id": "c1", "desc": "\u5fc5\u987b\u6709\u7ed3\u8bba\u6bb5"}]
        reading = _issue_reading("c1: unjudged: \u8be5\u6807\u51c6\u6ca1\u6709\u673a\u68c0\u89c4\u5219", criteria)
        self.assertIn("\u5fc5\u987b\u6709\u7ed3\u8bba\u6bb5", reading)
        self.assertNotIn("c1:", reading)

    def test_the_note_is_translated(self):
        criteria = [{"id": "c1", "desc": "\u5fc5\u987b\u6709\u7ed3\u8bba\u6bb5"}]
        reading = _issue_reading("c1: unjudged: \u8be5\u6807\u51c6\u6ca1\u6709\u673a\u68c0\u89c4\u5219", criteria)
        self.assertIn("\u6ca1\u6709\u4eba\u5224\u5b9a", reading)
        self.assertNotIn("unjudged:", reading)

    def test_a_standard_with_no_description_still_reads(self):
        criteria = [{"id": "c1", "desc": ""}]
        reading = _issue_reading("c1: no machine check", criteria)
        self.assertIn("\u6ca1\u6709\u673a\u68c0", reading)

    def test_an_unknown_issue_keeps_its_own_text(self):
        reading = _issue_reading("something nobody predicted", [])
        self.assertEqual(reading, "something nobody predicted")

    def test_an_issue_without_a_serial_number_still_reads(self):
        reading = _issue_reading("all required strings present", [])
        self.assertIn("\u6240\u6709\u8981\u6c42\u7684\u5b57\u7b26\u4e32\u90fd\u5728", reading)


class TicketQueueTest(unittest.TestCase):
    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="mas-ticket-reading-"))
        cfg = {
            "provider": "mock",
            "model": "mock-1",
            "state_dir": str(self.state),
            "budget": {"deadline_sec": 60, "max_iterations": 1, "max_tokens": 20000},
        }
        self.config = self.state / "config.json"
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self._old_config = os.environ.get("MAS_CONFIG_PATH")
        self._old_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_CONFIG_PATH"] = str(self.config)
        os.environ["MAS_STATE_DIR"] = str(self.state)

    def tearDown(self):
        if self._old_config is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._old_config
        if self._old_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._old_state

    def _cap(self, argv):
        out, err = io.StringIO(), io.StringIO()
        from mas.cli import main

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
        return code, out.getvalue(), err.getvalue()

    def test_the_queue_names_the_standard_that_is_blocked(self):
        # A file check the mock backend can never satisfy forces
        # the run to escalate, so a ticket is opened.
        self._cap([
            "run",
            "--goal", "写一份说明",
            "--criteria", "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
        ])
        code, out, _err = self._cap(["tickets"])
        self.assertEqual(code, 0)
        self.assertIn("\u5361\u5728\uff1a", out)
        self.assertNotIn("\u5361\u5728\uff1ac1:", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

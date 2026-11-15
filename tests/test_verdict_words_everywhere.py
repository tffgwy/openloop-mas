# -*- coding: utf-8 -*-
"""Every screen says the verdict in words.

``mas run`` and ``mas report`` are the two screens a person actually
reads. v0.7.56 put the standard in words underneath the verdict; v0.7.57
puts the verdict itself in words - and, on the same journey, fixes the
places where the words were still missing:

* ``mas resume`` said the verdict in a different shape than ``mas run``;
* the report's "nobody judged it" line showed a raw source value;
* the status vocabulary did not cover the states the engine writes when
  a run does not close.
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.verdict_text import note_reading, source_reading, status_reading


class TheVocabulariesCoverTheEngineTest(unittest.TestCase):
    """Every state and source the engine writes has a reading."""

    def test_the_states_a_run_ends_in_all_read(self):
        for status in (
            "closed",
            "escalated",
            "budget_exhausted",
            "error",
            "not_closed",
            "incomplete",
            "deliverable_ok_but_not_closed",
        ):
            reading = status_reading(status)
            self.assertTrue(reading, "no reading for status " + status)
            self.assertNotEqual(reading, status, "status " + status + " was left in English")
            # A placeholder is not a reading: the words have to be
            # Chinese, because that is the whole point of the module.
            self.assertTrue(
                any("\u4e00" <= ch <= "\u9fff" for ch in reading),
                "status " + status + " has no Chinese in its reading: " + reading,
            )

    def test_the_sources_a_verdict_comes_from_all_read(self):
        for source in ("rule", "file", "review", "lead", "none", "invalid",
                       "model-missing", "skipped", "model"):
            reading = source_reading(source)
            self.assertTrue(reading, "no reading for source " + source)
            self.assertNotEqual(reading, source, "source " + source + " was left in English")
            self.assertTrue(
                any("\u4e00" <= ch <= "\u9fff" for ch in reading),
                "source " + source + " has no Chinese in its reading: " + reading,
            )

    def test_the_unjudged_note_says_nobody_judged_it(self):
        self.assertIn("没有人判定", note_reading("unjudged: 评审没有给出该标准的判定"))


class TheResumeScreenSpeaksTheSameLanguageTest(unittest.TestCase):
    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="mas-resume-words-"))
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

    def test_the_resumed_verdict_reads_like_the_first_one(self):
        # A file check the mock backend can never satisfy forces the
        # run to escalate, so a ticket is opened and can be resumed.
        self._cap([
            "run",
            "--goal", "写一份说明",
            "--criteria", "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
        ])
        code, out, _err = self._cap(["tickets"])
        self.assertEqual(code, 0)
        ticket = ""
        for line in out.split("\n"):
            if line.startswith("T-"):
                ticket = line.split()[0]
                break
        self.assertTrue(ticket, "no ticket was opened: " + out)
        code, out, _err = self._cap(["resume", ticket])
        # The verdict line names where the verdict came from, exactly
        # like ``mas run`` does.
        self.assertIn("文件检查：", out)
        # And the standard is quoted back underneath it.
        self.assertIn("要求：", out)


class TheUnjudgedLineNamesItsSourceTest(unittest.TestCase):
    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="mas-unjudged-words-"))
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

    def test_the_unjudged_source_is_read_and_keeps_its_raw_value(self):
        from mas.report import build_report, render_markdown

        rows = [
            {"seq": 1, "ts": "t1", "run_id": "R-u", "task_id": "R-u-1",
             "iteration": 1, "role": "closure", "verdict": "fail",
             "decision": "revise",
             "scores": [{"id": "c2", "pass": None, "note": "no machine check",
                         "source": "model-missing"}],
             "issues": [], "stagnation": 0, "closed": False,
             "artifact_sha": "sha1111", "tokens_total": 100},
            {"seq": 2, "ts": "t2", "run_id": "R-u", "task_id": "R-u-1",
             "iteration": 1, "role": "run_end", "status": "escalated",
             "decision": "escalate", "iterations": 1, "closed": False,
             "deliverable_ok": False, "ticket_id": "T-1",
             "artifact_sha": "sha1111", "issues": [], "tokens_total": 100},
        ]
        markdown = render_markdown(build_report(rows))
        # Only the unjudged section carries the source, so the
        # assertion is scoped to it - the verdict list above repeats
        # the note, and would hide a broken translation.
        marker = "无人判定的标准"
        self.assertIn(marker, markdown)
        section = markdown[markdown.index(marker):]
        self.assertIn("评审没给出判定", section)
        # The raw value stays visible: a reader greps for it.
        self.assertIn("model-missing", section)
        # And the note itself is in words.
        self.assertIn("这条标准没有机检规则", section)
        self.assertNotIn("no machine check", section)



class TheReportScreenSaysItInWordsTest(unittest.TestCase):
    """The report is the screen a beginner reads after the run."""

    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-words-"))
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

    def _one_closed_run(self):
        self._cap(["run", "--goal", "写一份说明", "--criteria", "contains:结论"])

    def test_the_run_line_says_where_the_verdict_came_from(self):
        self._one_closed_run()
        code, out, _err = self._cap(["run", "--goal", "再写一份", "--criteria", "contains:结论"])
        self.assertEqual(code, 0)
        self.assertIn("机检：", out)
        self.assertNotIn("(rule)", out)

    def test_the_report_table_says_the_status_in_words(self):
        self._one_closed_run()
        code, out, _err = self._cap(["report"])
        self.assertEqual(code, 0)
        # The overview row, not the totals line: "已闭环" also appears
        # in the summary, so only the cell carries the raw value.
        self.assertIn("已闭环（closed）", out)

    def test_the_report_table_says_the_decision_in_words(self):
        self._one_closed_run()
        code, out, _err = self._cap(["report"])
        self.assertEqual(code, 0)
        self.assertIn("闭环了（close）", out)

    def test_the_report_verdict_line_says_why_in_words(self):
        self._one_closed_run()
        code, out, _err = self._cap(["report"])
        self.assertEqual(code, 0)
        self.assertIn("所有要求的字符串都在", out)
        self.assertNotIn("all required strings present", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

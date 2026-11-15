# -*- coding: utf-8 -*-
"""The verdict says why, in words.

The engine records *why* a criterion passed or failed in a note. Those
notes are written for the machine - ``all required strings present``,
``missing: ['\u7ed3\u8bba']``, ``length 42 < 80`` - and they used to
land on the screen right next to the user's own standards.

These tests pin the two rules that govern the translation:
  - the ledger keeps its English (the audit record must not change);
  - an unrecognised note is left alone (never invent a verdict).
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.verdict_text import (
    decision_reading,
    note_reading,
    source_reading,
    status_reading,
)


class NoteReadingTest(unittest.TestCase):
    def test_the_pass_note_reads_in_words(self):
        self.assertEqual(
            note_reading("all required strings present"),
            "\u6240\u6709\u8981\u6c42\u7684\u5b57\u7b26\u4e32\u90fd\u5728",
        )

    def test_the_fail_note_names_what_is_missing(self):
        reading = note_reading("missing: ['\u7ed3\u8bba']")
        self.assertIn("\u7ed3\u8bba", reading)
        self.assertNotIn("[", reading)
        self.assertNotIn("'", reading)

    def test_a_length_note_says_which_side_it_fell_on(self):
        self.assertIn("\u8d85\u8fc7", note_reading("length 42 < 80"))
        self.assertIn("\u4e0d\u5c11\u4e8e", note_reading("length 100 >= 80"))

    def test_a_file_note_names_the_file(self):
        reading = note_reading("file not found: out/report.md")
        self.assertIn("out/report.md", reading)
        self.assertIn("\u627e\u4e0d\u5230", reading)

    def test_a_regex_note_keeps_the_pattern(self):
        reading = note_reading("regex /\\d{3}/ matched")
        self.assertIn("\\d{3}", reading)

    def test_a_section_note_names_the_section(self):
        self.assertIn("\u7ed3\u8bba", note_reading("section '## \u7ed3\u8bba' found"))

    def test_a_sha_note_says_it_did_not_match(self):
        reading = note_reading("sha256 abc123def456 != 999888777000")
        self.assertIn("abc123def456", reading)
        self.assertIn("999888777000", reading)

    def test_an_mtime_note_says_which_side(self):
        reading = note_reading(
            "mtime 2026-11-15T11:14:06+00:00 is not newer than 2099-01-01T00:00:00+00:00"
        )
        self.assertIn("2026-11-15T11:14:06+00:00", reading)
        self.assertIn("2099-01-01T00:00:00+00:00", reading)

    def test_the_no_machine_check_note_reads(self):
        self.assertIn("\u6ca1\u6709\u673a\u68c0", note_reading("no machine check"))

    def test_an_unknown_note_is_left_alone(self):
        # An unknown note may carry information this module cannot
        # see. Returning a translation would throw that away.
        self.assertEqual(note_reading("something nobody predicted"), "")
        self.assertEqual(note_reading(""), "")


class VocabularyTest(unittest.TestCase):
    def test_the_status_reads_in_words(self):
        self.assertEqual(status_reading("closed"), "\u5df2\u95ed\u73af")
        self.assertEqual(status_reading("budget_exhausted"), status_reading("budget_exhausted"))
        self.assertNotEqual(status_reading("budget_exhausted"), "")

    def test_the_decision_reads_in_words(self):
        self.assertEqual(decision_reading("close"), "\u95ed\u73af\u4e86")
        self.assertEqual(decision_reading("revise"), "\u8981\u6539\u4e00\u7248")

    def test_the_source_reads_in_words(self):
        self.assertEqual(source_reading("rule"), "\u673a\u68c0")
        self.assertEqual(source_reading("file"), "\u6587\u4ef6\u68c0\u67e5")

    def test_an_unknown_status_is_left_alone(self):
        self.assertEqual(status_reading("some_new_state"), "some_new_state")
        self.assertEqual(status_reading(""), "")


class TheScreenShowsTheWordsTest(unittest.TestCase):
    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="mas-verdict-text-"))
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

    def test_the_run_output_says_the_verdict_in_words(self):
        code, out, _err = self._cap(
            ["run", "--goal", "\u5199\u4e00\u4efd\u8bf4\u660e", "--criteria", "contains:\u7ed3\u8bba"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("all required strings present", out)
        self.assertIn("\u6240\u6709\u8981\u6c42\u7684\u5b57\u7b26\u4e32\u90fd\u5728", out)

    def test_the_report_says_the_verdict_in_words(self):
        self._cap(["run", "--goal", "\u5199\u4e00\u4efd\u8bf4\u660e", "--criteria", "contains:\u7ed3\u8bba"])
        code, out, _err = self._cap(["report"])
        self.assertEqual(code, 0)
        self.assertNotIn("all required strings present", out)

    def test_the_ledger_keeps_its_english(self):
        # The audit record must not change: a script may key on the
        # note, and a stored run means what it meant when it was stored.
        self._cap(["run", "--goal", "\u5199\u4e00\u4efd\u8bf4\u660e", "--criteria", "contains:\u7ed3\u8bba"])
        ledger = self.state / "ledgers" / "runs.jsonl"
        self.assertTrue(ledger.exists(), "no ledger was written")
        text = ledger.read_text(encoding="utf-8")
        self.assertIn("all required strings present", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# -*- coding: utf-8 -*-
"""The acceptance result must say which standard was judged.

The user writes three standards and the loop decides each of them. The
report used to say ``[PASS] c1 (rule) all required strings present`` -
a serial number and an English sentence. Neither half identifies the
standard the user actually wrote, and "all required strings present"
does not say what "required" means here.

These tests pin down the human reading of a criterion and the way it
reaches the CLI and the report.
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
import unittest

from mas.criteria_text import (
    CHECK_TYPE_READING,
    check_reading,
    criterion_reading,
    describe_scores,
)
from mas.engine import Criterion
from mas.util import parse_criterion_shorthand


class CheckReadingTest(unittest.TestCase):
    def test_a_contains_rule_reads_as_a_sentence(self):
        self.assertEqual(
            check_reading({"type": "contains", "arg": "结论"}),
            "正文里要出现「结论」",
        )

    def test_a_min_chars_rule_reads_as_a_sentence(self):
        self.assertIn("至少 220", check_reading({"type": "min_chars", "arg": 220}))

    def test_a_file_rule_names_the_file(self):
        reading = check_reading(
            {"type": "file_contains", "arg": {"path": "out/report.md", "value": "结论"}}
        )
        self.assertIn("out/report.md", reading)
        self.assertIn("结论", reading)

    def test_a_criterion_without_a_check_has_no_reading(self):
        self.assertEqual(check_reading(None), "")
        self.assertEqual(check_reading({}), "")

    def test_an_unknown_check_type_says_so_instead_of_raising(self):
        self.assertIn("unknown_kind", check_reading({"type": "unknown_kind", "arg": "x"}))

    def test_every_reading_template_has_the_placeholders_it_needs(self):
        for kind, template in CHECK_TYPE_READING.items():
            self.assertTrue(template.strip(), kind)
            self.assertIn("{", template, kind)


class CriterionReadingTest(unittest.TestCase):
    def test_a_shorthand_criterion_reads_as_the_rule_says(self):
        parsed = parse_criterion_shorthand("contains:结论")
        criterion = Criterion.from_raw("contains:结论", 0)
        reading = criterion_reading(criterion)
        self.assertEqual(reading, "正文里要出现「结论」")
        # The raw shorthand must not be repeated next to its reading.
        self.assertNotIn("contains:结论", reading)
        self.assertTrue(parsed["check"])

    def test_a_written_description_is_kept_and_the_rule_is_appended(self):
        criterion = Criterion.from_raw(
            {"desc": "必须有结论段", "check": {"type": "contains", "arg": "结论"}},
            0,
        )
        reading = criterion_reading(criterion)
        self.assertIn("必须有结论段", reading)
        self.assertIn("正文里要出现「结论」", reading)

    def test_an_optional_criterion_says_it_does_not_block(self):
        criterion = Criterion.from_raw(
            {"desc": "锦上添花", "check": {"type": "contains", "arg": "x"}, "required": False},
            0,
        )
        self.assertIn("可选", criterion_reading(criterion))

    def test_a_criterion_with_nothing_at_all_still_reads(self):
        reading = criterion_reading({"id": "c1", "desc": "", "check": None})
        self.assertTrue(reading.strip())


class DescribeScoresTest(unittest.TestCase):
    def test_the_reading_is_joined_to_the_score_by_id(self):
        crits = [Criterion.from_raw("contains:结论", 0), Criterion.from_raw("min_chars:220", 1)]
        scores = [
            {"id": "c1", "pass": True, "note": "ok"},
            {"id": "c2", "pass": False, "note": "too short"},
        ]
        described = describe_scores(scores, crits)
        self.assertIn("结论", described[0]["reading"])
        self.assertIn("220", described[1]["reading"])

    def test_a_score_without_a_criterion_still_has_a_reading_key(self):
        described = describe_scores([{"id": "c9", "pass": True}], [])
        self.assertEqual(described[0]["reading"], "")


class CliShowsTheRequirementTest(unittest.TestCase):
    def _cap(self, argv):
        out, err = io.StringIO(), io.StringIO()
        from mas.cli import main

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_the_run_output_names_the_standard(self):
        code, out, _err = self._cap(
            ["run", "--goal", "写一份说明", "--criteria", "contains:结论"]
        )
        self.assertEqual(code, 0)
        self.assertIn("要求：", out)
        self.assertIn("正文里要出现「结论」", out)

    def test_the_report_quotes_the_standard_back(self):
        self._cap(["run", "--goal", "写一份说明", "--criteria", "contains:结论"])
        code, out, _err = self._cap(["report"])
        self.assertEqual(code, 0)
        self.assertIn("要求：", out)
        self.assertIn("正文里要出现「结论」", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

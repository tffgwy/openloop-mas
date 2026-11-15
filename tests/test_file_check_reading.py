# -*- coding: utf-8 -*-
"""Regression: a file-level check must never print a Python dict.

The first version of the plain-language reading stringified whatever
``arg`` was. The file-level checks carry their argument as
``{"path": ..., "value": ...}``, so the requirement line came out as

    文件 {'path': 'out/report.md', 'value': '...'} 的修改时间要晚于 {...}

That is a repr, not a sentence. These tests pin the fix.
"""

import unittest

from mas.criteria_text import check_reading, criterion_reading


class FileCheckReadingTest(unittest.TestCase):
    def test_a_file_check_names_the_path_and_the_expectation(self):
        reading = check_reading(
            {
                "type": "file_contains",
                "arg": {"path": "out/report.md", "value": "结论"},
            }
        )
        self.assertEqual(reading, "文件 out/report.md 里要出现「结论」")

    def test_a_file_check_never_leaks_a_dict(self):
        reading = check_reading(
            {
                "type": "file_mtime_newer",
                "arg": {"path": "out/report.md", "value": "2099-01-01T00:00:00Z"},
            }
        )
        self.assertNotIn("{", reading)
        self.assertNotIn("'", reading)
        self.assertIn("out/report.md", reading)
        self.assertIn("2099-01-01T00:00:00Z", reading)

    def test_a_path_only_file_check_still_reads(self):
        reading = check_reading({"type": "file_exists", "arg": {"path": "out/report.md"}})
        self.assertEqual(reading, "文件 out/report.md 必须存在")

    def test_a_text_check_is_unaffected(self):
        reading = check_reading({"type": "contains", "arg": "结论"})
        self.assertEqual(reading, "正文里要出现「结论」")

    def test_a_malformed_file_argument_does_not_raise(self):
        reading = check_reading({"type": "file_contains", "arg": {"value": "结论"}})
        self.assertNotIn("{", reading)
        self.assertIn("结论", reading)

    def test_a_criterion_over_a_file_check_reads_cleanly(self):
        criterion = {
            "id": "c1",
            "desc": "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
            "required": True,
            "check": {
                "type": "file_mtime_newer",
                "arg": {"path": "out/report.md", "value": "2099-01-01T00:00:00Z"},
            },
        }
        reading = criterion_reading(criterion)
        self.assertNotIn("{", reading)
        self.assertIn("out/report.md", reading)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

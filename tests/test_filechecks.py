"""Tests for the file-level machine checks (v0.7.15)."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from mas.engine import Criterion, evaluate_check, validate_criteria
from mas.filechecks import (
    FILE_CHECK_TYPES,
    evaluate_file_check,
    file_check_problem,
    parse_file_check_arg,
    resolve_path,
)
from mas.util import parse_criterion_shorthand


class FileCheckArgumentTests(unittest.TestCase):
    def test_object_form(self):
        path, value = parse_file_check_arg({"path": "a/b.md", "value": "结论"})
        self.assertEqual(path, "a/b.md")
        self.assertEqual(value, "结论")

    def test_string_form_splits_on_double_colon(self):
        path, value = parse_file_check_arg("a/b.md::结论")
        self.assertEqual(path, "a/b.md")
        self.assertEqual(value, "结论")

    def test_windows_drive_letter_survives(self):
        path, value = parse_file_check_arg("C:/tmp/a.md::x")
        self.assertEqual(path, "C:/tmp/a.md")
        self.assertEqual(value, "x")

    def test_missing_path_is_an_error(self):
        from mas.filechecks import FileCheckError

        with self.assertRaises(FileCheckError):
            parse_file_check_arg({"value": "x"})

    def test_path_outside_workspace_is_rejected(self):
        from mas.filechecks import FileCheckError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileCheckError):
                resolve_path(root, "../outside.txt")
            with self.assertRaises(FileCheckError):
                resolve_path(root, str((root.parent / "elsewhere.txt").resolve()))

    def test_relative_traversal_inside_workspace_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            target = root / "sub" / "x.md"
            target.write_text("x", encoding="utf-8")
            resolved = resolve_path(root, "sub/x.md")
            self.assertEqual(resolved, target.resolve())


class FileCheckEvaluationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.target = self.root / "report.md"
        self.target.write_text("# 标题\n\n结论：已完成\n", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def check(self, kind, arg):
        return evaluate_file_check(kind, arg, self.root)

    def test_file_exists(self):
        outcome = self.check("file_exists", {"path": "report.md"})
        self.assertTrue(outcome["pass"])
        self.assertEqual(outcome["source"], "file")

    def test_file_exists_missing_is_a_failure(self):
        outcome = self.check("file_exists", {"path": "nope.md"})
        self.assertFalse(outcome["pass"])

    def test_file_missing(self):
        outcome = self.check("file_missing", {"path": "nope.md"})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_missing", {"path": "report.md"})
        self.assertFalse(outcome["pass"])

    def test_file_contains(self):
        outcome = self.check("file_contains", {"path": "report.md", "value": "结论：已完成"})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_contains", {"path": "report.md", "value": "不存在"})
        self.assertFalse(outcome["pass"])
        self.assertIn("missing", outcome["note"])

    def test_file_contains_list(self):
        outcome = self.check("file_contains", {"path": "report.md", "value": ["标题", "结论"]})
        self.assertTrue(outcome["pass"])

    def test_file_not_contains(self):
        outcome = self.check("file_not_contains", {"path": "report.md", "value": "TODO"})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_not_contains", {"path": "report.md", "value": "结论"})
        self.assertFalse(outcome["pass"])

    def test_file_regex(self):
        outcome = self.check("file_regex", {"path": "report.md", "value": r"结论：.+"})
        self.assertTrue(outcome["pass"])

    def test_file_lines_min(self):
        outcome = self.check("file_lines_min", {"path": "report.md", "value": 2})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_lines_min", {"path": "report.md", "value": 99})
        self.assertFalse(outcome["pass"])

    def test_file_bytes(self):
        outcome = self.check("file_min_bytes", {"path": "report.md", "value": 5})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_max_bytes", {"path": "report.md", "value": 5})
        self.assertFalse(outcome["pass"])

    def test_file_sha256(self):
        digest = hashlib.sha256(self.target.read_bytes()).hexdigest()
        outcome = self.check("file_sha256", {"path": "report.md", "value": digest})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_sha256", {"path": "report.md", "value": "0" * 64})
        self.assertFalse(outcome["pass"])

    def test_file_mtime_newer(self):
        outcome = self.check("file_mtime_newer", {"path": "report.md", "value": "2000-01-01T00:00:00Z"})
        self.assertTrue(outcome["pass"])
        outcome = self.check("file_mtime_newer", {"path": "report.md", "value": "2999-01-01T00:00:00Z"})
        self.assertFalse(outcome["pass"])

    def test_missing_file_is_a_failure_not_an_error(self):
        outcome = self.check("file_contains", {"path": "gone.md", "value": "x"})
        self.assertFalse(outcome["pass"])
        self.assertIn("not found", outcome["note"])

    def test_escaping_path_is_unjudged(self):
        outcome = self.check("file_exists", {"path": "../escape.md"})
        self.assertIsNone(outcome["pass"])
        self.assertEqual(outcome["source"], "invalid")


class FileCheckProblemTests(unittest.TestCase):
    def test_missing_value_is_a_problem(self):
        self.assertTrue(file_check_problem("file_contains", {"path": "a.md"}))
        self.assertTrue(file_check_problem("file_regex", {"path": "a.md"}))
        self.assertTrue(file_check_problem("file_lines_min", {"path": "a.md"}))

    def test_missing_path_is_a_problem(self):
        self.assertTrue(file_check_problem("file_exists", ""))

    def test_bad_number_is_a_problem(self):
        self.assertTrue(file_check_problem("file_lines_min", {"path": "a.md", "value": "abc"}))

    def test_bad_regex_is_a_problem(self):
        self.assertTrue(file_check_problem("file_regex", {"path": "a.md", "value": "([unclosed"}))

    def test_bad_sha_is_a_problem(self):
        self.assertTrue(file_check_problem("file_sha256", {"path": "a.md", "value": "abc"}))

    def test_bad_timestamp_is_a_problem(self):
        self.assertTrue(file_check_problem("file_mtime_newer", {"path": "a.md", "value": "not-a-date"}))

    def test_good_args_have_no_problem(self):
        self.assertEqual(file_check_problem("file_exists", {"path": "a.md"}), "")
        self.assertEqual(file_check_problem("file_contains", {"path": "a.md", "value": "x"}), "")


class ShorthandTests(unittest.TestCase):
    def test_file_shorthand_is_parsed_as_a_check(self):
        parsed = parse_criterion_shorthand("file_contains:out/report.md::结论")
        self.assertEqual(parsed["check"]["type"], "file_contains")
        self.assertEqual(parsed["check"]["arg"]["path"], "out/report.md")
        self.assertEqual(parsed["check"]["arg"]["value"], "结论")

    def test_file_exists_shorthand_has_no_value(self):
        parsed = parse_criterion_shorthand("file_exists:out/report.md")
        self.assertEqual(parsed["check"]["type"], "file_exists")
        self.assertEqual(parsed["check"]["arg"]["path"], "out/report.md")

    def test_numeric_value_is_coerced(self):
        parsed = parse_criterion_shorthand("file_lines_min:out/report.md::12")
        self.assertEqual(parsed["check"]["arg"]["value"], 12)

    def test_unknown_type_stays_a_description(self):
        parsed = parse_criterion_shorthand("whatever:xyz")
        self.assertNotIn("check", parsed)


class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_validate_accepts_a_valid_file_check(self):
        crits = [Criterion.from_raw({"id": "f1", "desc": "文件存在", "check": {"type": "file_exists", "arg": {"path": "a.md"}}}, 0)]
        self.assertEqual(validate_criteria(crits), [])

    def test_validate_rejects_a_broken_file_check(self):
        crits = [Criterion.from_raw({"id": "f1", "desc": "文件含某词", "check": {"type": "file_contains", "arg": {"path": "a.md"}}}, 0)]
        problems = validate_criteria(crits)
        self.assertEqual(len(problems), 1)
        self.assertIn("f1", problems[0])

    def test_evaluate_check_uses_the_configured_workspace(self):
        (self.root / "a.md").write_text("hello", encoding="utf-8")
        crit = Criterion.from_raw({"id": "f1", "desc": "d", "check": {"type": "file_contains", "arg": {"path": "a.md", "value": "hello"}}}, 0)
        outcome = evaluate_check(crit, "irrelevant artifact", {"workspace": str(self.root)})
        self.assertTrue(outcome["pass"])
        self.assertEqual(outcome["source"], "file")

    def test_evaluate_check_reports_a_missing_file(self):
        crit = Criterion.from_raw({"id": "f1", "desc": "d", "check": {"type": "file_exists", "arg": {"path": "a.md"}}}, 0)
        outcome = evaluate_check(crit, "", {"workspace": str(self.root)})
        self.assertFalse(outcome["pass"])

    def test_artifact_text_is_not_used_for_file_checks(self):
        # The artifact says the file is fine; the file does not exist.
        # The check must follow the disk, not the prose.
        crit = Criterion.from_raw({"id": "f1", "desc": "d", "check": {"type": "file_exists", "arg": {"path": "a.md"}}}, 0)
        outcome = evaluate_check(crit, "已完成，文件 a.md 已写入并通过检查", {"workspace": str(self.root)})
        self.assertFalse(outcome["pass"])
        self.assertIn("not found", outcome["note"])

    def test_every_file_check_type_is_known(self):
        self.assertEqual(len(FILE_CHECK_TYPES), 10)


if __name__ == "__main__":
    unittest.main()

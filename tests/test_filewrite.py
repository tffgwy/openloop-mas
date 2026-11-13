"""Tests for the write-file protocol and the gateway text sanitizer (v0.7.15)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mas.filewrite import (
    MAX_WRITE_BYTES,
    apply_write_blocks,
    parse_write_blocks,
    write_instruction,
)
from mas.providers import sanitize_gateway_text

OPEN = '<<<MAS:FILE path="notes/finding.txt">>>'
CLOSE = "<<<MAS:END FILE>>>"


def _block(path: str, content: str, close: str = CLOSE) -> str:
    return f'<<<MAS:FILE path="{path}">>>\n{content}\n{close}\n'


class ParseWriteBlockTests(unittest.TestCase):
    def test_parses_one_block(self):
        artifact, writes = parse_write_blocks("before\n" + _block("a.txt", "hello") + "after")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["path"], "a.txt")
        self.assertEqual(writes[0]["content"], "hello")
        self.assertNotIn("MAS:FILE", artifact)
        self.assertIn("before", artifact)
        self.assertIn("after", artifact)

    def test_parses_multiple_blocks(self):
        text = _block("a.txt", "one") + _block("b/c.txt", "two")
        artifact, writes = parse_write_blocks(text)
        self.assertEqual([w["path"] for w in writes], ["a.txt", "b/c.txt"])
        self.assertEqual(writes[1]["content"], "two")
        self.assertNotIn("MAS:FILE", artifact)

    def test_empty_text(self):
        _artifact, writes = parse_write_blocks("")
        self.assertEqual(writes, [])

    def test_text_without_blocks(self):
        artifact, writes = parse_write_blocks("just prose")
        self.assertEqual(writes, [])
        self.assertEqual(artifact, "just prose")

    def test_unterminated_block_is_an_error(self):
        text = '<<<MAS:FILE path="a.txt">>>\nhello\n'
        artifact, writes = parse_write_blocks(text)
        self.assertEqual(writes, [])
        self.assertEqual(artifact, text)

    def test_block_without_path_is_an_error(self):
        text = '<<<MAS:FILE path="">>>\nhello\n' + CLOSE + "\n"
        artifact, writes = parse_write_blocks(text)
        self.assertEqual(writes, [])
        self.assertEqual(artifact, text)

    def test_single_quoted_path(self):
        _artifact, writes = parse_write_blocks("<<<MAS:FILE path='a.txt'>>>\nhi\n" + CLOSE + "\n")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["path"], "a.txt")

    def test_unquoted_path_is_not_supported(self):
        # The protocol asks for quotes; an unquoted path must not be
        # silently mis-parsed into the body.
        _artifact, writes = parse_write_blocks("<<<MAS:FILE path=a.txt>>>\nhi\n" + CLOSE + "\n")
        self.assertEqual(writes, [])

    def test_four_closing_markers_are_accepted(self):
        _artifact, writes = parse_write_blocks(_block("a.txt", "hello", "<<<MAS:END FILE>>>>"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["content"], "hello")

    def test_regex_ish_closing_marker_is_accepted(self):
        # An earlier version of the instruction told the model to write the
        # literal regex tail; both spellings must parse.
        _artifact, writes = parse_write_blocks(_block("a.txt", "hello", "<<<MAS:END FILE>{3,}"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["content"], "hello")

    def test_content_is_written_verbatim(self):
        body = "line one\n\n  indented\n\ttabbed"
        _artifact, writes = parse_write_blocks(_block("a.txt", body))
        self.assertEqual(writes[0]["content"], body)

    def test_content_keeps_inner_markers(self):
        body = "before <<<MAS:END FILE not the end\nafter"
        _artifact, writes = parse_write_blocks(_block("a.txt", body))
        # The first closing marker wins - the rest stays in the content.
        self.assertEqual(writes[0]["content"], "before <<<MAS:END FILE not the end\nafter")

    def test_no_stray_leading_gt(self):
        _artifact, writes = parse_write_blocks(_block("a.txt", "hello"))
        self.assertFalse(writes[0]["content"].startswith(">"))
        self.assertEqual(writes[0]["content"], "hello")


class ApplyWriteBlockTests(unittest.TestCase):
    def test_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_write_blocks(_block("notes/finding.txt", "MAS-FILECHECK-LIVE-TOKEN"), root)
            self.assertEqual(result["errors"], [])
            target = root / "notes" / "finding.txt"
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "MAS-FILECHECK-LIVE-TOKEN")
            self.assertEqual(len(result["written"]), 1)
            self.assertTrue(result["written"][0]["written"])
            self.assertTrue(result["written"][0]["created"])

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_write_blocks(_block("a/b/c/deep.txt", "x"), root)
            self.assertTrue((root / "a" / "b" / "c" / "deep.txt").exists())

    def test_artifact_keeps_the_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_write_blocks("结论如下。\n" + _block("a.txt", "x"), root)
            self.assertIn("结论如下。", result["artifact"])
            self.assertNotIn("MAS:FILE", result["artifact"])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_write_blocks(_block("a.txt", "x"), root, dry_run=True)
            self.assertFalse((root / "a.txt").exists())
            self.assertEqual(len(result["written"]), 1)
            self.assertFalse(result["written"][0]["written"])

    def test_parent_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_write_blocks(_block("../escape.txt", "x"), root)
            self.assertEqual(result["written"], [])
            self.assertTrue(result["errors"])
            self.assertIn("outside the workspace", result["errors"][0])
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_absolute_path_outside_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_write_blocks(_block(str(Path(tmp).parent / "nope.txt"), "x"), root)
            self.assertTrue(result["errors"])

    def test_absolute_path_inside_workspace_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "inside.txt"
            result = apply_write_blocks(_block(str(target), "x"), root)
            self.assertEqual(result["errors"], [])
            self.assertTrue(target.exists())

    def test_oversized_content_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            huge = "a" * (MAX_WRITE_BYTES + 10)
            result = apply_write_blocks(_block("big.txt", huge), root)
            self.assertTrue(result["errors"])
            self.assertFalse((root / "big.txt").exists())

    def test_one_bad_path_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = _block("../escape.txt", "x") + _block("good.txt", "y")
            result = apply_write_blocks(text, root)
            self.assertTrue(result["errors"])
            self.assertTrue((root / "good.txt").exists())


class WriteInstructionTests(unittest.TestCase):
    def test_lists_the_paths(self):
        instr = write_instruction(["a.txt", "b.txt"])
        self.assertIn("a.txt", instr)
        self.assertIn("b.txt", instr)

    def test_empty_paths_gives_empty_instruction(self):
        self.assertEqual(write_instruction([]), "")

    def test_instruction_is_parseable_by_our_own_protocol(self):
        # The instruction must not contain a marker spelling that our own
        # parser rejects - that was a real bug.
        instr = write_instruction(["notes/finding.txt"])
        _artifact, writes = parse_write_blocks(instr + _block("notes/finding.txt", "x"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["path"], "notes/finding.txt")

    def test_template_block_in_the_instruction_is_not_a_write_request(self):
        # The instruction embeds a template. A model that copies it
        # verbatim must not make the engine write a placeholder file.
        instr = write_instruction(["notes/finding.txt"])
        _artifact, writes = parse_write_blocks(instr)
        self.assertEqual(writes, [])

    def test_placeholder_path_is_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_write_blocks(_block("\u76f8\u5bf9\u8def\u5f84", "template"), root)
            self.assertEqual(result["written"], [])
            self.assertFalse((root / "\u76f8\u5bf9\u8def\u5f84").exists())
            self.assertEqual(len(result["skipped"]), 1)

    def test_instruction_shows_a_literal_closing_marker(self):
        instr = write_instruction(["a.txt"])
        self.assertIn(CLOSE, instr)
        self.assertNotIn(">{3,}", instr)


class GatewaySanitizerTests(unittest.TestCase):
    def test_removes_control_markers(self):
        raw = "结论如下。]<]claude[>[<tool_call> | ]<]claude[>[No tool invocation syntax generated?"
        cleaned = sanitize_gateway_text(raw)
        self.assertNotIn("claude", cleaned)
        self.assertNotIn("tool_call", cleaned)
        self.assertIn("结论如下。", cleaned)

    def test_removes_empty_tool_call_block(self):
        raw = "answer<tool_call></tool_call>tail"
        cleaned = sanitize_gateway_text(raw)
        self.assertNotIn("tool_call", cleaned)

    def test_removes_stray_open_tool_call(self):
        raw = "answer < tool_call > tail"
        cleaned = sanitize_gateway_text(raw)
        self.assertNotIn("tool_call", cleaned)

    def test_keeps_clean_text_unchanged(self):
        self.assertEqual(sanitize_gateway_text("干净的答案"), "干净的答案")

    def test_empty_text(self):
        self.assertEqual(sanitize_gateway_text(""), "")

    def test_does_not_destroy_normal_brackets(self):
        raw = "数组 a[0] 与 b[1] 的取值"
        self.assertEqual(sanitize_gateway_text(raw), raw)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""v0.7.70 tests for ``mas.content`` - reading what is inside a file.

The content module is what makes the content checks possible: it names
the format of a file and pulls the readable text out of it, with the
standard library only. These tests pin the behaviour down - especially
the parts that decide whether a criterion may be closed.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import pathlib
import struct
import tempfile
import unittest
import zipfile
import zlib

from mas.content import (
    MAX_BYTES,
    describe_file,
    detect_format,
    extract_text,
    human_size,
)


def _pdf_bytes(body: str) -> bytes:
    """A PDF whose text lives in a *compressed* stream."""
    content = ("BT (\\(%s\\)) Tj ET" % body).encode("utf-8")
    compressed = zlib.compress(content)
    pieces = [
        b"%PDF-1.4\n",
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Page/Parent 1 0 R/MediaBox[0 0 200 200]>>endobj\n",
        b"3 0 obj<</Length %d/Filter/FlateDecode>>stream\n" % len(compressed),
        compressed,
        b"\nendstream endobj\n",
        b"trailer<</Root 1 0 R>>\n",
        b"%%EOF\n",
    ]
    return b"".join(pieces)


def _png_bytes(width: int = 4, height: int = 3) -> bytes:
    head = b"\x89PNG\r\n\x1a\n"
    head += struct.pack(">I", 13)
    head += b"IHDR"
    head += struct.pack(">II", width, height)
    head += b"\x08\x06\x00\x00\x00"
    head += b"\x00" * 4
    return head


def _docx_bytes(body: str) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:t>%s</w:t></w:p></w:body></w:document>" % body,
        )
    return buffer.getvalue()


def _xlsx_bytes(body: str) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            "<sst><si><t>%s</t></si></sst>" % body,
        )
    return buffer.getvalue()


def _pptx_bytes(body: str) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld><p:cSld><a:p><a:t>%s</a:t></a:p></p:cSld></p:sld>" % body,
        )
    return buffer.getvalue()


class TestDetectFormat(unittest.TestCase):
    """The magic bytes decide; the extension only settles what they cannot."""

    def test_a_pdf_is_a_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.pdf"
            path.write_bytes(_pdf_bytes("hello"))
            self.assertEqual(detect_format(path), "pdf")

    def test_the_magic_bytes_beat_the_extension(self):
        """A .txt that is really a PDF is a PDF."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.txt"
            path.write_bytes(_pdf_bytes("hello"))
            self.assertEqual(detect_format(path), "pdf")

    def test_an_image_is_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.png"
            path.write_bytes(_png_bytes())
            self.assertEqual(detect_format(path), "image")

    def test_the_office_formats_are_told_apart(self):
        cases = {
            "a.docx": _docx_bytes("x"),
            "a.xlsx": _xlsx_bytes("x"),
            "a.pptx": _pptx_bytes("x"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in cases.items():
                path = pathlib.Path(tmp) / name
                path.write_bytes(data)
                self.assertEqual(
                    detect_format(path), name.split(".")[1], name
                )

    def test_a_plain_archive_is_a_zip(self):
        import io

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "hello")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.zip"
            path.write_bytes(buffer.getvalue())
            self.assertEqual(detect_format(path), "zip")

    def test_a_missing_file_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_format(pathlib.Path(tmp) / "nope.bin"), "binary")

    def test_a_text_file_is_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.md"
            path.write_text("hello\n", encoding="utf-8")
            self.assertEqual(detect_format(path), "markdown")


class TestExtractText(unittest.TestCase):
    """The text of a file, whatever the format."""

    def test_the_words_of_a_compressed_pdf_are_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.pdf"
            path.write_bytes(_pdf_bytes("Conclusion 42"))
            extracted = extract_text(path)
            self.assertTrue(extracted["ok"], extracted["reason"])
            self.assertEqual(extracted["format"], "pdf")
            self.assertIn("Conclusion", extracted["text"])

    def test_a_word_document_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.docx"
            path.write_bytes(_docx_bytes("Memo 42"))
            extracted = extract_text(path)
            self.assertTrue(extracted["ok"], extracted["reason"])
            self.assertEqual(extracted["format"], "docx")
            self.assertIn("Memo", extracted["text"])

    def test_a_workbook_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.xlsx"
            path.write_bytes(_xlsx_bytes("Sheet 42"))
            extracted = extract_text(path)
            self.assertTrue(extracted["ok"], extracted["reason"])
            self.assertEqual(extracted["format"], "xlsx")
            self.assertIn("Sheet", extracted["text"])

    def test_a_slide_deck_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.pptx"
            path.write_bytes(_pptx_bytes("Slide 42"))
            extracted = extract_text(path)
            self.assertTrue(extracted["ok"], extracted["reason"])
            self.assertEqual(extracted["format"], "pptx")
            self.assertIn("Slide", extracted["text"])

    def test_an_image_has_no_text_but_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.png"
            path.write_bytes(_png_bytes())
            extracted = extract_text(path)
            self.assertTrue(extracted["ok"])
            self.assertEqual(extracted["format"], "image")
            self.assertEqual(extracted["text"], "")

    def test_a_binary_file_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.bin"
            path.write_bytes(bytes(range(256)) * 4)
            extracted = extract_text(path)
            self.assertFalse(extracted["ok"])
            self.assertIn("binary", extracted["reason"])

    def test_a_plain_text_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.txt"
            path.write_text("hello there\n", encoding="utf-8")
            extracted = extract_text(path)
            self.assertTrue(extracted["ok"])
            self.assertIn("hello", extracted["text"])

    def test_a_missing_file_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            extracted = extract_text(pathlib.Path(tmp) / "nope.pdf")
            self.assertFalse(extracted["ok"])
            self.assertTrue(extracted["reason"])

    def test_a_too_large_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "big.txt"
            with open(path, "wb") as handle:
                handle.write(b"x" * (MAX_BYTES + 1))
            extracted = extract_text(path)
            self.assertFalse(extracted["ok"])
            self.assertIn("too large", extracted["reason"])


class TestDescribeFile(unittest.TestCase):
    """The fact sheet a person can read."""

    def test_a_pdf_reports_its_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.pdf"
            path.write_bytes(_pdf_bytes("Conclusion"))
            facts = describe_file(path)
            self.assertEqual(facts["format"], "pdf")
            self.assertEqual(facts["pages"], 1)
            self.assertGreater(facts["text_chars"], 0)
            self.assertTrue(facts["readable"])
            self.assertEqual(facts["name"], "a.pdf")

    def test_an_image_reports_its_size_in_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.png"
            path.write_bytes(_png_bytes(width=12, height=7))
            facts = describe_file(path)
            self.assertEqual(facts["format"], "image")
            self.assertEqual(facts["width"], 12)
            self.assertEqual(facts["height"], 7)

    def test_an_archive_reports_its_members(self):
        import io

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "hello")
            archive.writestr("b.txt", "world")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.zip"
            path.write_bytes(buffer.getvalue())
            facts = describe_file(path)
            self.assertEqual(facts["members"], 2)

    def test_the_size_is_human_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.txt"
            path.write_text("x" * 2048, encoding="utf-8")
            facts = describe_file(path)
            self.assertEqual(facts["size"], "2.0 KB")


class TestHumanSize(unittest.TestCase):
    """A short, human readable size."""

    def test_the_units(self):
        self.assertEqual(human_size(0), "0.0 B")
        self.assertEqual(human_size(512), "512.0 B")
        self.assertEqual(human_size(2048), "2.0 KB")
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(human_size(3 * 1024 ** 3), "3.0 GB")


class TestContentChecks(unittest.TestCase):
    """The content checks themselves."""

    def _root(self, tmp):
        root = pathlib.Path(tmp)
        (root / "a.pdf").write_bytes(_pdf_bytes("Conclusion 42"))
        (root / "a.docx").write_bytes(_docx_bytes("Memo 42"))
        (root / "a.txt").write_text("line one\nline two\n", encoding="utf-8")
        return root

    def test_content_contains(self):
        from mas.filechecks import evaluate_file_check

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertTrue(
                evaluate_file_check("content_contains", "a.pdf::Conclusion",
                                    root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_contains", "a.pdf::Verdict",
                                    root, None)["pass"]
            )

    def test_content_not_contains(self):
        from mas.filechecks import evaluate_file_check

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertTrue(
                evaluate_file_check("content_not_contains", "a.pdf::Verdict",
                                    root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_not_contains", "a.pdf::Conclusion",
                                    root, None)["pass"]
            )

    def test_content_regex(self):
        from mas.filechecks import evaluate_file_check

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertTrue(
                evaluate_file_check("content_regex", "a.pdf::Conclusion \\d+",
                                    root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_regex", "a.pdf::Verdict \\d+",
                                    root, None)["pass"]
            )

    def test_content_lines_min(self):
        from mas.filechecks import evaluate_file_check

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertTrue(
                evaluate_file_check("content_lines_min", "a.txt::2", root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_lines_min", "a.txt::5", root, None)["pass"]
            )

    def test_content_min_chars(self):
        from mas.filechecks import evaluate_file_check

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertTrue(
                evaluate_file_check("content_min_chars", "a.pdf::5", root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_min_chars", "a.pdf::5000", root, None)["pass"]
            )

    def test_content_format_is(self):
        from mas.filechecks import evaluate_file_check

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertTrue(
                evaluate_file_check("content_format_is", "a.pdf::pdf", root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_format_is", "a.docx::pdf", root, None)["pass"]
            )

    def test_the_checks_are_registered(self):
        from mas.filechecks import FILE_CHECK_TYPES

        for kind in ("content_contains", "content_not_contains", "content_regex",
                     "content_lines_min", "content_min_chars", "content_format_is"):
            self.assertIn(kind, FILE_CHECK_TYPES)

    def test_a_broken_argument_is_reported_as_broken(self):
        from mas.filechecks import file_check_problem

        self.assertTrue(file_check_problem("content_regex", "a.pdf::[unclosed"))
        self.assertTrue(file_check_problem("content_lines_min", "a.pdf::many"))
        self.assertTrue(file_check_problem("content_contains", ""))

    def test_they_are_described_for_the_user(self):
        from mas.filechecks import describe_file_checks

        rows = {row["type"]: row for row in describe_file_checks()}
        self.assertIn("content_contains", rows)
        self.assertIn("content_format_is", rows)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

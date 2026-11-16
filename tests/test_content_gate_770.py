# -*- coding: utf-8 -*-
"""v0.7.70 regression tests for the twelfth gate: ``content_check``.

The eleventh gate watched the *human path*. Nothing watched the
*content* of a deliverable: every machine check looked at the bytes
of a file, which is blind for every format whose payload is not plain
text. A PDF keeps its text inside a compressed stream, a .docx is a
zip file - so a task whose deliverable was a PDF could not be judged
by a machine at all.

These tests pin the twelfth gate down: it has to exist, it has to be
listed among the checks, it has to pass on a clean build, and it has
to fail when the content path breaks.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import pathlib
import tempfile
import unittest

from mas import selftest as st
from mas.content import detect_format, extract_text
from mas.filechecks import evaluate_file_check


def _write_pdf(path, body: str) -> None:
    """A PDF whose text lives in a *compressed* stream.

    The words are not in the bytes of the file, so a byte-level check
    cannot see them - which is the whole point of the gate.
    """
    import zlib

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
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"".join(pieces))


def _write_docx(path, body: str) -> None:
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:t>%s</w:t></w:p></w:body></w:document>" % body,
        )


class TestContentCheckGateExists(unittest.TestCase):
    """The gate has to be there, and it has to be reachable."""

    def test_the_twelfth_gate_is_registered(self):
        names = [st._check_name(check) for check in st.CHECKS]
        self.assertIn("content_check", names)

    def test_the_gate_has_its_own_short_name(self):
        check = st.check_a_content_check_reads_inside_the_file
        self.assertEqual(st._check_name(check), "content_check")

    def test_the_gate_passes_on_a_clean_build(self):
        row = st.check_a_content_check_reads_inside_the_file(iterations=1)
        self.assertTrue(row["ok"], row.get("failures"))
        self.assertEqual(row["failures"], [])

    def test_the_gate_states_its_invariant(self):
        row = st.check_a_content_check_reads_inside_the_file(iterations=1)
        self.assertTrue(row.get("detail"))
        self.assertTrue(row.get("invariant"))
        self.assertEqual(row["name"], "content_check")

    def test_run_selftest_can_select_it_by_name(self):
        result = st.run_selftest(iterations=1, only=["content_check"])
        self.assertTrue(result["ok"], result.get("failed"))
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["name"], "content_check")

    def test_the_gate_is_one_of_the_thirteen(self):
        names = [st._check_name(check) for check in st.CHECKS]
        self.assertGreaterEqual(len(names), 12)


class TestContentCheckGateCatchesBreakage(unittest.TestCase):
    """The gate has to fail when the content path breaks."""

    def test_the_gate_fails_when_the_content_cannot_be_read(self):
        """A file whose content nobody can read must not pass."""
        import mas.filechecks as filechecks

        real = filechecks.extract_text

        def _blind(path):
            return {"text": "", "format": "binary", "ok": False,
                    "reason": "mutated: nothing can be read",
                    "bytes": 0}

        filechecks.extract_text = _blind
        try:
            row = st.check_a_content_check_reads_inside_the_file(iterations=1)
        finally:
            filechecks.extract_text = real
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_the_format_is_always_right(self):
        """A format check that never fails judges nothing."""
        import mas.filechecks as filechecks

        real = filechecks.detect_format

        def _always_pdf(path):
            return "pdf"

        filechecks.detect_format = _always_pdf
        try:
            row = st.check_a_content_check_reads_inside_the_file(iterations=1)
        finally:
            filechecks.detect_format = real
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_the_overlay_is_ignored(self):
        """A file the loop would write has to be judged as it will be.

        The file is not on the disk at all, so an overlay that is
        ignored leaves the check with nothing to read - and the words
        the loop would have written are lost with it.
        """
        import mas.filechecks as filechecks

        real = filechecks._extract_from_bytes

        def _read_the_old_file(path, data):
            return {"text": "", "format": "pdf", "ok": True,
                    "reason": "", "bytes": len(data)}

        filechecks._extract_from_bytes = _read_the_old_file
        try:
            row = st.check_a_content_check_reads_inside_the_file(iterations=1)
        finally:
            filechecks._extract_from_bytes = real
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])

    def test_the_gate_fails_when_an_unreadable_file_is_unjudged(self):
        """An unjudged check goes back to the model - and the model may
        say "looks fine". That closes a criterion nobody checked."""
        import mas.filechecks as filechecks

        real = filechecks.extract_text

        def _blind(path):
            return {"text": "", "format": "binary", "ok": False,
                    "reason": "mutated: nothing can be read",
                    "bytes": 0}

        filechecks.extract_text = _blind
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                (root / "blob.bin").write_bytes(bytes(range(256)) * 8)
                row = evaluate_file_check(
                    "content_contains", "blob.bin::anything", root, None
                )
        finally:
            filechecks.extract_text = real
        self.assertIs(row["pass"], False)
        self.assertEqual(row["source"], "content")


class TestContentCheckInvariants(unittest.TestCase):
    """The invariants the twelfth gate guards, checked one by one."""

    def test_the_content_check_sees_the_words_of_a_compressed_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_pdf(root / "report.pdf", "Conclusion 42")
            row = evaluate_file_check(
                "content_contains", "report.pdf::Conclusion", root, None
            )
            self.assertTrue(row["pass"], row.get("note"))
            self.assertEqual(row["source"], "content")

    def test_the_byte_level_check_cannot_see_those_words(self):
        """The test has to prove something: the bytes must not hold it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_pdf(root / "report.pdf", "Conclusion 42")
            row = evaluate_file_check(
                "file_contains", "report.pdf::Conclusion", root, None
            )
            self.assertFalse(row["pass"])

    def test_the_content_check_reads_a_word_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_docx(root / "memo.docx", "Memo 42")
            row = evaluate_file_check(
                "content_contains", "memo.docx::Memo", root, None
            )
            self.assertTrue(row["pass"], row.get("note"))

    def test_the_format_check_names_the_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_pdf(root / "report.pdf", "Conclusion 42")
            _write_docx(root / "memo.docx", "Memo 42")
            self.assertTrue(
                evaluate_file_check("content_format_is", "report.pdf::pdf",
                                    root, None)["pass"]
            )
            self.assertFalse(
                evaluate_file_check("content_format_is", "memo.docx::pdf",
                                    root, None)["pass"]
            )

    def test_a_missing_file_fails_rather_than_passing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            row = evaluate_file_check(
                "content_contains", "nothing.pdf::Conclusion", root, None
            )
            self.assertIs(row["pass"], False)

    def test_the_overlay_file_is_judged_by_what_would_be_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / "fresh.pdf"
            overlay = {str(target): b"%PDF-1.4\nnot really a pdf"}
            row = evaluate_file_check(
                "content_contains", "fresh.pdf::Conclusion", root, overlay
            )
            self.assertIs(row["pass"], False)

    def test_extraction_reports_the_format_it_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_docx(root / "memo.docx", "Memo 42")
            extracted = extract_text(root / "memo.docx")
            self.assertEqual(extracted["format"], "docx")
            self.assertTrue(extracted["ok"])
            self.assertIn("Memo", extracted["text"])
            self.assertEqual(detect_format(root / "memo.docx"), "docx")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

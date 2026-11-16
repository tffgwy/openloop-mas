# -*- coding: utf-8 -*-
"""The resource index of v0.7.71: one folder, every file, every fact.

The index is the folder-level answer. It must list *every* file - the
ones no criterion mentions are still part of the work - and it must
read the facts from the file's own header.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def png_bytes(width=12, height=7):
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(
        b"\x00" + bytes([(x * 7 + y * 3) % 256 for x in range(width * 3)])
        for y in range(height)
    )
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def wav_bytes(rate=44100, channels=2, frames=66150):
    import struct

    samples = b"".join(struct.pack("<h", (i * 37) % 30000 - 15000)
                     for i in range(frames * channels))
    header = (b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                             rate * channels * 2, channels * 2, 16)
              + b"data" + struct.pack("<I", len(samples)))
    return header + samples


def docx_bytes(body="Memo 42"):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:t>%s</w:t></w:p></w:body></w:document>" % body,
        )
    return buffer.getvalue()


def pdf_bytes(body="Conclusion 42"):
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
    return b"".join(pieces)


def make_folder(tmp):
    root = Path(tmp)
    (root / "report.pdf").write_bytes(pdf_bytes())
    (root / "memo.docx").write_bytes(docx_bytes())
    (root / "shot.png").write_bytes(png_bytes())
    (root / "liar.txt").write_bytes(png_bytes(4, 4))
    (root / "clip.wav").write_bytes(wav_bytes())
    (root / "model.obj").write_text("v 0 0 0\nv 1 0 0\nf 1 2 3\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(bytes(range(256)) * 4)
    support = root / "support"
    support.mkdir(exist_ok=True)
    (support / "notes.txt").write_text("some notes", encoding="utf-8")
    return root


class DescribeFolderTest(unittest.TestCase):
    def test_it_lists_every_file(self):
        from mas.resources import describe_folder

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        names = {str(row.get("relative")) for row in index["files"]}
        self.assertIn("report.pdf", names)
        self.assertIn("memo.docx", names)
        self.assertIn("shot.png", names)
        self.assertIn("clip.wav", names)
        self.assertIn("model.obj", names)
        self.assertIn("blob.bin", names)
        self.assertEqual(index["count"], len(index["files"]))

    def test_it_lists_a_file_nobody_checks(self):
        from mas.resources import describe_folder

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        names = {str(row.get("relative")) for row in index["files"]}
        self.assertIn("support/notes.txt", names)

    def test_the_relative_path_uses_forward_slashes(self):
        from mas.resources import describe_folder

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        names = {str(row.get("relative")) for row in index["files"]}
        self.assertIn("support/notes.txt", names)

    def test_the_format_comes_from_the_header_not_the_name(self):
        from mas.resources import describe_folder

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        rows = {str(row.get("relative")): row for row in index["files"]}
        self.assertEqual(rows["liar.txt"].get("format"), "image")

    def test_a_missing_folder_is_not_an_error(self):
        from mas.resources import describe_folder

        with TemporaryDirectory() as tmp:
            index = describe_folder(Path(tmp) / "nope")
        self.assertFalse(index["exists"])
        self.assertEqual(index["count"], 0)

    def test_the_counts_add_up(self):
        from mas.resources import describe_folder

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        total = sum(int(row.get("bytes") or 0) for row in index["files"])
        self.assertEqual(index["bytes"], total)
        self.assertEqual(sum(index["formats"].values()), index["count"])


class FormatSummaryTest(unittest.TestCase):
    def test_it_groups_by_format(self):
        from mas.resources import describe_folder, format_summary

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        summary = format_summary(index)
        formats = {str(row["format"]) for row in summary}
        self.assertIn("image", formats)
        self.assertIn("pdf", formats)

    def test_every_format_of_the_index_appears_once(self):
        from mas.resources import describe_folder, format_summary

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        summary = format_summary(index)
        self.assertEqual(len(summary), len(index["formats"]))


class FactsTest(unittest.TestCase):
    def test_it_reads_the_facts_of_a_pdf(self):
        from mas.resources import describe_folder, facts_for

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        rows = {str(row.get("relative")): row for row in index["files"]}
        facts = facts_for(rows["report.pdf"])
        self.assertTrue(any("pages" in fact for fact in facts))

    def test_it_reads_the_duration_of_a_recording(self):
        from mas.resources import describe_folder, facts_for

        with TemporaryDirectory() as tmp:
            index = describe_folder(make_folder(tmp))
        rows = {str(row.get("relative")): row for row in index["files"]}
        facts = facts_for(rows["clip.wav"])
        self.assertTrue(any(fact.strip()[-1] == "s" for fact in facts))

    def test_a_file_without_facts_yields_nothing(self):
        from mas.resources import facts_for

        self.assertEqual(facts_for({"format": "binary"}), [])


class PreviewFileTest(unittest.TestCase):
    def test_it_shows_the_words_of_a_pdf(self):
        from mas.resources import preview_file

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.pdf"
            path.write_bytes(pdf_bytes("Conclusion 42"))
            preview = preview_file(path)
        self.assertIn("Conclusion", str(preview.get("text") or ""))

    def test_it_shows_the_words_of_a_word_document(self):
        from mas.resources import preview_file

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.docx"
            path.write_bytes(docx_bytes("Memo 42"))
            preview = preview_file(path)
        self.assertIn("Memo", str(preview.get("text") or ""))

    def test_an_image_has_no_body_but_says_so(self):
        from mas.resources import preview_file

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png_bytes())
            preview = preview_file(path)
        self.assertEqual(preview.get("format"), "image")
        self.assertEqual(preview.get("text"), "")
        self.assertTrue(preview.get("facts"))

    def test_a_missing_file_is_reported_not_raised(self):
        from mas.resources import preview_file

        with TemporaryDirectory() as tmp:
            preview = preview_file(Path(tmp) / "nope.pdf")
        self.assertFalse(preview.get("readable"))
        self.assertTrue(preview.get("reason"))

    def test_the_text_is_truncated_at_the_limit(self):
        from mas.resources import preview_file

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.txt"
            path.write_text("x" * 5000, encoding="utf-8")
            preview = preview_file(path, max_chars=100)
        self.assertEqual(len(preview.get("text") or ""), 100)
        self.assertTrue(preview.get("truncated"))


class DetectKindTest(unittest.TestCase):
    def test_it_uses_the_header(self):
        from mas.resources import detect_kind

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "liar.txt"
            path.write_bytes(png_bytes(4, 4))
            kind = detect_kind(path)
        self.assertEqual(kind, "image")


if __name__ == "__main__":
    unittest.main()

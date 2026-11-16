# -*- coding: utf-8 -*-
"""The two resource commands of v0.7.71: `mas files` and `mas preview`.

These are the commands a beginner types first, so their exit codes
and their JSON have to mean exactly one thing each.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]


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
    (root / "shot.png").write_bytes(png_bytes())
    (root / "liar.txt").write_bytes(png_bytes(4, 4))
    (root / "notes.txt").write_text("some notes", encoding="utf-8")
    support = root / "support"
    support.mkdir(exist_ok=True)
    (support / "extra.txt").write_text("more notes", encoding="utf-8")
    return root


class CliRunner(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
        )


class FilesCommandTest(CliRunner):
    def test_it_lists_the_files_of_a_folder(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("files", str(root))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("report.pdf", proc.stdout)
        self.assertIn("shot.png", proc.stdout)
        self.assertIn("support/extra.txt", proc.stdout)

    def test_it_names_the_format_of_every_file(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("files", str(root))
        self.assertIn("pdf", proc.stdout)
        self.assertIn("image", proc.stdout)

    def test_the_json_form_carries_every_file(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("files", str(root), "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["files"], 5)
        names = {str(row.get("relative")) for row in payload["folders"][0]["files"]}
        self.assertIn("report.pdf", names)
        self.assertIn("liar.txt", names)

    def test_the_json_form_reports_the_header_not_the_name(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("files", str(root), "--json")
        payload = json.loads(proc.stdout)
        rows = {str(row.get("relative")): row
                for row in payload["folders"][0]["files"]}
        self.assertEqual(rows["liar.txt"]["format"], "image")

    def test_a_missing_folder_is_not_a_crash(self):
        proc = self._run("files", "/definitely/not/here")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class PreviewCommandTest(CliRunner):
    def test_it_shows_the_words_of_a_pdf(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("preview", str(root / "report.pdf"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Conclusion", proc.stdout)

    def test_the_json_form_carries_the_text(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("preview", str(root / "report.pdf"), "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("Conclusion", str(payload.get("text") or ""))
        self.assertEqual(payload.get("format"), "pdf")

    def test_a_missing_file_exits_with_two(self):
        proc = self._run("preview", "/definitely/not/here.pdf")
        self.assertEqual(proc.returncode, 2)

    def test_a_missing_file_says_why_in_json(self):
        proc = self._run("preview", "/definitely/not/here.pdf", "--json")
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload.get("error"), "file_not_found")

    def test_a_folder_is_refused_with_a_hint(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("preview", str(root))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("mas files", proc.stdout + proc.stderr)

    def test_a_folder_is_refused_in_json(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("preview", str(root), "--json")
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload.get("error"), "not_a_file")

    def test_an_image_says_it_has_no_body(self):
        with TemporaryDirectory() as tmp:
            root = make_folder(tmp)
            proc = self._run("preview", str(root / "shot.png"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("没有可预览的正文", proc.stdout)
        self.assertIn("width", proc.stdout)

    def test_the_text_can_be_cut_short(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "long.txt").write_text("x" * 5000, encoding="utf-8")
            proc = self._run("preview", str(root / "long.txt"), "--max-chars", "100")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertLess(len(proc.stdout), 1000)


if __name__ == "__main__":
    unittest.main()

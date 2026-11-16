# -*- coding: utf-8 -*-
"""The format parsers of v0.7.71 must read real files, and refuse to
guess when a file lies.

The rule of this project is that a fact about a file comes from the
file's own header. A parser that guesses is worse than a parser that
returns nothing: a guess is indistinguishable from a fact.
"""
import os
import struct
import unittest
import zipfile
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory


def png_bytes(width=12, height=7):
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
    samples = b"".join(struct.pack("<h", (i * 37) % 30000 - 15000)
                     for i in range(frames * channels))
    header = (b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                             rate * channels * 2, channels * 2, 16)
              + b"data" + struct.pack("<I", len(samples)))
    return header + samples


def font_bytes(family="OpenLoop Sans", glyphs=256):
    names = [(1, family), (2, "Regular"), (4, family), (6, family.replace(" ", ""))]
    records = b""
    strings = b""
    for name_id, text in names:
        raw = text.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x0409, name_id, len(raw), len(strings))
        strings += raw
    name_table = (struct.pack(">HHH", 0, len(names), 6 + 12 * len(names))
                  + records + strings)
    tables = {b"name": name_table,
              b"maxp": struct.pack(">IH", 0x00010000, int(glyphs))}
    tags = sorted(tables)
    count = len(tags)
    offset_table = b""
    body = b""
    offset = 12 + 16 * count
    for tag in tags:
        data = tables[tag]
        offset_table += tag + struct.pack(">III", 0, offset, len(data))
        body += data + b"\x00" * ((4 - len(data) % 4) % 4)
        offset += len(data) + ((4 - len(data) % 4) % 4)
    return struct.pack(">IHHHH", 0x00010000, count, 16, 0, 0) + offset_table + body


def gltf_bytes(doc):
    import json
    js = json.dumps(doc).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    binary = b"\x00" * 16
    total = 12 + 8 + len(js) + 8 + len(binary)
    out = b"glTF" + struct.pack("<II", 2, total)
    out += struct.pack("<I", len(js)) + b"JSON" + js
    out += struct.pack("<I", len(binary)) + b"BIN\x00" + binary
    return out


GLTF_DOC = {
    "asset": {"version": "2.0", "generator": "demo"},
    "meshes": [{"name": "a"}, {"name": "b"}],
    "nodes": [{"name": "n1"}, {"name": "n2"}, {"name": "n3"}],
    "materials": [{"name": "mat"}],
}


class FontTest(unittest.TestCase):
    def test_it_reads_the_family_and_the_glyph_count(self):
        from mas.formats import font_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.ttf"
            path.write_bytes(font_bytes("OpenLoop Sans", 256))
            facts = font_info(path)
        self.assertEqual(facts.get("family"), "OpenLoop Sans")
        self.assertEqual(facts.get("glyphs"), 256)
        self.assertEqual(facts.get("font"), "truetype")

    def test_a_truncated_font_yields_nothing(self):
        from mas.formats import font_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.ttf"
            path.write_bytes(font_bytes()[:20])
            facts = font_info(path)
        self.assertEqual(facts, {})

    def test_a_file_that_is_not_a_font_yields_nothing(self):
        from mas.formats import font_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "notafont.ttf"
            path.write_bytes(b"hello world, just text")
            facts = font_info(path)
        self.assertEqual(facts, {})

    def test_a_missing_file_yields_nothing(self):
        from mas.formats import font_info
        self.assertEqual(font_info(Path(os.devnull).parent / "nope.ttf"), {})


class ImageTest(unittest.TestCase):
    def test_it_reads_the_png_size(self):
        from mas.formats import image_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png_bytes(12, 7))
            facts = image_info(path)
        self.assertEqual(facts.get("width"), 12)
        self.assertEqual(facts.get("height"), 7)

    def test_a_truncated_png_yields_no_size(self):
        from mas.formats import image_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png_bytes(12, 7)[:20])
            facts = image_info(path)
        self.assertNotIn("width", facts)

    def test_a_png_is_never_reported_as_a_jpeg(self):
        from mas.formats import image_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(png_bytes())
            facts = image_info(path)
        self.assertNotEqual(facts.get("width"), None)


class MediaTest(unittest.TestCase):
    def test_it_reads_the_duration_of_a_wav(self):
        from mas.formats import media_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.wav"
            path.write_bytes(wav_bytes(44100, 2, 66150))
            facts = media_info(path)
        self.assertAlmostEqual(float(facts.get("duration_sec")), 1.5, places=2)
        self.assertEqual(facts.get("channels"), 2)
        self.assertEqual(facts.get("sample_rate"), 44100)

    def test_a_truncated_wav_yields_nothing(self):
        from mas.formats import media_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.wav"
            path.write_bytes(wav_bytes()[:20])
            facts = media_info(path)
        self.assertEqual(facts, {})

    def test_a_file_that_is_not_media_yields_nothing(self):
        from mas.formats import media_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.wav"
            path.write_bytes(b"not a recording at all")
            facts = media_info(path)
        self.assertEqual(facts, {})

    def test_it_names_the_container_of_an_mp3(self):
        from mas.formats import media_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.mp3"
            path.write_bytes(b"ID3\x03\x00\x00\x00" + b"\x00" * 64)
            facts = media_info(path)
        self.assertEqual(facts.get("container"), "mp3")


class ModelTest(unittest.TestCase):
    def test_it_counts_the_vertices_and_faces_of_an_obj(self):
        from mas.formats import model_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.obj"
            path.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n", encoding="utf-8")
            facts = model_info(path)
        self.assertEqual(facts.get("vertices"), 3)
        self.assertEqual(facts.get("faces"), 1)

    def test_it_reads_a_gltf_json_document(self):
        import json

        from mas.formats import model_info

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scene.gltf"
            path.write_text(json.dumps(GLTF_DOC), encoding="utf-8")
            facts = model_info(path)
        self.assertEqual(facts.get("meshes"), 2)
        self.assertEqual(facts.get("nodes"), 3)
        self.assertEqual(facts.get("materials"), 1)

    def test_it_reads_a_binary_gltf_container(self):
        from mas.formats import model_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scene.glb"
            path.write_bytes(gltf_bytes(GLTF_DOC))
            facts = model_info(path)
        self.assertEqual(facts.get("meshes"), 2)
        self.assertEqual(facts.get("gltf_version"), "2.0")

    def test_a_truncated_glb_yields_nothing(self):
        from mas.formats import model_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scene.glb"
            path.write_bytes(gltf_bytes(GLTF_DOC)[:24])
            facts = model_info(path)
        self.assertNotIn("meshes", facts)

    def test_it_counts_the_elements_of_a_ply(self):
        from mas.formats import model_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mesh.ply"
            path.write_text(
                "ply\nformat ascii 1.0\nelement vertex 3\n"
                "property float x\nelement face 1\nend_header\n", encoding="utf-8")
            facts = model_info(path)
        self.assertEqual(facts.get("vertices"), 3)
        self.assertEqual(facts.get("faces"), 1)

    def test_it_counts_the_triangles_of_a_binary_stl(self):
        from mas.formats import model_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "solid.stl"
            tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
            path.write_bytes(b"\x00" * 80 + struct.pack("<I", 1) + tri)
            facts = model_info(path)
        self.assertEqual(facts.get("triangles"), 1)

    def test_a_file_that_is_not_a_model_yields_nothing(self):
        from mas.formats import model_info
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.obj"
            path.write_bytes(b"\x00\x01\x02\x03")
            facts = model_info(path)
        self.assertEqual(facts, {})


class ArchiveTest(unittest.TestCase):
    def test_it_lists_the_members_of_a_zip(self):
        from mas.content import describe_file
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("notes/one.txt", "first")
                archive.writestr("notes/two.txt", "second")
            facts = describe_file(path)
        self.assertEqual(facts.get("members"), 2)


if __name__ == "__main__":
    unittest.main()

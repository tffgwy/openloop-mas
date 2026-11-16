# -*- coding: utf-8 -*-
"""v0.7.70: content extraction - read what is *inside* a file.

The gap this closes
-------------------
A machine check could only look at the *bytes* of a file. That is
enough for "the file has the line in it", but it is blind for every
format whose payload is not plain text:

* a PDF stores its text inside a compressed stream, so the bytes of
  the file do not contain the words - ``file_contains`` on a PDF is a
  coin flip;
* a .docx / .xlsx / .pptx is a zip file, so the words live in a member
  of the archive;
* an image has no text at all, but it *does* have a format, a size and
  a header that says which one it is.

So the loop could only accept work it could read as text, and a task
whose deliverable is a PDF was unjudgeable - the criterion would be
handed to the model, and a model that says "looks fine" would close a
criterion whose rule was never written. That is a fake closure.

This module extracts the *content* of a file, in the standard library
only, and says what it managed to do:

``extract_text(path)``
    the readable text of a file, whatever the format;
``describe_file(path)``
    a fact sheet: format, size, and format-specific facts (pages for a
    PDF, sheets for a workbook, ...).

Design rules
------------
* No dependency, no binary, no network. A PDF is read by parsing its
  own structure; a .docx is read by unzipping it.
* A format we cannot read is **not** a failure: the extraction
  returns what it could get and the reason it could not get more, so
  the caller decides what to do with it.
* Nothing is ever executed. An archive is only ever *read*.
"""

from __future__ import annotations

import pathlib
import re
import struct
import zipfile
import zlib
from typing import Any, Dict, List, Tuple

#: The maximum number of bytes read from one file.
MAX_BYTES = 16 * 1024 * 1024

#: The formats we know how to read.
KNOWN_FORMATS = (
    "text",
    "markdown",
    "json",
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "image",
    "zip",
    "font",
    "audio",
    "video",
    "model",
    "archive",
    "binary",
)

#: The extensions that are fonts.
_FONT_EXTENSIONS = {
    ".ttf": "truetype",
    ".otf": "opentype",
    ".ttc": "fontcollection",
    ".woff": "woff",
    ".woff2": "woff2",
}

#: The extensions that are audio files.
_AUDIO_EXTENSIONS = {
    ".wav": "audio",
    ".wave": "audio",
    ".mp3": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".aac": "audio",
}

#: The extensions that are video files.
_VIDEO_EXTENSIONS = {
    ".mp4": "video",
    ".m4v": "video",
    ".mov": "video",
    ".mkv": "video",
    ".webm": "video",
    ".avi": "video",
}

#: The extensions that are 3D models.
_MODEL_EXTENSIONS = {
    ".gltf": "model",
    ".glb": "model",
    ".obj": "model",
    ".stl": "model",
    ".fbx": "model",
    ".ply": "model",
    ".3mf": "model",
}

#: The extensions that are archives.
_ARCHIVE_EXTENSIONS = {
    ".zip": "zip",
    ".tar": "tar",
    ".gz": "gzip",
    ".tgz": "gzip",
    ".7z": "archive",
    ".rar": "archive",
}

#: The magic bytes of the image formats we can recognise.
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"II*\x00",
    b"MM\x00*",
)

#: The extensions that are images, used when the magic bytes do not
#: decide (a RIFF file is a WAV or a WEBP - the extension says which).
_IMAGE_EXTENSIONS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".bmp": "bmp",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
}

#: The extensions that are office documents (all of them zip files).
_OFFICE_EXTENSIONS = {
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

#: The extensions that are plain text.
_TEXT_EXTENSIONS = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "text",
    ".log": "text",
    ".json": "json",
    ".jsonl": "json",
    ".csv": "text",
    ".tsv": "text",
    ".yaml": "text",
    ".yml": "text",
    ".toml": "text",
    ".ini": "text",
    ".cfg": "text",
    ".xml": "text",
    ".html": "text",
    ".htm": "text",
    ".py": "text",
    ".js": "text",
    ".ts": "text",
    ".css": "text",
    ".sh": "text",
    ".ps1": "text",
}

#: The office formats, by name (the extension map above is keyed by
#: extension, this one by format).
_OFFICE_FORMATS = frozenset(_OFFICE_EXTENSIONS.values())

#: The office formats whose text lives in these members of the archive.
_OFFICE_TEXT_MEMBERS = {
    "docx": ("word/document.xml",),
    "xlsx": ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"),
    "pptx": ("ppt/slides/slide1.xml",),
}


def human_size(size: int) -> str:
    """A short, human readable size."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%.1f %s" % (value, unit)
        value /= 1024
    return "%.1f GB" % value


def detect_format(path: Any) -> str:
    """Name the format of a file, by magic bytes first and extension second.

    The magic bytes decide, because the extension lies. A ``.txt`` that
    is really a PDF is a PDF.
    """
    target = pathlib.Path(path)
    suffix = target.suffix.lower()
    try:
        with target.open("rb") as handle:
            head = handle.read(64)
    except OSError:
        return "binary"
    for signature in (
        (b"\x00\x01\x00\x00", "font"),
        (b"OTTO", "font"),
        (b"ttcf", "font"),
        (b"wOFF", "font"),
        (b"wOF2", "font"),
    ):
        if head.startswith(signature[0]):
            return str(signature[1])
    if head.startswith((b"glTF", b"Kaydara FBX Binary")):
        return "model"
    if head.startswith((b"ply\n", b"ply\r")):
        return "model"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "archive"
    if head.startswith(b"Rar!"):
        return "archive"
    for signature in _IMAGE_SIGNATURES:
        if head.startswith(signature):
            return "image"
    if head[:4] == b"RIFF":
        if head[8:12] == b"WEBP":
            return "image"
        if head[8:12] == b"AVI ":
            return "video"
        if head[8:12] == b"WAVE":
            return "audio"
        if suffix in _VIDEO_EXTENSIONS:
            return "video"
        return "audio"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        if suffix in _OFFICE_EXTENSIONS:
            return _OFFICE_EXTENSIONS[suffix]
        if suffix in _MODEL_EXTENSIONS:
            return "model"
        if suffix in _ARCHIVE_EXTENSIONS and suffix not in (".zip",):
            return "archive"
        return "zip"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head[4:8] == b"ftyp":
        # An ISO base media file: MP4, MOV, M4A. The brand says which.
        if suffix in _VIDEO_EXTENSIONS:
            return "video"
        if suffix in _AUDIO_EXTENSIONS:
            return "audio"
        return "video"
    if head.startswith(b"ID3"):
        return "audio"
    if suffix in _FONT_EXTENSIONS:
        return "font"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    if suffix in _MODEL_EXTENSIONS:
        return "model"
    if suffix in _ARCHIVE_EXTENSIONS:
        return "archive"
    return _TEXT_EXTENSIONS.get(suffix, "binary")


def _pdf_streams(data: bytes) -> List[bytes]:
    """Return the raw (still compressed) content streams of a PDF."""
    streams: List[bytes] = []
    marker = b"stream"
    index = 0
    while True:
        found = data.find(marker, index)
        if found < 0:
            break
        start = found + len(marker)
        if data[start:start + 2] == b"\r\n":
            start += 2
        elif data[start:start + 1] in (b"\n", b"\r"):
            start += 1
        end = data.find(b"endstream", start)
        if end < 0:
            break
        streams.append(data[start:end])
        index = end + len("endstream")
    return streams


def _pdf_text_from_stream(stream: bytes) -> str:
    """Pull the text out of one PDF content stream.

    PDF text is drawn by operators such as ``(hello) Tj`` or
    ``[(a) -100 (b)] TJ``. The strings are read out of the parentheses,
    with the escape sequences undone. The glyphs themselves are not
    decoded - a PDF may use a subset font with a custom encoding - but
    the *words* are recoverable, which is what a content check needs.
    """
    out: List[str] = []
    index = 0
    size = len(stream)
    while index < size:
        if stream[index:index + 1] != b"(":
            index += 1
            continue
        index += 1
        depth = 1
        buffer = bytearray()
        while index < size and depth > 0:
            current = stream[index:index + 1]
            if current == b"\\":
                index += 1
                escaped = stream[index:index + 1]
                mapping = {
                    b"n": b"\n",
                    b"r": b"\r",
                    b"t": b"\t",
                    b"b": b"\b",
                    b"f": b"\f",
                    b"(": b"(",
                    b")": b")",
                    b"\\": b"\\",
                }
                buffer.extend(mapping.get(escaped, escaped))
                index += 1
                continue
            if current == b"(":
                depth += 1
                buffer.extend(current)
            elif current == b")":
                depth -= 1
                if depth:
                    buffer.extend(current)
            elif current in (b"\n", b"\r"):
                pass
            else:
                buffer.extend(current)
            index += 1
        out.append(buffer.decode("utf-8", errors="replace"))
    return "\n".join(out)


def _pdf_page_count(data: bytes) -> int:
    """Count the pages of a PDF by counting its page objects."""
    return len(re.findall(rb"/Type\s*/Page[^s]", data))


def extract_pdf_text(path: pathlib.Path) -> Tuple[str, str]:
    """Return ``(text, reason)`` for a PDF."""
    data = path.read_bytes()
    text_parts: List[str] = []
    for stream in _pdf_streams(data):
        raw = stream
        if raw[:1] == b"\x78":
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = b""
        text_parts.append(_pdf_text_from_stream(raw))
    return "\n".join(part for part in text_parts if part), ""


def _strip_xml(data: bytes) -> str:
    """Turn an XML document into its readable text."""
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    return re.sub(r"[ \t]+", " ", text)


def _office_text(path: pathlib.Path, kind: str) -> Tuple[str, str]:
    """Return ``(text, reason)`` for a .docx / .xlsx / .pptx."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            parts: List[str] = []
            for member in _OFFICE_TEXT_MEMBERS.get(kind, ()):
                if member not in names:
                    continue
                data = archive.read(member)
                if member.endswith(".xml"):
                    parts.append(_strip_xml(data))
                else:
                    parts.append(data.decode("utf-8", errors="replace"))
            return "\n".join(part for part in parts if part), ""
    except (zipfile.BadZipFile, OSError) as exc:
        return "", "cannot read the archive: %s" % exc


def _zip_text(path: pathlib.Path) -> Tuple[str, str]:
    """Return the text of the members of a zip archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            parts: List[str] = []
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                try:
                    data = archive.read(name)
                except (KeyError, OSError):
                    continue
                if len(data) > MAX_BYTES:
                    continue
                parts.append(data.decode("utf-8", errors="replace"))
            return "\n".join(parts), ""
    except (zipfile.BadZipFile, OSError) as exc:
        return "", "cannot read the archive: %s" % exc


def extract_text(path: Any) -> Dict[str, Any]:
    """Extract the readable text of a file.

    Returns a dict with:

    ``text``      the text that was found (may be empty);
    ``format``    the format that was recognised;
    ``ok``        True when the format could be read;
    ``reason``    why it could not be read (empty when it could);
    ``bytes``     the size of the file.
    """
    target = pathlib.Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {
            "text": "",
            "format": "binary",
            "ok": False,
            "reason": "cannot stat the file: %s" % exc,
            "bytes": 0,
        }
    if size > MAX_BYTES:
        return {
            "text": "",
            "format": "binary",
            "ok": False,
            "reason": "file too large to read (%s)" % human_size(size),
            "bytes": size,
        }
    kind = detect_format(target)
    text = ""
    reason = ""
    ok = True
    if kind == "pdf":
        text, reason = extract_pdf_text(target)
        ok = bool(text.strip())
        if not ok and not reason:
            reason = "no readable text stream in the PDF"
    elif kind in _OFFICE_FORMATS:
        text, reason = _office_text(target, kind)
        ok = bool(text.strip())
        if not ok and not reason:
            reason = "no readable text in the archive"
    elif kind == "zip":
        text, reason = _zip_text(target)
        ok = bool(text.strip())
        if not ok and not reason:
            reason = "no readable member in the archive"
    elif kind == "image":
        text = ""
    elif kind in {"text", "markdown", "json", "binary"}:
        try:
            data = target.read_bytes()
        except OSError as exc:
            return {
                "text": "",
                "format": kind,
                "ok": False,
                "reason": "cannot read the file: %s" % exc,
                "bytes": size,
            }
        if b"\x00" in data[:4096]:
            kind = "binary"
            text = ""
            ok = False
            reason = "binary content, no text to read"
        else:
            text = data.decode("utf-8", errors="replace")
    else:
        try:
            data = target.read_bytes()
        except OSError as exc:
            return {
                "text": "",
                "format": kind,
                "ok": False,
                "reason": "cannot read the file: %s" % exc,
                "bytes": size,
            }
        text = data.decode("utf-8", errors="replace")
    return {
        "text": text,
        "format": kind,
        "ok": ok,
        "reason": reason,
        "bytes": size,
    }


def _image_size(path: pathlib.Path) -> Dict[str, int]:
    """The pixel size of an image, when the format says it plainly."""
    head = b""
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
    except OSError:
        return {}
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
            width, height = struct.unpack(">II", head[16:24])
            return {"width": int(width), "height": int(height)}
        if head.startswith(b"GIF") and len(head) >= 10:
            width, height = struct.unpack("<HH", head[6:10])
            return {"width": int(width), "height": int(height)}
        if head.startswith(b"BM") and len(head) >= 26:
            width, height = struct.unpack("<ii", head[18:26])
            return {"width": abs(int(width)), "height": abs(int(height))}
    except struct.error:
        return {}
    return {}


def describe_file(path: Any) -> Dict[str, Any]:
    """A fact sheet about a file: what it is and what is in it."""
    target = pathlib.Path(path)
    extracted = extract_text(target)
    kind = extracted["format"]
    facts: Dict[str, Any] = {
        "name": target.name,
        "path": str(target),
        "format": kind,
        "bytes": int(extracted["bytes"]),
        "size": human_size(int(extracted["bytes"])),
        "readable": bool(extracted["ok"]),
        "reason": str(extracted["reason"] or ""),
    }
    if kind == "pdf":
        try:
            data = target.read_bytes()
        except OSError:
            data = b""
        facts["pages"] = _pdf_page_count(data)
        facts["text_chars"] = len(extracted["text"])
    elif kind in _OFFICE_FORMATS or kind == "zip":
        try:
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, OSError):
            names = []
        facts["members"] = len(names)
        facts["text_chars"] = len(extracted["text"])
    elif kind == "image":
        facts.update(_image_size(target))
    else:
        facts["text_chars"] = len(extracted["text"])
    facts.update(_extra_facts(target, kind))
    return facts


def _extra_facts(target: pathlib.Path, kind: str) -> Dict[str, Any]:
    """The facts that only the deeper parsers can supply.

    ``describe_file`` already knows the size of a file and the number of
    pages in a PDF. These are the facts about the *payload*: the family
    of a font, the duration of a recording, the number of triangles in a
    model. A file whose header does not state them yields nothing - a
    guess here would be indistinguishable from a fact.
    """
    from mas.formats import font_info, image_info, media_info, model_info

    try:
        if kind == "font":
            return font_info(target)
        if kind == "image":
            return image_info(target)
        if kind in ("audio", "video"):
            return media_info(target)
        if kind == "model":
            return model_info(target)
    except Exception:  # pragma: no cover - a broken header must not crash
        return {}
    return {}

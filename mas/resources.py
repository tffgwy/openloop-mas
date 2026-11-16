# -*- coding: utf-8 -*-
"""v0.7.71: the resource index - one place that knows every file.

The gap
-------
A run produces files in a folder, and the folder was only ever visible
through the run that made it: ``mas artifacts`` listed the *deliverable*
of every run, ``mas status`` summarised the machine, and a file that
was neither - a PDF the worker wrote as evidence, a font, a model - had
no listing at all. So the answer to "what did this machine actually
produce?" was scattered across commands, and the answer to "what is in
this folder?" did not exist.

This module is the folder-level answer. It walks a folder, names every
file, says what kind of file it is, and - where the header proves it -
what is inside it: the pages of a PDF, the family of a font, the
duration of a recording, the triangles of a model.

Design rules
------------
* One folder is the unit. Every file in the folder is listed, including
  the ones no criterion mentions: a supporting file that nobody checks
  is still part of the work.
* Facts come from the file's own header, never from its name. A file
  called ``report.pdf`` that is actually a PNG is reported as a PNG.
* A file that cannot be read is listed with the reason. It is never
  silently dropped: an unreadable file in the middle of a deliverable
  is exactly the thing a reader needs to see.
* The index is a *description*, not a loader. Nothing is executed.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

from mas.content import describe_file, detect_format, human_size

#: The formats that carry no text but still carry facts.
FACTFUL_FORMATS = ("pdf", "image", "font", "audio", "video", "model", "zip")

#: The order the formats are shown in, so the reader sees the same
#: grouping every time.
FORMAT_ORDER = (
    "markdown",
    "text",
    "json",
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "image",
    "font",
    "audio",
    "video",
    "model",
    "zip",
    "gzip",
    "archive",
    "binary",
)

#: The facts that are worth printing for a given format, in order.
FACT_KEYS = {
    "pdf": ("pages", "text_chars"),
    "image": ("width", "height"),
    "font": ("family", "glyphs"),
    "audio": ("duration_sec", "channels", "sample_rate"),
    "video": ("duration_sec", "tracks", "brand"),
    "model": ("meshes", "vertices", "triangles", "faces"),
    "zip": ("members",),
    "docx": ("members", "text_chars"),
    "xlsx": ("members", "text_chars"),
    "pptx": ("members", "text_chars"),
}


def _skip_name(name: str) -> bool:
    """Whether a file is noise rather than work."""
    return name in (".DS_Store", "Thumbs.db")


def describe_folder(
    root: Any,
    skip_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Index every file of a folder.

    Returns a dict with:

    ``root``     the folder that was indexed;
    ``files``    one row per file, sorted by path;
    ``count``    how many files there are;
    ``formats``  how many files of each format;
    ``bytes``    the total size.
    """
    base = pathlib.Path(root)
    skipped = set(skip_names or ())
    rows: List[Dict[str, Any]] = []
    formats: Dict[str, int] = {}
    total = 0
    if base.exists() and base.is_dir():
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.name in skipped or _skip_name(path.name):
                continue
            row = describe_file(path)
            row["relative"] = str(path.relative_to(base)).replace("\\", "/")
            rows.append(row)
            formats[row["format"]] = formats.get(row["format"], 0) + 1
            total += int(row.get("bytes") or 0)
    return {
        "root": str(base),
        "exists": base.exists(),
        "files": rows,
        "count": len(rows),
        "formats": formats,
        "bytes": total,
        "size": human_size(total),
    }


def format_summary(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The formats of a folder, grouped, biggest group first."""
    counts = dict(index.get("formats") or {})
    order = {name: position for position, name in enumerate(FORMAT_ORDER)}
    rows = [
        {"format": name, "count": count}
        for name, count in counts.items()
    ]
    rows.sort(key=lambda row: (order.get(str(row["format"]), 99), -int(row["count"])))
    return rows


def facts_for(row: Dict[str, Any]) -> List[str]:
    """The readable facts of one file, as short strings."""
    kind = str(row.get("format") or "")
    out: List[str] = []
    for key in FACT_KEYS.get(kind, ()):
        value = row.get(key)
        if value in (None, "", 0):
            continue
        if key in ("width", "height"):
            out.append("%s %s" % (key, value))
        elif key in ("duration_sec",):
            out.append("%s s" % value)
        elif key in ("sample_rate",):
            out.append("%s Hz" % value)
        elif key in ("glyphs", "vertices", "triangles", "faces", "members", "tracks",
                     "pages", "text_chars", "channels", "meshes"):
            out.append("%s %s" % (key.replace("_", " "), value))
        else:
            out.append("%s %s" % (key.replace("_", " "), value))
    if not out and row.get("family"):
        out.append(str(row["family"]))
    return out


def preview_file(path: Any, max_chars: int = 4000) -> Dict[str, Any]:
    """A preview of one file: what it is, and the text it holds.

    The text is only there for the formats that hold text. A PDF's
    words are extracted; an image has none, and the preview says so
    instead of showing an empty screen.
    """
    target = pathlib.Path(path)
    facts = describe_file(target)
    out: Dict[str, Any] = {
        "name": target.name,
        "path": str(target),
        "format": facts.get("format", "binary"),
        "bytes": facts.get("bytes", 0),
        "size": facts.get("size", ""),
        "readable": facts.get("readable", False),
        "reason": facts.get("reason", ""),
        "facts": facts_for(facts),
        "text": "",
        "truncated": False,
    }
    for key, value in facts.items():
        if key not in out and key not in ("name", "path"):
            out[key] = value
    kind = str(facts.get("format") or "")
    if kind in ("text", "markdown", "json", "pdf", "docx", "xlsx", "pptx", "zip"):
        from mas.content import extract_text

        extracted = extract_text(target)
        text = str(extracted.get("text") or "")
        if len(text) > int(max_chars):
            text = text[: int(max_chars)]
            out["truncated"] = True
        out["text"] = text
        out["text_chars"] = len(str(extracted.get("text") or ""))
    elif kind == "binary":
        out["reason"] = out["reason"] or "二进制文件，没有可预览的正文"
    return out


def index_rows(index: Dict[str, Any]) -> List[str]:
    """The rows of an index, ready to be printed."""
    out: List[str] = []
    for row in index.get("files") or []:
        name = str(row.get("relative") or row.get("name") or "")
        out.append("%s\t%s\t%s" % (row.get("format", ""), row.get("size", ""), name))
    return out


def detect_kind(path: Any) -> str:
    """The format of a file, by its own header."""
    return detect_format(path)

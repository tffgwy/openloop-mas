# -*- coding: utf-8 -*-
"""v0.7.69: the human handoff - edit in place, judge the draft, keep it on refusal.

The gap
-------
The handback path of v0.7.67 took a human deliverable as *one string*.
That works for prose, but the loop this project runs produces its work
as *files*: a worker that has to write ``notes/finding.txt`` writes a
file, and the acceptance criteria that matter are file checks -
``file_exists``, ``file_contains``, ``file_regex``. A human who fixes
such a task does the same thing the worker did: they open the file in
the workspace and change it.

But there was nowhere to do that. The ticket carried the workspace
path and no command ever printed it, so the human had to go and find
the folder by hand; and the handback command could only accept one
file or one blob of text, so a human who had fixed *three* files had
to either concatenate them (and the file checks would then read the
original, unfixed files off the disk) or hand back one file and lose
the other two.

What this module does
---------------------
It is the "draft first, write back on approval" rule, applied to a
folder:

* **Read** the workspace as it stands *now* - the human's edits are
  already in it.
* **Judge** it against the ticket's own acceptance criteria, with the
  folder's current contents as an *overlay*. The judge sees exactly
  what is on the disk, and it changes nothing.
* **Write back** only what the acceptance criteria demand, and only
  after the judge says yes. The deliverable is then written next to
  the files, and the loop closes.

Three properties are load-bearing:

* **Nothing is written before the verdict.** The checks run against an
  overlay, so a rejected deliverable cannot leave half-written files
  behind.
* **A refusal changes nothing.** The human's edits stay exactly where
  they put them. A rejected draft is still their work, and the next
  attempt starts from it.
* **The folder is the unit.** Every file the human touched is part of
  the deliverable, not just one of them.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, List

#: The name of the file that holds the human-written deliverable when
#: the handoff is made from a folder.
DELIVERABLE_NAME = "deliverable.md"

#: Files that are never part of a handoff: scratch files that carry no
#: meaning for the acceptance criteria.
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def human_size(size: int) -> str:
    """A short human reading of a byte count."""
    if size < 1024:
        return "%d B" % size
    if size < 1024 * 1024:
        return "%.1f KB" % (size / 1024.0)
    return "%.1f MB" % (size / (1024.0 * 1024.0))


def collect_workspace_files(root: Any) -> List[Dict[str, Any]]:
    """List the files of a workspace, relative to it.

    The deliverable of the previous attempt is left out on purpose:
    the human is offering a *new* deliverable, and the old one is
    replaced by it.
    """
    base = pathlib.Path(root)
    if not base.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name in SKIP_NAMES:
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:  # pragma: no cover - defensive
            continue
        if str(relative).replace("\\", "/") == DELIVERABLE_NAME:
            continue
        try:
            size = path.stat().st_size
        except OSError:  # pragma: no cover - defensive
            continue
        rows.append(
            {
                "path": str(relative).replace("\\", "/"),
                "absolute": str(path),
                "bytes": int(size),
            }
        )
    return rows


def read_workspace_overlay(root: Any) -> Dict[str, bytes]:
    """Read every file of a workspace into an overlay.

    The overlay is what the file checks read instead of the disk. The
    bytes are read once, so the judge sees a consistent snapshot even
    if the human keeps editing while the review runs.
    """
    overlay: Dict[str, bytes] = {}
    for row in collect_workspace_files(root):
        try:
            data = pathlib.Path(row["absolute"]).read_bytes()
        except OSError:
            continue
        overlay[str(row["absolute"])] = data
    return overlay


def build_deliverable(files: List[Dict[str, Any]]) -> str:
    """Write the human-readable deliverable for a folder handoff.

    The deliverable is an *index* of what the human produced, plus the
    content of the files that are small enough to read. It is what the
    text checks (``contains``, ``section`` ...) are judged against, so
    it has to carry the human's own words.
    """
    if not files:
        return ""
    lines: List[str] = []
    lines.append("# 人工交付物")
    lines.append("")
    lines.append("这个文件夹是人工改好的交付物，共 %d 个文件：" % len(files))
    lines.append("")
    for row in files:
        lines.append(
            "- %s（%s）"
            % (row.get("path"), human_size(int(row.get("bytes") or 0)))
        )
    lines.append("")
    for row in files:
        relative = str(row.get("path") or "")
        absolute = str(row.get("absolute") or "")
        if int(row.get("bytes") or 0) > 20000:
            lines.append("## %s" % relative)
            lines.append("")
            lines.append("（文件较大，正文未展开）")
            lines.append("")
            continue
        try:
            text = pathlib.Path(absolute).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        lines.append("## %s" % relative)
        lines.append("")
        lines.append(text.rstrip())
        lines.append("")
    return "\n".join(lines)


def file_listing_reading(files: List[Dict[str, Any]]) -> str:
    """A one-line human reading of the files in a handoff."""
    if not files:
        return "没有文件"
    total = sum(int(row.get("bytes") or 0) for row in files)
    return "%d 个文件，共 %s" % (len(files), human_size(total))

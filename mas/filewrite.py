"""The write-file protocol (v0.7.15).

The gap
-------
The file checks of v0.7.15 can *prove* that a file is on the disk. But
the loop had no way to put one there: the worker's only output was a
string, so a goal like "write the conclusion into notes/finding.txt" was
answered with prose about writing the file. The check then failed -
correctly - and the loop could never close, no matter how many
iterations it ran.

The protocol
------------
A worker that needs to write a file emits a fenced block:

    <<<MAS:FILE path="notes/finding.txt">>>
    ...the file content...
    <<<MAS:END FILE>{3,}

The engine parses the block, writes the content to the path (inside the
workspace only), and removes the block from the artifact. The artifact
therefore stays a readable deliverable, and the disk carries the file.

Safety rules (identical in spirit to the file checks)
-----------------------------------------------------
* A path outside the workspace is refused. The worker may not write to
  ``C:\\Windows`` or to ``~/.ssh``.
* The path must be relative or already inside the workspace.
* A write is recorded in the ledger, so a reviewer can see exactly which
  files the loop touched.
* The content is written verbatim - no templating, no substitution.
"""
from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

# The opening marker carries the path in a small attribute syntax:
#   <<<MAS:FILE path="notes/finding.txt">>>
# The path may also be given without quotes.
# The body is matched lazily up to the *first* closing marker, and the
# opening marker consumes exactly three ``>``. An earlier version used
# ``.*?>>>`` for the closing marker as well, which swallowed the third
# ``>`` of the *opening* tag and left a stray ``>`` at the top of the
# written file.
# The path is matched with a *possessive-free* but bounded pattern: it
# may not contain the closing quote. A lazy ``.*?`` followed by the
# closing quote backtracks into the marker itself, which swallowed the
# third ``>`` of the opening tag and left a stray ``>`` at the top of
# every written file.
# The opening marker may carry an ``encoding`` attribute:
#
#   <<<MAS:FILE path="out/report.pdf" encoding="base64">>>
#
# A PDF, an image or a spreadsheet is *not* text. Without this the
# worker could only write UTF-8, so a task whose deliverable is a PDF
# had no way to produce one - and the content checks of v0.7.70 would
# never have anything to read.
_BLOCK_RE = re.compile(
    r"<<<MAS:FILE[ \t]+path=(?P<q>[\"'])(?P<path>[^\"']*)(?P=q)"
    r"(?P<attrs>(?:[ \t]+[A-Za-z_][A-Za-z0-9_]*=(?P<aq>[\"\'])(?P<aval>[^\"\']*)(?P=aq))*)[ \t]*>{3,}[ \t]*\r?\n?"
    r"(?P<body>.*?)"
    r"<<<MAS:END FILE(?:>{3,}|>\{3,\})",
    re.DOTALL,
)

MAX_WRITE_BYTES = 4 * 1024 * 1024

# The instruction that tells the worker how to write a file contains a
# *template* block. A model that copies the template verbatim would
# otherwise make the engine write a file called "relative path" - and, if
# the template were used with a real path, it could fake a file check
# with placeholder content. These paths are therefore ignored.
PLACEHOLDER_PATHS = {
    "\u76f8\u5bf9\u8def\u5f84",
    "<path>",
    "<\u76f8\u5bf9\u8def\u5f84>",
    "path/to/file.txt",
    "your-file.txt",
}

# The *body* of a template block. The binary template names a
# real-looking path ("out/report.pdf"), so the path alone cannot mark
# it as a template - a task may well want to write exactly that file.
# The body can: a template says "文件内容" or "JVBERi0xLjQK...", and a
# real request never says that. A model that copies the template
# verbatim would otherwise make the engine write a file whose content
# is the literal text of the example - and a file check on that name
# would then be satisfied by a placeholder. A template is not a
# request.
PLACEHOLDER_BODIES = {
    "\u6587\u4ef6\u5185\u5bb9",
    "JVBERi0xLjQK...",
    "<content>",
    "file content",
}



class FileWriteError(ValueError):
    """Raised when a write request cannot be honoured."""


def _strip_placeholders(text: str) -> Tuple[str, List[str]]:
    """Remove template blocks from ``text``.

    The instruction that explains the protocol embeds a *template* block.
    A model that copies the template verbatim must not make the engine
    write a file called "relative path" - and a placeholder file would
    also let a model fake a file check with template content. Such blocks
    are therefore removed from the text and reported separately.
    """
    if not text:
        return text or "", []

    found: List[str] = []

    def _repl(match: "re.Match[str]") -> str:
        path = match.group("path").strip()
        body = match.group("body").strip()
        if path in PLACEHOLDER_PATHS or body in PLACEHOLDER_BODIES:
            found.append(path)
            return ""
        return match.group(0)

    cleaned = _BLOCK_RE.sub(_repl, text)
    return cleaned, found


def _encoding_of(attrs: str) -> str:
    """The ``encoding`` attribute of a write block (empty when absent)."""
    found = re.search(
        r"\bencoding=(?P<q>[\"\'])(?P<value>[^\"\']*)(?P=q)", attrs or ""
    )
    if not found:
        return ""
    return str(found.group("value")).strip().lower()


def parse_write_blocks(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Split ``text`` into the deliverable and the file-write requests.

    Returns ``(artifact, writes)`` where ``writes`` is a list of
    ``{"path": str, "content": str}``. The blocks are removed from the
    artifact so the deliverable stays readable.

    A malformed block (no path, an unterminated block) is *left in the
    artifact* and reported as an error - the engine must not silently
    swallow half a request.
    """
    if not text:
        return text or "", []
    text, _placeholders = _strip_placeholders(text)
    writes: List[Dict[str, Any]] = []
    errors: List[str] = []

    def _collect(match: "re.Match[str]") -> str:
        path = match.group("path").strip()
        content = match.group("body")
        if content.endswith("\n"):
            content = content[:-1]
        if not path:
            errors.append("a MAS:FILE block has no path")
            return match.group(0)
        encoding = _encoding_of(match.group("attrs") or "")
        if encoding and encoding not in ("base64", "utf-8", "utf8", "text"):
            errors.append(
                "a MAS:FILE block asks for an unknown encoding: %s" % encoding
            )
            return match.group(0)
        writes.append({"path": path, "content": content, "encoding": encoding})
        return ""

    artifact = _BLOCK_RE.sub(_collect, text)

    # An opening marker without a closing one is a broken request.
    leftovers = _BLOCK_RE.sub("", text)
    if "<<<MAS:FILE" in leftovers:
        errors.append("a MAS:FILE block is not closed with <<<MAS:END FILE>{3,}")

    if errors:
        # Keep the malformed text in the artifact so a human - and the
        # reviewer - can see what the worker actually produced.
        artifact = text
        return artifact, []

    return artifact, writes


def parse_write_blocks_with_errors(
    text: str,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """``parse_write_blocks`` and the reasons it refused to parse.

    ``parse_write_blocks`` returns an *empty* list of writes when a
    block is malformed, and the malformed text stays in the artifact.
    That is the right behaviour for a reader - but the caller has to
    be able to say *why* nothing was written. Without this, a worker
    that asked for ``encoding="rot13"`` was refused in silence: no
    file, no error, no hint in the ledger. The loop then went round
    again with the same request, for ever.
    """
    if not text:
        return text or "", [], []
    text, _placeholders = _strip_placeholders(text)
    writes: List[Dict[str, Any]] = []
    errors: List[str] = []

    def _collect(match: "re.Match[str]") -> str:
        path = match.group("path").strip()
        content = match.group("body")
        if content.endswith("\n"):
            content = content[:-1]
        if not path:
            errors.append("a MAS:FILE block has no path")
            return match.group(0)
        encoding = _encoding_of(match.group("attrs") or "")
        if encoding and encoding not in ("base64", "utf-8", "utf8", "text"):
            errors.append(
                "a MAS:FILE block asks for an unknown encoding: %s" % encoding
            )
            return match.group(0)
        writes.append({"path": path, "content": content, "encoding": encoding})
        return ""

    artifact = _BLOCK_RE.sub(_collect, text)
    leftovers = _BLOCK_RE.sub("", text)
    if "<<<MAS:FILE" in leftovers:
        errors.append("a MAS:FILE block is not closed with <<<MAS:END FILE>{3,}")
    if errors:
        return text, [], errors
    return artifact, writes, []


def _safe_target(root: Path, raw_path: str) -> Path:
    root = root.resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise FileWriteError(f"refusing to write outside the workspace: {raw_path}") from exc
        return resolved
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise FileWriteError(f"refusing to write outside the workspace: {raw_path}") from exc
    return resolved


def _request_bytes(content: str, encoding: Any = None) -> bytes:
    """The bytes a write request stands for.

    A block without an encoding is text and is written as UTF-8. A block
    that says ``encoding="base64"`` carries **binary** content - a PDF,
    an image, a spreadsheet - and is decoded before it is written. The
    decoded bytes are what the content checks of v0.7.70 read.
    """
    kind = str(encoding or "").strip().lower()
    if kind in ("base64",):
        # The whitespace is removed first - a base64 payload is often
        # wrapped over several lines - and the rest is validated
        # *strictly*. The lenient decoder dropped every character that
        # was not part of the alphabet, so a block of pure nonsense
        # decoded into an empty file without a word of complaint: the
        # worker asked for a PDF and got a zero-byte file that the
        # content checks then judged as unreadable. A silent zero-byte
        # file is worse than a refusal, because the refusal at least
        # says what went wrong.
        cleaned = "".join(str(content).split())
        try:
            return base64.b64decode(cleaned, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FileWriteError(
                "cannot decode the base64 content: %s" % exc
            ) from exc
    return str(content).encode("utf-8")


def apply_write_blocks(
    text: str,
    root: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Parse the write requests in ``text`` and perform them.

    Returns a dict with ``artifact`` (the text without the blocks),
    ``written`` (the files that were created or updated) and ``errors``.
    A path that cannot be written is reported in ``errors`` and does not
    stop the other writes.
    """
    text, placeholders = _strip_placeholders(text)
    artifact, writes, parse_errors = parse_write_blocks_with_errors(text)
    root = Path(root)
    written: List[Dict[str, Any]] = []
    errors: List[str] = list(parse_errors)
    skipped: List[Dict[str, Any]] = [
        {"path": path, "reason": "placeholder path from the template"} for path in placeholders
    ]

    for request in writes:
        raw_path = request["path"]
        content = request["content"]
        try:
            target = _safe_target(root, raw_path)
            data = _request_bytes(content, request.get("encoding"))
            if len(data) > MAX_WRITE_BYTES:
                raise FileWriteError(
                    f"refusing to write {len(data)} bytes (limit {MAX_WRITE_BYTES})"
                )
            if dry_run:
                written.append({"path": raw_path, "bytes": len(data), "written": False})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            target.write_bytes(data)
            written.append(
                {
                    "path": raw_path,
                    "absolute": str(target),
                    "bytes": len(data),
                    "written": True,
                    "created": not existed,
                }
            )
        except (FileWriteError, OSError) as exc:
            errors.append(str(exc))

    return {"artifact": artifact, "written": written, "errors": errors, "skipped": skipped}


def plan_writes(text: str, root: Path) -> Dict[str, Any]:
    """Work out *what would be written* without touching the disk.

    The parallel candidate ranking has to compare candidates against the
    file level checks, but the checks read the real filesystem - and at
    ranking time the candidate has not written its file yet. Ranking the
    candidates against the *old* disk state made every file level check
    fail for every candidate, so the file rules had no influence on the
    choice at all: the winner was picked by length, and it could be a
    candidate whose file would never satisfy the rule.

    This function returns the writes a candidate *would* perform, with
    their resolved target and content, so the caller can evaluate the
    file checks against the candidate's own output without writing it.
    Nothing is written here - the winning candidate is still written by
    the engine, and only that one.
    """
    text, placeholders = _strip_placeholders(text)
    artifact, writes, parse_errors = parse_write_blocks_with_errors(text)
    root = Path(root)
    planned: List[Dict[str, Any]] = []
    errors: List[str] = list(parse_errors)
    skipped: List[Dict[str, Any]] = [
        {"path": path, "reason": "placeholder path from the template"} for path in placeholders
    ]

    for request in writes:
        raw_path = request["path"]
        content = request["content"]
        try:
            target = _safe_target(root, raw_path)
            data = _request_bytes(content, request.get("encoding"))
            if len(data) > MAX_WRITE_BYTES:
                raise FileWriteError(
                    f"refusing to write {len(data)} bytes (limit {MAX_WRITE_BYTES})"
                )
            planned.append(
                {
                    "path": raw_path,
                    "absolute": str(target),
                    "content": content,
                    "encoding": request.get("encoding") or "",
                    # The *decoded* bytes: the overlay has to carry what
                    # would land on the disk, not the base64 text, or the
                    # content checks would read the encoding instead of
                    # the file.
                    "data": data,
                    "bytes": len(data),
                }
            )
        except (FileWriteError, OSError) as exc:
            errors.append(str(exc))

    return {"artifact": artifact, "planned": planned, "errors": errors, "skipped": skipped}


def write_instruction(paths: List[str]) -> str:
    """Build the instruction that tells a worker how to write a file."""
    if not paths:
        return ""
    listed = "\n".join(f"- {path}" for path in paths)
    return (
        "\n\n【写文件协议】本任务有文件级机检，你必须真的把文件写到磁盘上。\n"
        f"需要写入的文件：\n{listed}\n"
        "在交付文本中，用下面的围栏块表达每个文件的内容（路径用工作区相对路径）：\n"
        '<<<MAS:FILE path="相对路径">>>>\n'
        "文件内容\n"
        "<<<MAS:END FILE>>>\n"
        "引擎会把块里的内容写成真正的文件。写完文件后，请再用一两句话说明你写了什么；"
        "不要把文件内容重复贴在说明里。\n"
        "如果目标文件是**二进制**（PDF、图片、表格等），在开头标签上加 "
        '`encoding="base64"`，块里放 base64 编码后的内容：\n'
        '<<<MAS:FILE path="out/report.pdf" encoding="base64">>>>\n'
        "JVBERi0xLjQK...\n"
        "<<<MAS:END FILE>>>\n"
        "引擎会把它解码成真正的二进制文件。\n"
    )

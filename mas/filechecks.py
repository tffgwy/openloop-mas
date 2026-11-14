"""Generate mas/filechecks.py - file-level machine checks (v0.7.15).

The gap this closes
-------------------
Every machine check in ``mas/engine.py`` inspects the *artifact string* -
the text the model produced. That is enough to prove "the answer contains
the section", but it can never prove "the file on disk now contains the
line". A worker that claims it wrote ``out/report.md`` and produced a
plausible prose answer closes the loop on a promise, not on a fact.

This module adds checks that read the real filesystem, so the loop can
close on evidence:

``file_exists``      the path exists and is a file
``file_missing``     the path does not exist (cleanup / "the bug is gone")
``file_contains``    a string (or any of a list) appears in the file
``file_not_contains``no forbidden string appears in the file
``file_regex``       a regular expression matches somewhere in the file
``file_lines_min``   the file has at least N non-empty lines
``file_min_bytes``   the file is at least N bytes
``file_max_bytes``   the file is at most N bytes
``file_sha256``      the file's sha256 equals the given digest
``file_mtime_newer`` the file was modified after the given ISO timestamp

Design rules (identical to the text checks)
--------------------------------------------
* A check that cannot decide anything is reported as *unjudged*, never as
  passed. A missing file is a failing file; an unreadable path is an
  unjudged check.
* The path is resolved relative to the run's *workspace*, not the current
  working directory, so a run started from anywhere still checks the same
  file.
* Paths may not escape the workspace. A check that reaches outside the
  workspace is rejected as invalid - a standard must not be able to read
  the user's whole disk.
* Binary files are decoded leniently; a byte-level regex still works.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Human-readable description of what a usable argument looks like, per
# type. A check whose argument does not meet the requirement is a *broken
# check*, not a failing artifact.
FILE_CHECK_ARG_REQUIRED: Dict[str, str] = {
    "file_exists": "a file path",
    "file_missing": "a file path",
    "file_contains": "a file path plus the required string",
    "file_not_contains": "a file path plus the forbidden string",
    "file_regex": "a file path plus a regular expression",
    "file_lines_min": "a file path plus a minimum line count",
    "file_min_bytes": "a file path plus a minimum byte count",
    "file_max_bytes": "a file path plus a maximum byte count",
    "file_sha256": "a file path plus the expected sha256 digest",
    "file_mtime_newer": "a file path plus an ISO timestamp",
}

FILE_CHECK_TYPES = tuple(FILE_CHECK_ARG_REQUIRED)

# The argument of a file check is an object, not a bare string:
#   {"path": "out/report.md", "value": "结论"}
# ``value`` is required for the checks that compare against something.
FILE_CHECK_VALUE_REQUIRED = {
    "file_contains",
    "file_not_contains",
    "file_regex",
    "file_lines_min",
    "file_min_bytes",
    "file_max_bytes",
    "file_sha256",
    "file_mtime_newer",
}

_MAX_READ_BYTES = 8 * 1024 * 1024


class FileCheckError(ValueError):
    """Raised when a file check cannot be evaluated at all."""


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def parse_file_check_arg(arg: Any) -> Tuple[str, Any]:
    """Normalize the argument of a file check into ``(path, value)``.

    Accepts either the object form ``{"path": ..., "value": ...}`` or the
    shorthand string form ``"path::value"``. The object form is preferred;
    the string form exists so the CLI shorthand stays usable.

    Raises ``FileCheckError`` when the path is missing or the path tries to
    escape the workspace.
    """
    if isinstance(arg, dict):
        path = _as_text(arg.get("path") or arg.get("file") or arg.get("target")).strip()
        value = arg.get("value")
        if value is None:
            value = arg.get("arg")
        if value is None:
            value = arg.get("expect")
        if value is None:
            value = arg.get("contains")
        if value is None:
            value = arg.get("pattern")
        if value is None:
            value = arg.get("min")
        if value is None:
            value = arg.get("max")
        if value is None:
            value = arg.get("sha256")
        if value is None:
            value = arg.get("after")
    elif isinstance(arg, (list, tuple)) and len(arg) >= 1:
        path = _as_text(arg[0]).strip()
        value = arg[1] if len(arg) > 1 else None
    else:
        path = _as_text(arg).strip()
        value = None
        if "::" in path:
            path, _, tail = path.partition("::")
            path = path.strip()
            value = tail
    if not path:
        raise FileCheckError("missing file path")
    return path, value


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path(root: Path, raw_path: str) -> Path:
    """Resolve ``raw_path`` inside ``root``.

    Absolute paths are accepted only when they already live inside the
    workspace - a standard must not be able to point at arbitrary places
    on the disk. ``..`` segments that escape the workspace are rejected.
    """
    root = root.resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not _within(root, resolved):
            raise FileCheckError(f"path escapes the workspace: {raw_path}")
        return resolved
    resolved = (root / candidate).resolve()
    if not _within(root, resolved):
        raise FileCheckError(f"path escapes the workspace: {raw_path}")
    return resolved


def _read_bytes(path: Path) -> bytes:
    size = path.stat().st_size
    if size > _MAX_READ_BYTES:
        raise FileCheckError(f"file too large to check ({size} bytes)")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FileCheckError(f"cannot read file: {exc}") from exc


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _parse_timestamp(value: Any) -> datetime:
    text = _as_text(value).strip()
    if not text:
        raise FileCheckError("missing timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FileCheckError(f"invalid timestamp: {value}") from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def file_check_problem(kind: str, arg: Any) -> str:
    """Return why a file check cannot be evaluated, or "" when usable."""
    if kind not in FILE_CHECK_ARG_REQUIRED:
        return ""
    try:
        _path, value = parse_file_check_arg(arg)
    except FileCheckError as exc:
        return str(exc)
    if kind in FILE_CHECK_VALUE_REQUIRED:
        if value is None or (isinstance(value, str) and not value.strip()):
            return FILE_CHECK_ARG_REQUIRED[kind]
        if kind in {"file_lines_min", "file_min_bytes", "file_max_bytes"}:
            try:
                int(value)
            except (TypeError, ValueError):
                return FILE_CHECK_ARG_REQUIRED[kind]
        if kind == "file_regex":
            try:
                re.compile(str(value))
            except re.error as exc:
                return f"invalid regular expression: {exc}"
        if kind == "file_sha256":
            digest = str(value).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                return "expected a 64-character sha256 hex digest"
        if kind == "file_mtime_newer":
            try:
                _parse_timestamp(value)
            except FileCheckError as exc:
                return str(exc)
    return ""


def evaluate_file_check(
    kind: str,
    arg: Any,
    root: Path,
    overlay: Optional[Dict[str, bytes]] = None,
) -> Dict[str, Any]:
    """Evaluate one file check against the real filesystem.

    Returns a dict shaped like the text checks: ``id`` is filled in by the
    caller, ``pass`` is True/False/None, plus ``note`` and ``source``.
    ``None`` means *unjudged* - the check could not decide.

    ``overlay`` maps an absolute path to the bytes a *candidate* would
    write there. The parallel candidate ranking uses it to score a
    candidate against the file it would actually produce, instead of
    against the old contents of the workspace. The overlay never writes
    anything; it only changes what this check reads.
    """
    try:
        raw_path, value = parse_file_check_arg(arg)
        path = resolve_path(Path(root), raw_path)
    except FileCheckError as exc:
        return {"pass": None, "note": str(exc), "source": "invalid"}

    kind = str(kind).lower()
    overlay = overlay or {}
    overlay_key = str(path)

    def _overlay_bytes() -> Optional[bytes]:
        if overlay_key in overlay:
            return overlay[overlay_key]
        return None

    if kind in {"file_exists", "file_missing"}:
        virtual = _overlay_bytes()
        exists = True if virtual is not None else path.exists()
        if exists and not path.is_file() and virtual is None:
            note = "path exists but is not a file"
            passed = False if kind == "file_exists" else None
            return {"pass": passed, "note": note, "source": "file"}
        if kind == "file_exists":
            if virtual is not None:
                return {"pass": True, "note": f"file exists: {raw_path}", "source": "file"}
            if exists:
                return {"pass": True, "note": f"file exists: {raw_path}", "source": "file"}
            # The file is not there. "file_exists" must fail here - a
            # missing file is the exact case this check exists for, and
            # passing it would close the loop on a file nobody wrote.
            return {"pass": False, "note": f"file not found: {raw_path}", "source": "file"}
        if virtual is not None:
            return {"pass": False, "note": f"file still exists: {raw_path}", "source": "file"}
        if exists:
            return {"pass": False, "note": f"file still exists: {raw_path}", "source": "file"}
        return {"pass": True, "note": f"file is gone: {raw_path}", "source": "file"}

    virtual = _overlay_bytes()
    if virtual is not None:
        data = virtual
    elif not path.exists():
        return {"pass": False, "note": f"file not found: {raw_path}", "source": "file"}
    elif not path.is_file():
        return {"pass": None, "note": f"path is not a file: {raw_path}", "source": "file"}
    else:
        try:
            data = _read_bytes(path)
        except FileCheckError as exc:
            return {"pass": None, "note": str(exc), "source": "invalid"}

    if kind == "file_min_bytes":
        limit = int(value)
        size = len(data)
        passed = size >= limit
        return {
            "pass": passed,
            "note": f"{size} bytes >= {limit}" if passed else f"{size} bytes < {limit}",
            "source": "file",
        }
    if kind == "file_max_bytes":
        limit = int(value)
        size = len(data)
        passed = size <= limit
        return {
            "pass": passed,
            "note": f"{size} bytes <= {limit}" if passed else f"{size} bytes > {limit}",
            "source": "file",
        }
    if kind == "file_sha256":
        digest = hashlib.sha256(data).hexdigest()
        wanted = str(value).strip().lower()
        passed = digest == wanted
        return {
            "pass": passed,
            "note": f"sha256 {digest[:12]}" + ("" if passed else f" != {wanted[:12]}"),
            "source": "file",
        }
    if kind == "file_mtime_newer":
        stamp = _parse_timestamp(value)
        if virtual is not None:
            # The overlay is a file that does not exist on disk yet, so
            # it has no mtime. A candidate that would create the file
            # makes it newer than anything in the past, which is the
            # only defensible answer: the file is being written now.
            mtime = datetime.now(tz=timezone.utc)
        else:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        passed = mtime > stamp
        return {
            "pass": passed,
            "note": f"mtime {mtime.isoformat(timespec='seconds')}"
            + (" is newer" if passed else f" is not newer than {stamp.isoformat(timespec='seconds')}"),
            "source": "file",
        }

    text = _decode(data)
    if kind == "file_contains":
        items = value if isinstance(value, list) else [value]
        items = [_as_text(x) for x in items if x is not None]
        missing = [x for x in items if x not in text]
        passed = not missing
        return {
            "pass": passed,
            "note": "all required strings present" if passed else f"missing: {missing}",
            "source": "file",
        }
    if kind == "file_not_contains":
        items = value if isinstance(value, list) else [value]
        present = [x for x in items if x is not None and x in text]
        passed = not present
        return {
            "pass": passed,
            "note": "no forbidden string found" if passed else f"forbidden string present: {present}",
            "source": "file",
        }
    if kind == "file_regex":
        pattern = str(value)
        matched = re.search(pattern, text, re.DOTALL) is not None
        return {
            "pass": matched,
            "note": f"regex /{pattern}/ matched" if matched else f"regex /{pattern}/ not found",
            "source": "file",
        }
    if kind == "file_lines_min":
        limit = int(value)
        count = len([line for line in text.splitlines() if line.strip()])
        passed = count >= limit
        return {
            "pass": passed,
            "note": f"{count} non-empty lines >= {limit}" if passed else f"{count} non-empty lines < {limit}",
            "source": "file",
        }

    return {"pass": None, "note": f"unknown file check type: {kind}", "source": "invalid"}


def workspace_root(config: Optional[Dict[str, Any]] = None) -> Path:
    """Return the directory that file checks are resolved against.

    The workspace is explicit in the config (``workspace``), and falls back
    to the current working directory so a check written without any
    configuration still works.
    """
    if config:
        raw = config.get("workspace") or config.get("workspace_dir") or config.get("workdir")
        if raw:
            return Path(str(raw))
    return Path.cwd()


def describe_file_checks() -> List[Dict[str, str]]:
    """Return a machine-readable description of the file check types."""
    return [
        {
            "type": name,
            "arg": requirement,
        }
        for name, requirement in FILE_CHECK_ARG_REQUIRED.items()
    ]

"""Small shared helpers: hashing, JSON extraction, timestamps."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Dict, Optional

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_sha(text: str, size: int = 12) -> str:
    return sha256_text(text)[:size]


def ensure_dir(path: str | pathlib.Path) -> pathlib.Path:
    target = pathlib.Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def parse_json_loose(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of a JSON object from a model answer.

    Handles fenced code blocks and prose around the object. Returns None when
    nothing parsable is found - callers must handle that as a failed turn.
    """
    if not text:
        return None
    candidates = []
    match = _FENCE_RE.search(text)
    if match:
        candidates.append(match.group(1).strip())
    candidates.append(text.strip())
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def strip_reasoning(text: str) -> str:
    """Drop reasoning blocks some models emit in front of the real answer.

    Claude-style reasoning models wrap their scratchpad in ``<think>...</think>``.
    That scratchpad is not part of the deliverable, so it must not reach the
    acceptance checks, the artifact preview or the ledger.
    """
    if not text:
        return text
    return _THINK_RE.sub("", text).strip()


def force_utf8_output() -> None:
    """Make stdout/stderr UTF-8 safe on legacy consoles.

    Windows consoles still default to a legacy code page (cp1252, cp936, ...)
    in plenty of environments. Printing Chinese there raises
    ``UnicodeEncodeError`` and kills the process mid-run, which turns a
    healthy loop into a crash. Reconfiguring the streams once at start-up
    keeps the CLI and the MCP server alive on every code page.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass

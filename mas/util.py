"""Small shared helpers: hashing, JSON extraction, timestamps."""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


# --------------------------------------------------------------------------
# Criterion shorthand
#
# ``regex:foo`` on the command line is a *machine check*, not a description.
# Treating it as a description silently disables the check - which is the
# exact failure the JSON criterion form was introduced to fix. The parser
# lives here so the CLI, the MCP server and the HTTP server all agree.
CHECK_SHORTHANDS = {
    "contains",
    "not_contains",
    "min_chars",
    "max_chars",
    "regex",
    "lines_min",
    "section",
    "json_keys",
}

_INT_CHECKS = {"min_chars", "max_chars", "lines_min"}


def parse_criterion_shorthand(text: str) -> Dict[str, Any]:
    """Parse ``<type>:<arg>`` into a criterion carrying a machine check.

    The argument keeps everything after the first colon, so a regular
    expression may contain colons (``regex:https?://``). An *unknown* type
    stays a plain description - it is not a check at all.

    A *known* type with an unusable argument (an empty tail, a
    non-numeric count) keeps its check on purpose: dropping it would
    silently turn "regex:" into a plain description, hand the criterion to
    the model, and burn the whole budget on a standard that was never
    written. The engine validates the argument instead and refuses to
    start.
    """
    head, sep, tail = str(text).partition(":")
    if not sep:
        return {"desc": str(text)}
    kind = head.strip().lower()
    if kind not in CHECK_SHORTHANDS:
        return {"desc": str(text)}
    arg = tail.strip()
    if kind in _INT_CHECKS:
        # An unusable numeric argument is *not* downgraded to a plain
        # description. That would silently drop the machine check and
        # leave the criterion to the model - exactly the failure the
        # validation layer exists to stop. Keep the raw value so the
        # engine can refuse the run before it burns any token.
        with contextlib.suppress(ValueError):
            arg = int(arg)
    return {"desc": str(text), "check": {"type": kind, "arg": arg}}


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
        # A stream that cannot be reconfigured (a closed pipe, a replaced
        # stdout) must not take the CLI down.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


_TRUE_WORDS = {"pass", "passed", "true", "yes", "ok", "success", "satisfied", "meet", "met", "符合", "通过"}
_FALSE_WORDS = {"fail", "failed", "false", "no", "unsatisfied", "violated", "不通过", "未通过", "失败"}


def _as_bool(value: Any) -> Optional[bool]:
    """Best-effort conversion of a model's verdict value into a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        # A model that answers "1" / "0" means the same thing as True /
        # False. Without this, such an answer looked unreadable and the
        # criterion stayed unjudged - the loop then burned an iteration
        # asking the same question again.
        if text in ("1", "true", "yes"):
            return True
        if text in ("0", "false", "no"):
            return False
    return None


def normalize_verdict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a reviewer answer into the canonical verdict schema.

    The engine expects::

        {"verdict": "pass" | "fail",
         "scores": [{"id": str, "pass": bool, "note": str}]}

    Strong models answer with their own shape instead - ``overall_pass``
    plus a ``criteria`` list, or ``passed``/``result`` fields. When that
    happened the review was silently dropped: the aggregate saw zero
    usable verdicts and the loop relied on the machine checks alone.

    This function accepts the common variants and returns the canonical
    shape. The original payload is kept under ``raw`` so nothing is lost.
    """
    if not isinstance(payload, dict):
        return payload
    if payload.get("scores") and isinstance(payload["scores"], list):
        # Already canonical - but normalise the individual score entries.
        scores = []
        for item in payload["scores"]:
            if not isinstance(item, dict):
                continue
            passed = _as_bool(item.get("pass"))
            if passed is None:
                passed = _as_bool(item.get("passed"))
            if passed is None:
                passed = _as_bool(item.get("result"))
            if passed is None:
                passed = _as_bool(item.get("status"))
            score = dict(item)
            score["id"] = str(item.get("id") or item.get("criterion") or item.get("name") or "")
            if passed is not None:
                score["pass"] = passed
            score.setdefault("note", str(item.get("note") or item.get("reason") or item.get("explanation") or ""))
            scores.append(score)
        result = dict(payload)
        result["scores"] = scores
        if not result.get("verdict"):
            result["verdict"] = "pass" if scores and all(s.get("pass") for s in scores) else "fail"
        return result

    # Alternative shape: {"criteria": [...]} or {"results": [...]}.
    raw_scores = None
    for key in ("criteria", "results", "checks", "items", "findings"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            raw_scores = value
            break

    if raw_scores is None:
        return payload

    scores: List[Dict[str, Any]] = []
    for item in raw_scores:
        if not isinstance(item, dict):
            continue
        passed = _as_bool(item.get("pass"))
        if passed is None:
            passed = _as_bool(item.get("passed"))
        if passed is None:
            passed = _as_bool(item.get("result"))
        if passed is None:
            passed = _as_bool(item.get("status"))
        if passed is None:
            passed = _as_bool(item.get("outcome"))
        if passed is None:
            passed = _as_bool(item.get("satisfied"))
        if passed is None:
            passed = _as_bool(item.get("verdict"))
        if passed is None:
            passed = _as_bool(item.get("overall_pass"))
        note = (
            item.get("note")
            or item.get("reason")
            or item.get("explanation")
            or item.get("justification")
            or item.get("analysis")
            or ""
        )
        if isinstance(note, list):
            note = "; ".join(str(part) for part in note)
        scores.append(
            {
                "id": str(item.get("id") or item.get("criterion") or item.get("name") or ""),
                "pass": bool(passed),
                "note": str(note),
            }
        )

    if not scores:
        return payload

    overall = _as_bool(payload.get("overall_pass"))
    if overall is None:
        overall = _as_bool(payload.get("passed"))
    if overall is None:
        overall = _as_bool(payload.get("verdict"))
    if overall is None:
        overall = all(score["pass"] for score in scores)

    result = dict(payload)
    result["scores"] = scores
    result["verdict"] = "pass" if overall else "fail"
    result.setdefault("issues", [
        str(item.get("required_fix") or item.get("reason") or "") for item in raw_scores
        if isinstance(item, dict) and not _as_bool(item.get("pass", item.get("result")))
    ])
    return result

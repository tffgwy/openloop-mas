# -*- coding: utf-8 -*-
"""Say the verdict in words, not in engine vocabulary.

The engine records *why* a criterion passed or failed in a note. Those
notes are written for the machine: ``all required strings present``,
``missing: ['\u7ed3\u8bba']``, ``length 42 < 80``, ``no machine check``.

They end up on the screen right next to the user's own standards:

    [PASS] c1 (rule) all required strings present

The reader sees an English sentence that describes the *rule*, not the
*standard*. And the rule is already printed underneath as the reading
("the body must contain \u7ed3\u8bba"). So the line says the same thing
twice, in a language the user did not write in.

This module translates the note. Two rules govern it:

* **The ledger keeps its English.** The notes are part of the audit
  record and scripts read them; translating them in place would change
  what a stored run means. So the translation happens at the display
  layer only.
* **Never invent a verdict.** An unknown note is left alone. A note the
  module does not recognise may carry information this module cannot
  see, and replacing it with "could not be judged" would throw that
  away. An untranslated note is still a true note.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from mas.criteria_text import criterion_reading

#: How the run ended, in words.
STATUS_READING: Dict[str, str] = {
    "closed": "\u5df2\u95ed\u73af",
    "budget_exhausted": "\u9884\u7b97\u7528\u5b8c\u4e86\uff08\u6ca1\u8dd1\u5b8c\uff0c\u5df2\u505c\u4e0b\u6765\u4f60\u63a5\u624b\uff09",
    "max_iterations": "\u8f6e\u6570\u7528\u5b8c\u4e86",
    "timeout": "\u5230\u4e86\u65f6\u95f4\u9884\u7b97",
    "error": "\u540e\u7aef\u62a5\u9519",
    "failed": "\u6ca1\u8dd1\u6210",
    "cancelled": "\u5df2\u53d6\u6d88",
    "running": "\u8fd0\u884c\u4e2d",
    "pending": "\u7b49\u5f85\u4e2d",
    # The states the engine actually writes when a run does not close.
    # Without them the report fell back to the raw value, so the one
    # line a reader scans to see "what went wrong" was in English.
    "escalated": "\u5df2\u4ea4\u7ed9\u4eba\u5de5",
    "not_closed": "\u6ca1\u95ed\u73af",
    "incomplete": "\u6ca1\u8dd1\u5b8c",
    "deliverable_ok_but_not_closed": "\u4ea4\u4ed8\u7269\u5408\u683c\uff0c\u4f46\u6ca1\u95ed\u73af",
}

#: What the loop decided to do next, in words.
DECISION_READING: Dict[str, str] = {
    "close": "\u95ed\u73af\u4e86",
    "revise": "\u8981\u6539\u4e00\u7248",
    "escalate": "\u4ea4\u7ed9\u4eba\u5de5",
    "stop": "\u505c\u4e0b\u4e86",
}

#: Where a verdict came from, in words.
SOURCE_READING: Dict[str, str] = {
    "rule": "\u673a\u68c0",
    "file": "\u6587\u4ef6\u68c0\u67e5",
    "review": "\u8bc4\u5ba1",
    "lead": "\u4e3b\u7ebf",
    "none": "\u65e0\u673a\u68c0",
    "invalid": "\u89c4\u5219\u6709\u95ee\u9898",
    "human": "\u4eba\u5de5",
    # The sources the engine writes when a criterion could not be
    # decided by a machine check.
    "model-missing": "\u8bc4\u5ba1\u6ca1\u7ed9\u51fa\u5224\u5b9a",
    "skipped": "\u5df2\u8df3\u8fc7",
    "model": "\u6a21\u578b\u5224\u5b9a",
}


#: Which agent did what, in words. The ledger records the role under
#: its internal name (``planner``, ``write``, ``closure``); a reader
#: scanning the ledger sees a column of English identifiers and has to
#: look them up to learn that ``closure`` is the line that says
#: whether the run passed.
ROLE_READING: Dict[str, str] = {
    "planner": "规划",
    "plan": "规划",
    "write": "执行",
    "work": "执行",
    "review": "评审",
    "verify": "验证",
    "closure": "验收",
    "reflector": "复盘",
    "reflect": "复盘",
    "candidates": "候选方案",
    "lead": "主线",
    "reask": "补问",
    "reask_failed": "补问失败",
    "truncated": "输出被截断",
    "error": "出错",
    "run_end": "收尾",
    "artifact": "交付物",
}


def role_reading(role: Any) -> str:
    """The plain reading of a role, or "" when unknown."""
    key = str(role or "").strip()
    if not key:
        return ""
    return ROLE_READING.get(key, key)


def status_reading(status: Any) -> str:
    """The plain reading of a run status, or "" when unknown."""
    key = str(status or "").strip()
    if not key:
        return ""
    return STATUS_READING.get(key, key)


def decision_reading(decision: Any) -> str:
    """The plain reading of a decision, or "" when unknown."""
    key = str(decision or "").strip()
    if not key:
        return ""
    return DECISION_READING.get(key, key)


def source_reading(source: Any) -> str:
    """The plain reading of a verdict source, or "" when unknown."""
    key = str(source or "").strip()
    if not key:
        return ""
    return SOURCE_READING.get(key, key)


#: Notes that are a whole sentence on their own.
_EXACT: Dict[str, str] = {
    "all required strings present": "\u6240\u6709\u8981\u6c42\u7684\u5b57\u7b26\u4e32\u90fd\u5728",
    "no forbidden string found": "\u6ca1\u6709\u51fa\u73b0\u7981\u6b62\u7684\u5b57\u7b26\u4e32",
    "no machine check": "\u8fd9\u6761\u6807\u51c6\u6ca1\u6709\u673a\u68c0\u89c4\u5219",
    "optional criterion": "\u53ef\u9009\u6807\u51c6\uff0c\u4e0d\u963b\u585e\u95ed\u73af",
    "all keys present": "\u8981\u6c42\u7684\u952e\u90fd\u5728",
    "artifact is not a JSON object": "\u4ea7\u7269\u4e0d\u662f\u4e00\u4e2a JSON \u5bf9\u8c61",
}

#: Notes of the shape ``<prefix>: <list>``.
_LIST_NOTE = re.compile(r"^(missing|forbidden string present|missing keys)\s*:\s*(.+)$")
#: Notes of the shape ``length <n> <op> <limit>``.
_LENGTH_NOTE = re.compile(r"^length\s+(\d+)\s*(>=|<=|<|>)\s*(\d+)$")
#: Notes of the shape ``<n> non-empty lines <op> <limit>``.
_LINES_NOTE = re.compile(r"^(\d+)\s+non-empty lines\s*(>=|<)\s*(\d+)$")
#: Notes of the shape ``regex /<pattern>/ matched`` / ``... not found``.
_REGEX_NOTE = re.compile(r"^regex\s+/(?P<pattern>.*)/\s*(?P<state>matched|not found)$", re.DOTALL)
#: Notes of the shape ``section '<marker>' found`` / ``missing``.
_SECTION_NOTE = re.compile(r"^section\s+'(?P<marker>.*)'\s*(?P<state>found|missing)$", re.DOTALL)
#: Notes of the shape ``<n> bytes <op> <limit>``.
_BYTES_NOTE = re.compile(r"^(\d+)\s+bytes\s*(>=|<|<=|>)\s*(\d+)$")
#: Notes of the shape ``sha256 <digest>`` or ``<digest> != <digest>``.
_SHA_NOTE = re.compile(r"^sha256\s+(?P<left>[0-9a-f]{6,})\s*(?P<op>!=)?\s*(?P<right>[0-9a-f]{6,})?$")
#: Notes of the shape ``mtime <stamp> <op> <stamp>``.
_MTIME_NOTE = re.compile(r"^mtime\s+(?P<left>\S+)\s+(?P<op>is not newer than|is newer than|>=|<)\s+(?P<right>\S+)$")
#: Notes of the shape ``file exists: <path>`` and friends.
_FILE_NOTE = re.compile(r"^file (?P<state>exists|not found|still exists|is gone)\s*:\s*(?P<path>.+)$")


def _quote_list(raw: str) -> str:
    """Render a Python list of strings as a readable Chinese list."""
    items = re.findall(r"'([^']*)'", raw)
    if not items:
        return raw
    return "\u3001".join(items)


def _note_text(note: Any) -> str:
    return str(note or "").strip()


def note_reading(note: Any) -> str:
    """The plain reading of a machine note.

    An empty string means "this note could not be translated" - the
    caller should keep the original text. Nothing is invented here: a
    note this module does not recognise stays exactly as it was.
    """
    text = _note_text(note)
    if not text:
        return ""

    if text in _EXACT:
        return _EXACT[text]

    match = _LIST_NOTE.match(text)
    if match:
        kind = match.group(1)
        items = _quote_list(match.group(2))
        if kind == "missing":
            return "\u7f3a\u5c11\uff1a" + items
        if kind == "forbidden string present":
            return "\u51fa\u73b0\u4e86\u7981\u6b62\u7684\u5b57\u7b26\u4e32\uff1a" + items
        return "\u7f3a\u5c11\u7684\u952e\uff1a" + items

    match = _LENGTH_NOTE.match(text)
    if match:
        length, op, limit = match.group(1), match.group(2), match.group(3)
        if op == ">=":
            return "\u6b63\u6587 {0} \u5b57\uff0c\u4e0d\u5c11\u4e8e {1} \u5b57".format(length, limit)
        if op == "<=":
            return "\u6b63\u6587 {0} \u5b57\uff0c\u4e0d\u591a\u4e8e {1} \u5b57".format(length, limit)
        # The comparison says which side the body fell on. A body
        # of 48 characters against a limit of 999999 is too short,
        # not too long - and the old wording said the opposite,
        # sending the reader after the wrong problem.
        if op == "<":
            return "\u6b63\u6587\u53ea\u6709 {0} \u5b57\uff0c\u4e0d\u5230 {1} \u5b57".format(length, limit)
        return "\u6b63\u6587\u6709 {0} \u5b57\uff0c\u8d85\u8fc7\u4e86 {1} \u5b57".format(length, limit)

    match = _LINES_NOTE.match(text)
    if match:
        count, op, limit = match.group(1), match.group(2), match.group(3)
        if op == ">=":
            return "\u6b63\u6587\u6709 {0} \u884c\u6709\u6548\u5185\u5bb9\uff0c\u4e0d\u5c11\u4e8e {1} \u884c".format(count, limit)
        return "\u6b63\u6587\u53ea\u6709 {0} \u884c\u6709\u6548\u5185\u5bb9\uff0c\u4e0d\u8db3 {1} \u884c".format(count, limit)

    match = _REGEX_NOTE.match(text)
    if match:
        pattern = match.group("pattern")
        if match.group("state") == "matched":
            return "\u6b63\u6587\u5339\u914d\u4e86\u6b63\u5219 /{0}/".format(pattern)
        return "\u6b63\u6587\u6ca1\u5339\u914d\u5230\u6b63\u5219 /{0}/".format(pattern)

    match = _SECTION_NOTE.match(text)
    if match:
        marker = match.group("marker")
        if match.group("state") == "found":
            return "\u6b63\u6587\u91cc\u6709\u300c{0}\u300d\u8fd9\u4e00\u8282".format(marker)
        return "\u6b63\u6587\u91cc\u6ca1\u6709\u300c{0}\u300d\u8fd9\u4e00\u8282".format(marker)

    match = _BYTES_NOTE.match(text)
    if match:
        size, op, limit = match.group(1), match.group(2), match.group(3)
        if op == ">=":
            return "\u6587\u4ef6 {0} \u5b57\u8282\uff0c\u4e0d\u5c11\u4e8e {1} \u5b57\u8282".format(size, limit)
        if op == "<=":
            return "\u6587\u4ef6 {0} \u5b57\u8282\uff0c\u4e0d\u591a\u4e8e {1} \u5b57\u8282".format(size, limit)
        # Same rule as the length note: the comparison says which
        # side the file fell on.
        if op == "<":
            return "\u6587\u4ef6 {0} \u5b57\u8282\uff0c\u4e0d\u5230 {1} \u5b57\u8282".format(size, limit)
        return "\u6587\u4ef6 {0} \u5b57\u8282\uff0c\u8d85\u8fc7\u4e86 {1} \u5b57\u8282".format(size, limit)

    match = _SHA_NOTE.match(text)
    if match:
        left = match.group("left")
        if match.group("op") == "!=" and match.group("right"):
            return "\u6587\u4ef6\u7684 sha256 \u662f {0}\uff0c\u4e0d\u662f {1}".format(left, match.group("right"))
        return "\u6587\u4ef6\u7684 sha256 \u662f {0}".format(left)

    match = _MTIME_NOTE.match(text)
    if match:
        left, op, right = match.group("left"), match.group("op"), match.group("right")
        if op == "is not newer than":
            return "\u6587\u4ef6\u7684\u4fee\u6539\u65f6\u95f4 {0} \u4e0d\u665a\u4e8e {1}".format(left, right)
        if op == "is newer than":
            return "\u6587\u4ef6\u7684\u4fee\u6539\u65f6\u95f4 {0} \u665a\u4e8e {1}".format(left, right)
        return "\u6587\u4ef6\u7684\u4fee\u6539\u65f6\u95f4 {0} {1} {2}".format(left, op, right)

    match = _FILE_NOTE.match(text)
    if match:
        state = match.group("state")
        path = match.group("path")
        if state == "exists":
            return "\u6587\u4ef6\u5b58\u5728\uff1a{0}".format(path)
        if state == "not found":
            return "\u627e\u4e0d\u5230\u6587\u4ef6\uff1a{0}".format(path)
        if state == "still exists":
            return "\u6587\u4ef6\u8fd8\u5728\uff1a{0}".format(path)
        return "\u6587\u4ef6\u5df2\u5220\u9664\uff1a{0}".format(path)

    # Notes that say what went wrong, in the engine's own words.
    for prefix, reading in (
        ("unjudged: ", "\u6ca1\u6709\u4eba\u5224\u5b9a"),
        ("unknown check type: ", "\u4e0d\u8ba4\u8bc6\u7684\u68c0\u67e5\u7c7b\u578b\uff1a"),
        ("unknown file check type: ", "\u4e0d\u8ba4\u8bc6\u7684\u6587\u4ef6\u68c0\u67e5\u7c7b\u578b\uff1a"),
        ("check error: ", "\u68c0\u67e5\u51fa\u9519\uff1a"),
    ):
        if text.startswith(prefix):
            return reading + text[len(prefix):]

    # "path is not a file: <path>"
    if text.startswith("path is not a file: "):
        return "\u8fd9\u4e0d\u662f\u4e00\u4e2a\u6587\u4ef6\uff1a" + text[len("path is not a file: "):]

    return ""


#: The sentence ``criterion_reading`` uses when a standard has
#: no description of its own. It is a placeholder, not a
#: description - see ``issue_reading`` below.
_NO_DESCRIPTION = "\uff08\u8fd9\u6761\u6807\u51c6\u6ca1\u6709\u5199\u6587\u5b57\u8bf4\u660e\uff09"


def issue_reading(issue: Any, criteria: Optional[List[Dict[str, Any]]] = None) -> str:
    """Say what a blocking issue is, in words.

    The issue reads ``c1: unjudged: ...`` - a serial number the engine
    made up, followed by a note in the engine's vocabulary. Both are
    replaced: the note is translated, and the serial number is looked
    up in the criteria so the user's own standard can be named.

    An issue this module cannot read keeps its own text. The criteria
    are optional: without them the note is still translated.
    """
    raw = str(issue or "").strip()
    if not raw:
        return ""
    score_id = ""
    note = raw
    head, sep, tail = raw.partition(":")
    if sep and re.fullmatch(r"c\d+", head.strip()):
        score_id = head.strip()
        note = tail.strip()
    reading = note_reading(note)
    if not reading:
        # An untranslatable note keeps its own text - it may carry
        # information this module cannot see.
        reading = note
    for criterion in criteria or []:
        if not isinstance(criterion, dict):
            continue
        if str(criterion.get("id") or "") != score_id:
            continue
        # The criterion's own description, in words. A criterion
        # written as a bare shorthand carries the raw text as its
        # description, so the raw text is used - and the reading says
        # the same thing in a language the user can read.
        desc = criterion_reading(criterion)
        # ``criterion_reading`` answers "this standard has no
        # description" with a placeholder sentence. That placeholder
        # is not a description of the standard, and putting it in
        # front of the issue would bury the one thing the issue
        # says: what actually went wrong. A standard without a
        # description falls back to the plain note, with its serial
        # number still attached.
        if desc and not desc.startswith(_NO_DESCRIPTION):
            return f"{desc} - {reading}"
        break
    if score_id:
        # The serial number is part of the issue: it points at the
        # standard the reader wrote. Dropping it leaves the reader
        # with a sentence and no way to find the standard it is
        # about.
        return f"{score_id}: {reading}"
    return reading


def verdict_text(score: Optional[Dict[str, Any]]) -> str:
    """The whole verdict line, in words.

    Assembles the note, the source and the optional marker into one
    sentence a person can read. Returns "" when there is nothing to
    say, so the caller can decide what to print.
    """
    if not isinstance(score, dict):
        return ""
    parts: List[str] = []
    source = source_reading(score.get("source"))
    if source:
        parts.append(source)
    reading = note_reading(score.get("note"))
    if reading:
        parts.append(reading)
    if not parts:
        return ""
    return "\uff08".join(parts) + "\uff09" if len(parts) == 1 else " ".join(parts)

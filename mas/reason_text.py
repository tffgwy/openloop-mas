# -*- coding: utf-8 -*-
"""Say why the run did not close, in words.

A run that does not close carries a reason, and the reason is the one
line the human actually needs:

    [FAIL] closure  未闭环（状态 budget_exhausted）：budget: reached max_iterations=1
    - 未闭合原因：budget: reached max_iterations=1
    原因：budget: reached max_iterations=1

``budget: reached max_iterations=1`` is the engine talking to itself.
It says *that* something happened, not *what* happened: not that the
loop used up its rounds, not how many rounds it had, and not what to
do about it. A reader who does not know what ``max_iterations`` is
learns nothing at all from the line.

This module turns the reason into words. The same two rules as the
verdict translation apply:

* **The ledger keeps its English.** The reason is part of the audit
  record; the translation happens at the display layer only.
* **Never invent a reason.** An unrecognised reason is left alone.
  It may carry information this module cannot see - a provider error
  message, a custom error - and replacing it would throw that away.

The vocabulary here is taken from the engine, not from the outside.
Every reason the engine can write is listed in ``ENGINE_REASONS``;
``tests/test_reason_reading.py`` checks that list against the engine
source, so a new reason cannot silently lose its reading.
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from mas.verdict_text import issue_reading, status_reading

#: The reasons that carry a number, matched by a regular expression.
#: Each entry is (pattern, template). The template receives the named
#: groups of the pattern.
_NUMBERED: List[Tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"^budget: reached max_iterations=(?P<n>\d+)$"),
        "\u9884\u7b97\u7528\u5b8c\u4e86\uff1a\u8fd9\u8f6e\u53ea\u5141\u8bb8\u8dd1 {n} \u8f6e\uff0c"
        "\u5df2\u7ecf\u8dd1\u6ee1\u4e86\u3002",
        "\u8981\u7ee7\u7eed\u52a0\u8f6e\u6570\uff1amas init --max-iterations <\u66f4\u5927\u7684\u6570>",
    ),
    (
        re.compile(r"^budget: exceeded deadline_sec=(?P<n>\d+)$"),
        "\u9884\u7b97\u7528\u5b8c\u4e86\uff1a\u8fd9\u8f6e\u53ea\u5141\u8bb8\u7528 {n} \u79d2\uff0c"
        "\u65f6\u95f4\u5230\u4e86\u3002",
        "\u8981\u7ee7\u7eed\u52a0\u65f6\u95f4\uff1amas init --deadline-sec <\u66f4\u5927\u7684\u6570>",
    ),
    (
        re.compile(r"^budget: tokens (?P<used>\d+) >= (?P<limit>\d+)$"),
        "\u9884\u7b97\u7528\u5b8c\u4e86\uff1a\u8fd9\u8f6e\u53ea\u5141\u8bb8\u7528 {limit} \u4e2a token\uff0c"
        "\u5df2\u7ecf\u82b1\u4e86 {used} \u4e2a\u3002",
        "\u8981\u7ee7\u7eed\u52a0\u989d\u5ea6\uff1amas init --max-tokens <\u66f4\u5927\u7684\u6570>",
    ),
    (
        re.compile(
            r"^budget: exceeded deadline_sec=(?P<n>\d+) "
            r"\(the deliverable passed the checks, but the run "
            r"overran its wall-clock budget\)$"
        ),
        "\u9884\u7b97\u7528\u5b8c\u4e86\uff1a\u4ea4\u4ed8\u7269\u672c\u8eab\u662f\u5408\u683c\u7684\uff0c"
        "\u4f46\u8fd9\u8f6e\u8dd1\u8d85\u8fc7\u4e86 {n} \u79d2\u7684\u65f6\u95f4\u9884\u7b97\u3002",
        "\u4ea4\u4ed8\u7269\u53ef\u4ee5\u76f4\u63a5\u53d6\u7528\uff1b\u8981\u8ba9\u8fd0\u884c\u81ea\u5df1\u95ed\u73af\uff0c"
        "\u5c31\u52a0\u65f6\u95f4\uff1amas init --deadline-sec <\u66f4\u5927\u7684\u6570>",
    ),
    (
        re.compile(
            r"^stagnation: the deliverable did not change for "
            r"(?P<count>\d+) consecutive attempts "
            r"\(stagnation_limit=(?P<limit>\d+)\)$"
        ),
        "\u505c\u6ede\uff1a\u4ea4\u4ed8\u7269\u8fde\u7eed {count} \u6b21\u6ca1\u6709\u53d8\u5316"
        "\uff08\u4e0a\u9650 {limit} \u6b21\uff09\uff0c\u505c\u4e0b\u6765\u6362\u4e2a\u4eba\u6765\u5b9a\u3002",
        "\u8981\u7ee7\u7eed\u52a0\u6b21\u6570\uff1amas init --stagnation-limit <\u66f4\u5927\u7684\u6570>",
    ),
]

#: The reasons that are a prefix plus free text. The free text is
#: information this module cannot interpret, so it is kept verbatim -
#: with one exception: a number that the engine wrote as part of the
#: reason itself is put into the sentence where it belongs.
_PREFIXED: List[Tuple[str, str]] = [
    ("provider error: ", "\u540e\u7aef\u62a5\u9519\uff1a"),
    ("internal error: ", "\u5185\u90e8\u51fa\u9519\uff1a"),
    ("truncated answer: ", "\u56de\u7b54\u88ab\u622a\u65ad\uff1a"),
    ("loop could not close itself", "\u5faa\u73af\u81ea\u5df1\u6ca1\u80fd\u95ed\u73af\u3002"),
    ("the run ran out of budget", "\u8fd9\u8f6e\u7684\u9884\u7b97\u7528\u5b8c\u4e86\u3002"),
    ("the run failed", "\u8fd9\u8f6e\u8dd1\u5931\u8d25\u4e86\u3002"),
]

#: The wall-clock reason carries the seconds the call ran for.
_WALL_CLOCK = re.compile(
    r"^budget: wall-clock budget exhausted after (?P<sec>\d+)s?$"
)

#: The resume reason carries the status the resumed run ended in.
_RESUME = re.compile(
    r"^resume did not close the loop: status=(?P<status>[a-z_]+)(?P<tail>.*)$"
)

#: A bare ``budget: ...`` - the stop-loss mechanism firing inside a
#: provider call. The message after it is the engine's own.
_BARE_BUDGET = re.compile(r"^budget: (?P<rest>.+)$")


def _read_tail(tail: str) -> str:
    """Translate the parts of a tail that are reasons in their own right.

    A resume reason is built from parts: the status, then the reason
    the resumed run ended with, then the issues. The status is
    translated by the caller; the reason in the middle is a reason
    like any other, and the issues are notes. Leaving them in English
    in the middle of a Chinese sentence means the reader still has to
    decode them.
    """
    text = tail.strip()
    if not text:
        return ""
    # The tail starts with a comma; drop it and keep the pieces.
    if text.startswith(","):
        text = text[1:].strip()
    if not text:
        return ""
    # ``issues: ...`` is the last piece and carries the notes.
    head, sep, issues_tail = text.partition(", issues: ")
    if not sep:
        head, sep, issues_tail = text.partition(", issues:")
    reading = reason_reading(head) if sep else reason_reading(text)
    if not reading:
        reading = head if sep else text
    if sep:
        issues = [i.strip() for i in issues_tail.split(";") if i.strip()]
        if issues:
            named = "; ".join(issue_reading(i) for i in issues)
            return reading + ", " + named
    return reading


def _reason_text(reason: Any) -> str:
    return str(reason or "").strip()


def reason_reading(reason: Any) -> str:
    """The plain reading of a reason, or "" when unknown.

    An empty string means "this reason could not be translated" - the
    caller keeps the original text. Nothing is invented here.
    """
    text = _reason_text(reason)
    if not text:
        return ""

    for pattern, sentence, _hint in _NUMBERED:
        match = pattern.match(text)
        if match:
            return sentence.format(**match.groupdict())

    match = _WALL_CLOCK.match(text)
    if match:
        seconds = match.group("sec")
        return (
            "\u9884\u7b97\u7528\u5b8c\u4e86\uff1a\u8fd9\u8f6e\u53ea\u5141\u8bb8\u7528 "
            + seconds
            + " \u79d2\uff0c\u540e\u7aef\u8c03\u7528\u8d85\u65f6\uff0c\u65f6\u95f4\u5230\u4e86\u3002"
        )

    match = _RESUME.match(text)
    if match:
        # The status is the part a reader can act on, so it is
        # translated; the raw value stays visible so a reader can
        # grep for it.
        status = match.group("status")
        reading = status_reading(status)
        if reading and reading != status:
            status = reading + "\uff08" + status + "\uff09"
        tail = match.group("tail")
        return (
            "\u7eed\u8dd1\u4e4b\u540e\u4ecd\u672a\u95ed\u73af\uff1a"
            + status
            + _read_tail(tail)
        )

    for prefix, reading in _PREFIXED:
        if text.startswith(prefix):
            rest = text[len(prefix):].strip()
            return reading + rest

    match = _BARE_BUDGET.match(text)
    if match:
        return "\u9884\u7b97\u7528\u5b8c\u4e86\uff1a" + match.group("rest")

    return ""


def reason_hint(reason: Any) -> str:
    """The command that would make the reason go away, or "".

    A reason that names a ceiling is only half a story: the reader
    also needs to know which knob to turn. The hint is kept apart
    from the sentence so a one-line display (a ticket queue, a
    table cell) stays one line - the sentence says what happened,
    the hint says what to do.
    """
    text = _reason_text(reason)
    if not text:
        return ""
    for pattern, _sentence, hint in _NUMBERED:
        if pattern.match(text):
            return hint
    if _WALL_CLOCK.match(text):
        return (
            "\u8981\u7ee7\u7eed\u52a0\u65f6\u95f4\uff1amas init --deadline-sec <\u66f4\u5927\u7684\u6570>"
        )
    return ""


def reason_text(reason: Any) -> str:
    """The reading of a reason, falling back to the original text.

    This is the function a display layer wants: it always returns
    something to print. An unknown reason keeps its own text - the
    reading is never empty in a line a human reads.
    """
    text = _reason_text(reason)
    if not text:
        return ""
    return reason_reading(text) or text

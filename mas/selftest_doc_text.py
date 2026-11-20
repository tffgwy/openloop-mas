# -*- coding: utf-8 -*-
"""The thirty-fifth gate: the documents must still be *text*.

The gap
-------
While writing the v0.7.93 release notes, the text was produced through
a shell that treats a backquote as an **escape character**. So
`` `budget `` became a backspace, `` `tickets `` became a tab, ``
`reason_text `` became a carriage return followed by `` eason_text ``,
and a fenced block came out as `` ``bash ``.

The damage was small and it was invisible in a rendered Markdown
page: the sentence still read, the code block still looked like a
code block. But the **file on the disk was corrupted** - a control
character where a letter should be, a tab where an indent should be -
and it shipped, because nothing was looking.

That is the point. A document is the thing a beginner reads. A
document with a backspace in it is not a document; it is a file that
happens to be mostly text. And the project's own rule is that a
**capability nobody checks is a capability nobody has** - which is why
every other hole in this project got a gate. This one was waiting for
its gate too.

So this gate reads the documents - the READMEs, the changelog and
the ``docs/`` folder, both in the repository and in the installed
wheel - and it checks that they are still text:

* **no control characters** - a backspace or a bare carriage return
  inside a line means something ate a character;
* **no stray tabs** in the prose - a tab where an indent should be;
* **code fences are intact** - an opening fence names a language (or
  nothing), a closing fence names nothing, and every fence is closed.

And the gate proves it can fail: the documents are corrupted on
purpose - a backspace is injected into one of them - and the gate has
to notice that it can no longer tell a text file from a mangled one.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import tempfile
from typing import Any, Dict, List, Tuple

#: The documents the gate reads, by name, next to the folder they
#: live in. ``""`` means "the folder the package or repository is in".
DOCUMENT_NAMES = (
    "README.md",
    "README.en.md",
    "CHANGELOG.md",
)

#: The folders whose Markdown the gate reads.
DOCUMENT_FOLDERS = ("docs",)

#: The observations the gate makes.
DOC_TEXT_OBSERVATIONS = (
    "the documents carry no control characters",
    "the documents carry no stray tabs",
    "the code fences are intact",
)

_FENCE = re.compile(r"^\s*```(.*)$")
_LANGUAGE = re.compile(r"^[A-Za-z0-9_+.\-]+$")


def _search_roots() -> List[pathlib.Path]:
    """The places the documents may live.

    A repository has them in its root and in ``docs/``. An installed
    wheel has them in ``mas/docs/`` - the folder that travels with the
    package. The gate has to work in both places, because the whole
    point is that a beginner who only ever ran ``pip install`` gets
    the same guarantee as a contributor.
    """
    package = pathlib.Path(__file__).resolve().parent
    roots: List[pathlib.Path] = []

    repository = package.parent
    if (repository / "README.md").exists():
        roots.append(repository)

    packaged = package / "docs"
    if packaged.is_dir():
        roots.append(packaged)

    if not roots:
        roots.append(package)
    return roots


def _document_paths() -> List[pathlib.Path]:
    """Every document the gate reads."""
    paths: List[pathlib.Path] = []
    seen = set()
    for root in _search_roots():
        for name in DOCUMENT_NAMES:
            candidate = root / name
            if candidate.exists() and candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
        for folder in DOCUMENT_FOLDERS:
            directory = root / folder
            if not directory.is_dir():
                continue
            for candidate in sorted(directory.glob("*.md")):
                if candidate not in seen:
                    seen.add(candidate)
                    paths.append(candidate)
    return paths


def _lines(text: str) -> List[str]:
    """The lines of a document, with the endings normalised.

    The text has to arrive as **bytes**. ``read_text`` applies the
    universal-newline conversion, which turns a stray carriage return
    into a line break - and a stray carriage return is exactly the
    thing this gate is looking for. So the caller decodes the bytes
    itself and hands them over unchanged.

    A carriage return **in front of** a line feed is a line ending
    and is left alone; a carriage return on its own is not text and is
    kept where it is, so the control-character check can find it.
    """
    return text.replace("\r\n", "\n").split("\n")


def _control_problems(text: str) -> List[Tuple[int, str]]:
    """The control characters inside a document.

    A carriage return on its own is a control character: it is not a
    line ending in this project, it is a character that was eaten and
    replaced by an escape. A carriage return in front of a line feed
    is a line ending and never reaches this check - ``_lines``
    normalises it away.
    """
    problems: List[Tuple[int, str]] = []
    for number, line in enumerate(_lines(text), 1):
        for char in line:
            code = ord(char)
            if code < 32 and char != "\t":
                problems.append((number, "control character U+%04X" % code))
    return problems


def _tab_problems(text: str) -> List[Tuple[int, str]]:
    """The tabs in the prose of a document.

    A tab inside a fenced block may well be a literal tab the author
    wanted to show. A tab in the prose is a character that was eaten
    and replaced by an escape.
    """
    problems: List[Tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(_lines(text), 1):
        if _FENCE.match(line.rstrip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "\t" in line:
            problems.append((number, "tab character"))
    return problems


def _fence_problems(text: str) -> List[Tuple[int, str]]:
    """The broken code fences inside a document."""
    problems: List[Tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(_lines(text), 1):
        match = _FENCE.match(line.rstrip())
        if not match:
            continue
        info = match.group(1).strip()
        if not in_fence:
            in_fence = True
            if info and not _LANGUAGE.match(info):
                problems.append(
                    (number, "code fence names %r instead of a language" % info)
                )
        else:
            if info:
                problems.append(
                    (number, "closing code fence carries the text %r" % info)
                )
            in_fence = False
    if in_fence:
        problems.append((0, "a code fence is never closed"))
    return problems


def _document_facts(paths: List[pathlib.Path]) -> Dict[str, Any]:
    """Read the documents and collect what is wrong with them."""
    control: List[str] = []
    tabs: List[str] = []
    fences: List[str] = []
    read: List[str] = []
    for path in paths:
        # Read the bytes and decode them here: ``read_text`` would
        # apply the universal-newline conversion and silently turn a
        # stray carriage return into a line break.
        text = path.read_bytes().decode("utf-8", errors="replace")
        read.append(path.name)
        for number, why in _control_problems(text):
            control.append("%s:%d %s" % (path.name, number, why))
        for number, why in _tab_problems(text):
            tabs.append("%s:%d %s" % (path.name, number, why))
        for number, why in _fence_problems(text):
            fences.append("%s:%d %s" % (path.name, number, why))
    return {
        "read": read,
        "control": control,
        "tabs": tabs,
        "fences": fences,
    }


def _document_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The reading of the documents."""
    return {
        "the documents carry no control characters": not facts.get("control"),
        "the documents carry no stray tabs": not facts.get("tabs"),
        "the code fences are intact": not facts.get("fences"),
    }


def check_the_documents_are_still_text(
    iterations: int = 3,
) -> Dict[str, Any]:
    """The documents must still be text - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    paths = _document_paths()

    for round_index in range(max(1, int(iterations))):
        rounds += 1
        if not paths:
            failures.append(
                "round %d: there is no document to read - the gate has no subject"
                % (round_index + 1)
            )
            break

        facts = _document_facts(paths)
        verdicts = _document_verdicts(facts)
        for label in DOC_TEXT_OBSERVATIONS:
            if label not in verdicts:
                failures.append(
                    "round %d: the gate did not observe %s"
                    % (round_index + 1, label)
                )
                continue
            if not verdicts[label]:
                failures.append("round %d: %s" % (round_index + 1, label))

        # The gate proves it can fail: a document is corrupted on
        # purpose - a backspace is injected into it - and the gate has
        # to notice that it can no longer tell text from mangled text.
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="mas-doc-text-"))
        try:
            copies: List[pathlib.Path] = []
            for path in paths:
                target = scratch / path.name
                shutil.copy2(path, target)
                copies.append(target)
            victim = copies[0]
            victim.write_text(
                victim.read_text(encoding="utf-8") + "\n\x08broken\n",
                encoding="utf-8",
            )
            broken = _document_verdicts(_document_facts(copies))
            if broken.get("the documents carry no control characters"):
                failures.append(
                    "round %d: the gate did not notice that a document was "
                    "corrupted - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    return {
        "name": "doc_text_integrity",
        "title": "\u6587\u6863\u8fd8\u662f\u6587\u672c",
        "skipped": False,
        "invariant": (
            "\u6587\u6863\u91cc\u6ca1\u6709\u63a7\u5236\u5b57\u7b26\u3001\u6ca1\u6709\u6b8b\u7559\u7684 "
            "Tab\uff0c\u4ee3\u7801\u56f4\u680f\u5b8c\u6574"
        ),
        "detail": "mas selftest --only doc_text_integrity",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_documents_are_still_text.CHECK_NAME = "doc_text_integrity"  # type: ignore[attr-defined]

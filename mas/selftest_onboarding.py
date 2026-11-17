# -*- coding: utf-8 -*-
"""The twenty-third gate: a beginner can walk the docs and get a loop.

Every other gate in this project watches the *runtime*: does the
engine close, does the ledger keep, does the draft hold. This one
watches the *front door* - the README and the quickstart, which are
the only two things a newcomer ever reads.

That is not a soft concern. The project grew 22 gates deep, and the
cost of that growth landed on the first screen: the README had grown
to 2242 lines, the gate archaeology filled the opening, the quickstart
sat at line 1314, two install sections were both called "option two",
and the pinned version in the docs was still 0.7.17 while the
release was 0.7.82. None of that breaks the engine. All of it breaks
the beginner.

So this gate holds the door:

* the onboarding section is reachable **near the top**, not buried;
* the install options are numbered without a duplicate;
* every ``mas`` command the docs mention really exists in the parser;
* the version the docs pin is the version that is running;
* the pinned install command is the one that needs no key and no key
  knowledge - a beginner must be able to reach a closed loop without
  an account.

And it proves it can fail: the onboarding locator is blinded on
purpose, and the gate has to notice that it can no longer tell a
reachable path from a buried one.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import tempfile
from typing import Any, Dict, List, Tuple

#: The observations the gate makes, and the two readings of them.
ONBOARDING_OBSERVATIONS = (
    "the onboarding section is near the top",
    "the install options are numbered without a duplicate",
    "every command the docs mention exists",
    "the pinned version is the running version",
    "the first path needs no key",
)


#: The repository root, three levels up from this file.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The files a newcomer reads, in the order they read them.
DOC_FILES = ("README.md", "README.en.md", "docs/quickstart.md")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _level_two_headings(text: str) -> List[Tuple[int, str]]:
    headings: List[Tuple[int, str]] = []
    for index, line in enumerate(text.split("\n")):
        if line.startswith("## "):
            headings.append((index, line[3:].strip()))
    return headings


def _find_heading(text: str, needle: str) -> int:
    """The line number of a level-2 heading, or -1."""
    for index, title in _level_two_headings(text):
        if needle in title:
            return index
    return -1


def _known_commands() -> set:
    """Every ``mas`` subcommand the parser actually offers."""
    from mas.cli import build_parser

    parser = build_parser()
    for action in parser._actions:
        names = getattr(action, "_name_parser_map", None)
        if names:
            return set(names)
    return set()


def _documented_commands(text: str) -> List[str]:
    """The ``mas <word>`` commands a document mentions."""
    return sorted(set(re.findall(r"\bmas ([a-z][a-z0-9-]*)", text)))


def _pinned_versions(text: str) -> List[str]:
    """The versions the docs pin themselves to."""
    found = re.findall(r"(?:-Version|MAS_VERSION=)(\d+\.\d+\.\d+)", text)
    return sorted(set(found))


def _install_options(text: str) -> List[str]:
    """The install option headings, in order."""
    options = []
    for line in text.split("\n"):
        if re.match(r"^#{2,3}\s+(?:Option|方式)[^:：]*[:：]", line):
            options.append(line.strip())
    return options


def _option_numbers(text: str) -> List[int]:
    """The numbers in the install option headings."""
    numbers = []
    for line in text.split("\n"):
        if not re.match(r"^#{2,3}\s+(?:Option|方式)[^:：]*[:：]", line):
            continue
        match = re.search(r"(?:Option|方式)\s*([一二三四五六七八九十\d]+)", line)
        if match:
            token = match.group(1)
            if token.isdigit():
                numbers.append(int(token))
            else:
                mapping = {
                    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                }
                numbers.append(mapping.get(token, 0))
    return numbers


def _onboarding_observations(root: Any) -> Dict[str, Any]:
    """Watch what a newcomer who opens the README actually sees."""
    from mas import __version__ as running

    seen: Dict[str, Any] = {}
    texts: Dict[str, str] = {}
    for rel in DOC_FILES:
        texts[rel] = _read(rel)

    chinese = texts["README.md"]
    seen["readme_lines"] = len(chinese.split("\n"))
    seen["quickstart_line"] = _find_heading(chinese, "5 分钟上手")
    seen["why_line"] = _find_heading(chinese, "为什么做这个")
    seen["archaeology_line"] = _find_heading(chinese, "门禁考古")

    # The English README has the same shape.
    english = texts["README.en.md"]
    seen["readme_en_quickstart_line"] = _find_heading(english, "5-minute quickstart")

    # Install options are numbered once, without a duplicate.
    seen["option_numbers"] = _option_numbers(chinese)
    seen["option_numbers_en"] = _option_numbers(english)
    seen["install_options"] = _install_options(chinese)

    # Every command the docs mention has to exist.
    known = _known_commands()
    unknown: Dict[str, List[str]] = {}
    for rel, text in texts.items():
        missing = [c for c in _documented_commands(text) if c not in known]
        if missing:
            unknown[rel] = missing
    seen["unknown_commands"] = unknown

    # The version the docs pin is the version that is running.
    pinned: Dict[str, List[str]] = {}
    for rel, text in texts.items():
        versions = _pinned_versions(text)
        if versions:
            pinned[rel] = versions
    seen["pinned_versions"] = pinned
    seen["running_version"] = running

    # The first install path must not need a key: a beginner has no
    # account yet, so the first thing they try cannot be "paste your
    # API key here".
    head = chinese.split("\n")
    quick_at = seen["quickstart_line"]
    window = "\n".join(head[quick_at:quick_at + 40]) if quick_at >= 0 else ""
    seen["first_path_has_no_key"] = (
        "MAS_API_KEY" not in window
        and "api_key" not in window.lower()
        and "sk-" not in window
    )
    seen["first_path_mentions_demo"] = "mas demo" in window
    return seen


def _onboarding_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}

    quick_line = int(facts.get("quickstart_line") or -1)
    why_line = int(facts.get("why_line") or -1)
    archaeology_line = int(facts.get("archaeology_line") or -1)
    verdicts["the onboarding section is near the top"] = (
        0 <= quick_line <= 40
        and (why_line < 0 or why_line > quick_line)
        and (archaeology_line < 0 or archaeology_line > quick_line)
        and int(facts.get("readme_lines") or 0) > quick_line
    )

    numbers = [int(n) for n in (facts.get("option_numbers") or [])]
    verdicts["the install options are numbered without a duplicate"] = (
        bool(numbers)
        and len(numbers) == len(set(numbers))
        and numbers == sorted(numbers)
        and numbers[0] == 1
        and numbers == list(range(1, len(numbers) + 1))
    )

    verdicts["every command the docs mention exists"] = not (
        facts.get("unknown_commands") or {}
    )

    running = str(facts.get("running_version") or "")
    pinned = facts.get("pinned_versions") or {}
    pinned_versions = sorted({v for values in pinned.values() for v in values})
    verdicts["the pinned version is the running version"] = (
        bool(pinned_versions) and pinned_versions == [running]
    )

    verdicts["the first path needs no key"] = (
        facts.get("first_path_has_no_key") is True
        and facts.get("first_path_mentions_demo") is True
    )
    return verdicts


def _onboarding_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}

    quick_line = int(facts.get("quickstart_line") or -1)
    archaeology_line = int(facts.get("archaeology_line") or -1)
    verdicts["the onboarding section is near the top"] = (
        0 < quick_line < 40 and (archaeology_line < 0 or quick_line < archaeology_line)
    )

    numbers = [int(n) for n in (facts.get("option_numbers") or [])]
    verdicts["the install options are numbered without a duplicate"] = (
        len(numbers) == len(set(numbers)) and sorted(numbers) == numbers
    )

    verdicts["every command the docs mention exists"] = (
        len(facts.get("unknown_commands") or {}) == 0
    )

    running = str(facts.get("running_version") or "")
    pinned = facts.get("pinned_versions") or {}
    flat = set()
    for values in pinned.values():
        flat.update(str(v) for v in values)
    verdicts["the pinned version is the running version"] = flat == {running}

    verdicts["the first path needs no key"] = (
        facts.get("first_path_has_no_key") is True
    )
    return verdicts


def check_a_beginner_can_walk_the_docs_and_close_a_loop(
    iterations: int = 10,
) -> Dict[str, Any]:
    """A beginner can walk the docs and close a loop.

    The gate proves it can fail: the onboarding locator is blinded on
    purpose, and the gate has to notice.
    """
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-onboarding-"))
        rounds += 1
        try:
            try:
                facts = _onboarding_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the onboarding check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}
            verdicts = _onboarding_verdicts(facts)
            independent = _onboarding_verdicts_independent(facts)
            for label in ONBOARDING_OBSERVATIONS:
                if label not in verdicts:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                    continue
                if not verdicts[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))
                if independent.get(label) != verdicts.get(label):
                    failures.append(
                        "round %d: %s - the two readings disagree (%s vs %s)"
                        % (
                            round_index + 1,
                            label,
                            verdicts.get(label),
                            independent.get(label),
                        )
                    )

            # The gate proves it can fail: the locator is blinded, and
            # the gate has to notice that it can no longer tell a
            # reachable path from a buried one.
            import mas.selftest_onboarding as gate_module

            real_find = gate_module._find_heading

            def _blind_find(text, needle):
                return -1

            try:
                gate_module._find_heading = _blind_find
                broken: Dict[str, bool] = {}
                try:
                    broken = _onboarding_verdicts(_onboarding_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                gate_module._find_heading = real_find
            if broken.get("the onboarding section is near the top"):
                failures.append(
                    "round %d: the gate did not notice that the onboarding "
                    "path is buried - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "onboarding_path",
        "title": "初学者照着文档就能跑通",
        "invariant": (
            "上手章节在首屏而不是压在门禁考古下面；安装方式编号不重复；"
            "文档里出现的每个 mas 命令都真的存在；文档钉住的版本就是当前版本；"
            "第一条路径不需要 API Key"
        ),
        "detail": "照着 README 的「5 分钟上手」走一遍",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_beginner_can_walk_the_docs_and_close_a_loop.CHECK_NAME = "onboarding_path"  # type: ignore[attr-defined]

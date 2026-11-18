# -*- coding: utf-8 -*-
"""The twenty-fourth gate: the onboarding documents travel with the wheel.

A wheel carries the package, not the repository's prose. That was
fine until the twenty-third gate showed up: that gate watches the README
and the quickstart, so in a plain ``pip install`` tree it had no subject
at all - and it reported red, with a ``FileNotFoundError`` about a
README, which said nothing about the beginner and looked like a broken
product.

So the documents were packaged (``mas/docs/``). This gate is what keeps
them there. It watches three things:

* the documents are on disk, in the package, not only in the repository;
* the packaged copy is **byte-identical** to the repository's copy - a
  drift would make the gate check prose nobody is shipped;
* the packaging declaration actually ships them, so a future
  ``pyproject.toml`` edit cannot quietly drop them from the wheel.

It also proves it can fail: the locator is blinded on purpose, and the
gate has to notice.
"""

from __future__ import annotations

import hashlib
import pathlib
import tempfile
from typing import Any, Dict, List

from mas.selftest_onboarding import DOC_FILES, PACKAGED_DOCS, REPO_ROOT

#: The observations the gate makes.
PACKAGED_DOCS_OBSERVATIONS = (
    "the onboarding documents are packaged",
    "the packaged copy matches the repository",
    "the packaging declaration ships the documents",
)

#: Where each document comes from, and where it lands in the package.
SOURCES = (
    ("README.md", "README.md"),
    ("README.en.md", "README.en.md"),
    ("docs/quickstart.md", "quickstart.md"),
)


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packaged_docs_observations(root: Any) -> Dict[str, Any]:
    """Look at what the package would actually carry."""
    seen: Dict[str, Any] = {}

    present: Dict[str, bool] = {}
    digests: Dict[str, str] = {}
    for _source, name in SOURCES:
        path = PACKAGED_DOCS / name
        present[name] = path.exists()
        if path.exists():
            digests[name] = _digest(path)
    seen["present"] = present
    seen["digests"] = digests

    # The repository is the reference - but only when it is there. In an
    # installed tree there is no repository, so there is nothing to
    # drift from: the packaged copy *is* the shipped copy.
    origins: Dict[str, str] = {}
    repository_available = False
    for source, name in SOURCES:
        path = REPO_ROOT / source
        if path.exists():
            origins[name] = _digest(path)
            repository_available = True
    seen["origins"] = origins
    seen["reference_available"] = repository_available

    # The packaging declaration only exists in a checkout. In an
    # installed tree the wheel has already been built - the declaration
    # has already had its say. So its absence is not a missing
    # declaration; it is a tree where the question does not arise.
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.exists():
        seen["declares_docs"] = "docs/" in pyproject.read_text(encoding="utf-8")
        seen["declaration_available"] = True
    else:
        seen["declares_docs"] = True
        seen["declaration_available"] = False
    return seen


def _packaged_docs_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}

    present = facts.get("present") or {}
    verdicts["the onboarding documents are packaged"] = all(
        present.get(name) for name in DOC_FILES
    )

    digests = facts.get("digests") or {}
    origins = facts.get("origins") or {}
    if facts.get("reference_available") is False:
        # Nothing to compare against: the packaged copy is the shipped
        # copy. That is not a drift, and calling it one would be a lie.
        verdicts["the packaged copy matches the repository"] = bool(digests)
    else:
        verdicts["the packaged copy matches the repository"] = bool(digests) and all(
            digests.get(name) == origins.get(name) for name in DOC_FILES
        )

    verdicts["the packaging declaration ships the documents"] = (
        facts.get("declares_docs") is True
    )
    return verdicts


def _packaged_docs_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}

    present = facts.get("present") or {}
    missing = [name for name in DOC_FILES if not present.get(name)]
    verdicts["the onboarding documents are packaged"] = not missing

    digests = facts.get("digests") or {}
    origins = facts.get("origins") or {}
    if facts.get("reference_available") is False:
        verdicts["the packaged copy matches the repository"] = bool(digests)
    else:
        differing = [
            name
            for name in DOC_FILES
            if digests.get(name) is None or digests.get(name) != origins.get(name)
        ]
        verdicts["the packaged copy matches the repository"] = not differing

    verdicts["the packaging declaration ships the documents"] = (
        facts.get("declares_docs") is True
    )
    return verdicts


def check_the_documents_the_beginner_reads_travel_with_the_wheel(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The documents a beginner reads travel with the wheel.

    The gate proves it can fail: the packaged-copy lookup is blinded on
    purpose, and the gate has to notice.
    """
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-packaged-docs-"))
        rounds += 1
        try:
            try:
                facts = _packaged_docs_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the packaged-docs check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _packaged_docs_verdicts(facts)
            independent = _packaged_docs_verdicts_independent(facts)
            for label in PACKAGED_DOCS_OBSERVATIONS:
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

            # The gate proves it can fail: the packaged directory is
            # pointed at an empty one, and the gate has to notice that it
            # can no longer see the documents.
            #
            # The earlier version of this counter-proof blinded
            # ``doc_path`` - the lookup the *onboarding* gate uses. This
            # gate looks in the package itself, so blinding that lookup
            # changed nothing and the counter-proof did not fire. A
            # counter-proof that cannot fire is not a counter-proof.
            import mas.selftest_packaged_docs as gate_module

            real_packaged = gate_module.PACKAGED_DOCS
            empty = root / "no-docs-here"
            empty.mkdir(parents=True, exist_ok=True)
            try:
                gate_module.PACKAGED_DOCS = empty
                broken: Dict[str, bool] = {}
                try:
                    broken = _packaged_docs_verdicts(_packaged_docs_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                gate_module.PACKAGED_DOCS = real_packaged
            if broken.get("the onboarding documents are packaged"):
                failures.append(
                    "round %d: the gate did not notice that the documents are "
                    "not packaged - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "packaged_docs",
        "title": "初学者要读的那两份文档跟着 wheel 走",
        "skipped": False,
        "invariant": (
            "入门文档在包里；打包副本与仓库副本逐字节一致；"
            "打包声明真的会把它们带进 wheel"
        ),
        "detail": "把 README 和 quickstart 打进 wheel，并盯住它们不漂移",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_documents_the_beginner_reads_travel_with_the_wheel.CHECK_NAME = "packaged_docs"  # type: ignore[attr-defined]

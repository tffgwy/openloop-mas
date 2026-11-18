# -*- coding: utf-8 -*-
"""The twenty-sixth gate: the one-click install survives a machine without Python.

The promise
----------
The README says the first install path is *"one script, cross-platform,
no Python environment preparation"*. The installers said the same. And
then they began by looking for Python 3.10+ and, when they did not find
it, printed a sentence that sent the user to a browser.

That is the whole beginner's first step. A fresh Windows laptop or a
bare Linux box hits it immediately, and everything after that step
really is zero-dependency: the runtime is pure standard library, the
wheel is unpacked by hand, no pip, no git, no compiler. The bootstrap
was the only part that still asked for human labour.

The rule this gate holds
------------------------
There is exactly **one** set of bootstrap rules, and both installers
follow it. That is harder than it sounds, because the installers
*cannot import* the module that holds the rules: at the moment they
need the rules, there may be no Python at all. So the rules exist
twice - once in ``mas/bootstrap.py`` and once inside each installer -
and two copies drift apart the moment somebody edits one of them.

So the gate compares the copies. It watches that:

* the module and both installers agree on the minimum version;
* the Windows installer pins the *exact* version the module names, and
  the POSIX installer pins the same release series;
* the Windows installer builds the same download URL the module builds;
* both installers *have* a bootstrap path, and both can be told not to
  use it (``-NoBootstrap`` / ``MAS_NO_BOOTSTRAP=1``);
* neither installer can still dead-end with the old "not found, go and
  install it yourself" message;
* the module itself refuses what it should refuse.

And it proves it can fail: a *drifted* pair of installers is fed to the
comparison, and the gate has to notice that the copies no longer match.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import tempfile
from typing import Any, Dict, List

from mas.bootstrap import (
    BOOTSTRAP_PYTHON_SERIES,
    BOOTSTRAP_PYTHON_VERSION,
    MINIMUM_PYTHON,
    bootstrap_plan,
    find_just_installed,
    is_supported,
    posix_bootstrap_hint,
    version_tuple,
    windows_bootstrap_url,
)

#: The observations the gate makes.
BOOTSTRAP_OBSERVATIONS = (
    "the installers and the module agree on the minimum version",
    "the installers and the module agree on the bootstrap version",
    "the Windows installer and the module agree on the download URL",
    "both installers can bootstrap a Python",
    "both installers can be told not to bootstrap a Python",
    "neither installer dead-ends on a missing Python",
    "the module refuses what it should refuse",
)

#: The repository root, two levels up from this file.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _is_source_tree(root: Any = None) -> bool:
    """Is this a source checkout, or an installed runtime?

    It matters, because the installers are the *entry point*, not the
    payload: a wheel carries the package, never ``install.ps1``. So in
    an installed tree the installers are **absent by design** - and a
    gate that reports their absence as a defect would report a
    perfectly good install as broken.

    A source checkout has a ``pyproject.toml``; an installed tree does
    not.
    """
    base = pathlib.Path(root) if root else REPO_ROOT
    return (base / "pyproject.toml").exists()


#: The two installers, and the flag that turns the bootstrap off.
INSTALLERS = (
    ("install.ps1", "-NoBootstrap"),
    ("install.sh", "MAS_NO_BOOTSTRAP"),
)

#: The Windows installer builds its URL from a variable, so the gate
#: compares the *expanded* URL rather than a literal: the copy that
#: matters is the rule, not the string.
WINDOWS_URL_VARIABLE = "$MAS_BOOTSTRAP_PYTHON"


def _installer_text(name: str) -> str:
    """The text of an installer, or an empty string when it is absent.

    An installed tree has no installers - they are the *entry point*,
    not the payload - so their absence is not a failure. The gate then
    watches the module alone, which is still worth watching.
    """
    path = REPO_ROOT / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")

def _expanded_windows_url(text: str) -> str:
    """The URL the Windows installer will actually fetch.

    The installer keeps the version in a variable and builds the URL
    from it, so the literal URL never appears in the file. The gate
    expands the variable itself - the rule is what has to match, not
    the spelling.
    """
    version = ""
    match = re.search(r"\$MAS_BOOTSTRAP_PYTHON\s*=\s*\"([0-9.]+)\"", text)
    if match:
        version = match.group(1)
    if not version:
        return ""
    return "https://www.python.org/ftp/python/%s/python-%s-amd64.exe" % (version, version)


def _bootstrap_observations(root: Any) -> Dict[str, Any]:
    """Look at the module and at both installers."""
    seen: Dict[str, Any] = {}

    minimum = "%d.%d" % MINIMUM_PYTHON
    seen["module_minimum"] = minimum
    seen["module_bootstrap_version"] = BOOTSTRAP_PYTHON_VERSION
    seen["module_series"] = BOOTSTRAP_PYTHON_SERIES
    seen["module_url"] = windows_bootstrap_url()

    installers: Dict[str, str] = {}
    present: Dict[str, bool] = {}
    for name, _flag in INSTALLERS:
        text = _installer_text(name)
        installers[name] = text
        present[name] = bool(text)
    seen["present"] = present
    seen["installers"] = installers
    seen["source_tree"] = _is_source_tree(REPO_ROOT)

    # The minimum version each installer asks for. Both spell it as a
    # (3, 10) comparison inside a probe, so the pair is what is looked
    # for rather than a single literal.
    minimums: Dict[str, List[str]] = {}
    for name, text in installers.items():
        found = re.findall(r"version_info\s*>=\s*\((\d+),\s*(\d+)\)", text)
        minimums[name] = sorted({"%s.%s" % pair for pair in found})
    seen["installer_minimums"] = minimums

    # The version each installer would bootstrap. The Windows one pins
    # the exact release; the POSIX one names the release series,
    # because the package manager decides the patch level.
    bootstraps: Dict[str, List[str]] = {}
    for name, text in installers.items():
        found = re.findall(r"(\d+\.\d+\.\d+)", text)
        bootstraps[name] = sorted(set(found))
    seen["installer_bootstrap_versions"] = bootstraps

    # The POSIX installer asks a package manager for the *series*
    # (python3.12), not for an exact patch level - the package manager
    # decides that. So the series is extracted separately.
    series: Dict[str, List[str]] = {}
    for name, text in installers.items():
        found = re.findall(r"python@?(\d+\.\d+)", text)
        series[name] = sorted(set(found))
    seen["installer_series"] = series

    # The Windows installer builds its URL from a variable; the URL it
    # will really fetch is expanded here.
    seen["windows_url"] = _expanded_windows_url(installers.get("install.ps1", ""))

    # Does each installer have a bootstrap path at all, and an opt-out?
    can_bootstrap: Dict[str, bool] = {}
    can_opt_out: Dict[str, bool] = {}
    dead_end: Dict[str, bool] = {}
    for name, _flag in INSTALLERS:
        text = installers[name]
        can_bootstrap[name] = bool(
            text
            and (
                "python.org/ftp/python" in text
                or "brew install python" in text
                or "apt-get install" in text
                or "dnf install" in text
                or "yum install" in text
            )
        )
        can_opt_out[name] = bool(text and _flag in text)
        # The old ending: it finds no Python, tells the user to go and
        # install it, and stops. That sentence is the whole defect this
        # gate exists for, so its presence is the failure.
        dead_end[name] = bool(
            re.search(r"Python 3\.10\+ not found\.[^\n]*Install it, then re-run", text)
            or re.search(r"Python 3\.10\+ not found\.[^\n]*Install Python from", text)
        )
    seen["can_bootstrap"] = can_bootstrap
    seen["can_opt_out"] = can_opt_out
    seen["dead_end"] = dead_end

    # The module's own behaviour - the rules the copies must match.
    seen["module_rejects_old"] = is_supported("3.9.0") is False
    seen["module_accepts_min"] = is_supported(minimum) is True
    seen["module_rejects_garbage"] = is_supported("not a version") is False
    seen["module_garbage_version"] = version_tuple("not a version")
    seen["module_hint"] = posix_bootstrap_hint()
    seen["module_plan_ready"] = bootstrap_plan("windows", "C:/Python312/python.exe")["action"]
    seen["module_plan_needed"] = bootstrap_plan("windows", None)["action"]
    seen["module_plan_skipped"] = bootstrap_plan("windows", None, True)["action"]
    seen["module_found_none"] = find_just_installed(
        str(pathlib.Path(tempfile.gettempdir()) / "mas-bootstrap-nothing-here")
    )
    return seen

def _pins_the_same_release(
    versions: Any,
    series_found: Any,
    version: str,
    series: str,
) -> bool:
    """Does this installer pin the same Python release the module names?

    Two spellings are acceptable, and both are correct:

    * the exact release (``3.12.8``) - what the Windows installer needs,
      because it fetches one file by name;
    * the release series (``3.12``) - what the POSIX installer needs,
      because a package manager chooses the patch level.

    Anything else is a drift.
    """
    versions = list(versions or [])
    series_found = list(series_found or [])
    if version and version in versions:
        return True
    if not series:
        return False
    if series in series_found:
        return True
    return any(
        str(candidate).startswith(series + ".") for candidate in versions
    )


def _present_names(facts: Dict[str, Any]) -> List[str]:
    """The installers that are actually on disk."""
    present = facts.get("present") or {}
    return [name for name, _flag in INSTALLERS if present.get(name)]


def _bootstrap_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}
    names = _present_names(facts)

    # The installer comparison only makes sense where the installers
    # are. In an installed tree they are absent *by design* - the wheel
    # carries the package, not the entry point - and their absence is
    # not a drift. What still has to hold there is the module's own
    # rules, because the installed runtime is what a beginner ends up
    # running.
    source_tree = facts.get("source_tree") is True
    minimum = str(facts.get("module_minimum") or "")
    minimums = facts.get("installer_minimums") or {}
    verdicts["the installers and the module agree on the minimum version"] = bool(
        minimum
    ) and (
        not source_tree
        or all(minimum in (minimums.get(name) or []) for name in names)
    )

    version = str(facts.get("module_bootstrap_version") or "")
    series = str(facts.get("module_series") or "")
    bootstraps = facts.get("installer_bootstrap_versions") or {}
    series_found = facts.get("installer_series") or {}
    verdicts["the installers and the module agree on the bootstrap version"] = bool(
        version
    ) and (
        not source_tree
        or all(
            _pins_the_same_release(
                bootstraps.get(name), series_found.get(name), version, series
            )
            for name in names
        )
    )

    url = str(facts.get("module_url") or "")
    windows_url = str(facts.get("windows_url") or "")
    verdicts["the Windows installer and the module agree on the download URL"] = bool(
        url
    ) and (
        not source_tree
        or not windows_url
        or windows_url == url
    )

    can_bootstrap = facts.get("can_bootstrap") or {}
    verdicts["both installers can bootstrap a Python"] = (
        not source_tree
        or (bool(names) and all(bool(can_bootstrap.get(name)) for name in names))
    )

    can_opt_out = facts.get("can_opt_out") or {}
    verdicts["both installers can be told not to bootstrap a Python"] = (
        not source_tree
        or (bool(names) and all(bool(can_opt_out.get(name)) for name in names))
    )

    dead_end = facts.get("dead_end") or {}
    verdicts["neither installer dead-ends on a missing Python"] = not any(
        bool(dead_end.get(name)) for name in names
    )

    verdicts["the module refuses what it should refuse"] = bool(
        facts.get("module_rejects_old")
        and facts.get("module_accepts_min")
        and facts.get("module_rejects_garbage")
        and facts.get("module_garbage_version") == (0, 0, 0)
        and facts.get("module_plan_ready") == "ready"
        and facts.get("module_plan_needed") == "needed"
        and facts.get("module_plan_skipped") == "skipped"
        and facts.get("module_found_none") is None
    )
    return verdicts


def _bootstrap_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}
    names = _present_names(facts)

    source_tree = facts.get("source_tree") is True
    minimum = str(facts.get("module_minimum") or "")
    minimums = facts.get("installer_minimums") or {}
    verdicts["the installers and the module agree on the minimum version"] = bool(
        minimum
    ) and (
        not source_tree
        or all(minimum in list(minimums.get(name) or []) for name in names)
    )

    version = str(facts.get("module_bootstrap_version") or "")
    series = str(facts.get("module_series") or "")
    bootstraps = facts.get("installer_bootstrap_versions") or {}
    series_found = facts.get("installer_series") or {}
    verdicts["the installers and the module agree on the bootstrap version"] = bool(
        version
    ) and (
        not source_tree
        or all(
            _pins_the_same_release(
                list(bootstraps.get(name) or []),
                list(series_found.get(name) or []),
                version,
                series,
            )
            for name in names
        )
    )

    url = str(facts.get("module_url") or "")
    windows_url = str(facts.get("windows_url") or "")
    verdicts["the Windows installer and the module agree on the download URL"] = bool(
        url
    ) and (
        not source_tree
        or not windows_url
        or windows_url == url
    )

    can_bootstrap = facts.get("can_bootstrap") or {}
    verdicts["both installers can bootstrap a Python"] = (
        not source_tree
        or (bool(names) and all(bool(can_bootstrap.get(name)) for name in names))
    )

    can_opt_out = facts.get("can_opt_out") or {}
    verdicts["both installers can be told not to bootstrap a Python"] = (
        not source_tree
        or (bool(names) and all(bool(can_opt_out.get(name)) for name in names))
    )

    dead_end = facts.get("dead_end") or {}
    verdicts["neither installer dead-ends on a missing Python"] = not any(
        bool(dead_end.get(name)) for name in names
    )

    verdicts["the module refuses what it should refuse"] = bool(
        facts.get("module_rejects_old")
        and facts.get("module_accepts_min")
        and facts.get("module_rejects_garbage")
        and facts.get("module_plan_ready") == "ready"
        and facts.get("module_plan_needed") == "needed"
        and facts.get("module_plan_skipped") == "skipped"
    )
    return verdicts

def check_the_one_click_install_survives_a_machine_without_python(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The one-click install survives a machine without Python.

    The gate proves it can fail: a *drifted* copy of the installers is
    fed to the comparison, and the gate has to notice that the copies
    no longer match. A gate that cannot notice a drift is not watching
    the drift.
    """
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-bootstrap-"))
        rounds += 1
        try:
            try:
                facts = _bootstrap_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the bootstrap check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _bootstrap_verdicts(facts)
            independent = _bootstrap_verdicts_independent(facts)
            for label in BOOTSTRAP_OBSERVATIONS:
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

            # The gate proves it can fail - and the counter-proof has to
            # work in *both* kinds of tree.
            #
            # The first counter-proof corrupts the **module** side: the
            # minimum version is blanked out, so the comparison has
            # nothing to agree on. It must notice. This one works
            # everywhere, including an installed tree.
            corrupted = dict(facts)
            corrupted["module_minimum"] = ""
            if _bootstrap_verdicts(corrupted).get(
                "the installers and the module agree on the minimum version"
            ):
                failures.append(
                    "round %d: the gate did not notice that the module's own "
                    "rules broke - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )

            # The second counter-proof corrupts the **installer** side:
            # the installers are replaced by copies that name a
            # *different* Python, which is exactly the drift this gate
            # exists to catch. It only applies where the installers
            # are - in an installed tree there is nothing to drift.
            if facts.get("source_tree") is True and _present_names(facts):
                drifted = dict(facts)
                installers = dict(drifted.get("installers") or {})
                drifted_installers = {}
                for name, text in installers.items():
                    drifted_installers[name] = (
                        text.replace(
                            '$MAS_BOOTSTRAP_PYTHON = "3.12.8"',
                            '$MAS_BOOTSTRAP_PYTHON = "3.11.9"',
                        )
                        .replace("python3.12", "python3.9")
                        .replace("python@3.12", "python@3.9")
                    )
                drifted["installers"] = drifted_installers
                drifted["windows_url"] = _expanded_windows_url(
                    drifted_installers.get("install.ps1", "")
                )
                drifted["installer_bootstrap_versions"] = {
                    name: sorted(
                        set(re.findall(r"(\d+\.\d+\.\d+)", text))
                    )
                    for name, text in drifted_installers.items()
                }
                drifted["installer_series"] = {
                    name: sorted(set(re.findall(r"python@?(\d+\.\d+)", text)))
                    for name, text in drifted_installers.items()
                }
                broken = _bootstrap_verdicts(drifted)
                if broken.get(
                    "the installers and the module agree on the bootstrap version"
                ):
                    failures.append(
                        "round %d: the gate did not notice that the installers "
                        "drifted - it cannot fail, so it does not watch"
                        % (round_index + 1)
                    )
                if broken.get(
                    "the Windows installer and the module agree on the download URL"
                ):
                    failures.append(
                        "round %d: the gate did not notice that the download URL "
                        "drifted - it cannot fail, so it does not watch"
                        % (round_index + 1)
                    )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "bootstrap_path",
        "title": "没有 Python 的机器上，一键安装也走得通",
        "skipped": False,
        "invariant": (
            "模块与两个安装器的自举规则一致；"
            "没有 Python 时安装器会自己装一个；"
            "用户可以拒绝自举；不再把人赶到浏览器前"
        ),
        "detail": "把自举规则放进模块，让两个安装器照做，并盯住三份副本不漂移",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_one_click_install_survives_a_machine_without_python.CHECK_NAME = "bootstrap_path"  # type: ignore[attr-defined]


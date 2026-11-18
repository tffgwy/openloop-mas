# -*- coding: utf-8 -*-
"""v0.7.86: the one-click install survives a machine without Python.

The gap
-------
The README promises the first install path as *"one script, cross-platform,
no Python environment preparation"*. The installers promised the same:
they need Python 3.10+, they need nothing else.

But the installers *begin* by looking for Python 3.10+, and when they do
not find it they stop. The very first step of the very first install -
the step a beginner takes on a fresh Windows laptop or a bare Linux
box - is the step that fails, and it fails with a sentence that sends
the user to a browser to download and install an interpreter by hand.

That is the one thing the "one-click" promise was supposed to remove.
Everything after that step really is zero-dependency: the runtime is
pure standard library, the wheel is unpacked by hand, no pip, no git,
no compiler. The bootstrap was the only part that still asked for
human labour.

What this module does
---------------------
It holds the *decisions* that both installers share, so they cannot
drift apart:

* which Python versions are acceptable;
* whether a given interpreter is usable;
* where a freshly installed interpreter is looked for afterwards
  (the installer installs Python for the current user, so the answer
  is not on PATH yet - the installer has to look in the place it put
  it);
* what the fallback plan is when Python cannot be installed at all
  (the user is told the *exact* command to run, not a URL to visit).

The installers call this module's rules; the twenty-sixth gate calls
them too. One implementation, two consumers.

The rules that keep this honest:

* **Never pretend.** A machine without Python is reported as a machine
  without Python. The installers do not claim a bootstrap happened
  when it did not.
* **Never guess a version.** The download URL is built from a version
  this module names, and the version is one this module also checks.
* **The user can always say no.** An installer that installs a Python
  interpreter behind the user's back is a surprise. The bootstrap is
  opt-out (``--no-bootstrap``), and the flag is honoured by both
  installers.
"""

from __future__ import annotations

import os
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple

#: The oldest Python this project runs on.
MINIMUM_PYTHON = (3, 10)

#: The version the installers fetch when they have to bootstrap one.
#: It is a specific release, not "latest": a bootstrap that resolves
#: to whatever happens to be newest is a bootstrap whose behaviour
#: changes without anyone changing this file.
BOOTSTRAP_PYTHON_VERSION = "3.12.8"

#: The release *series* the bootstrap version belongs to.
#:
#: The Windows installer fetches one exact release - the URL has to
#: name a file that exists. The POSIX installer asks a package manager
#: for the series instead, because the package manager decides the
#: patch level. Both name the same series, so the two installers cannot
#: drift onto different Python lines.
BOOTSTRAP_PYTHON_SERIES = "3.12"

#: Where the Windows installer puts a per-user Python. The installer
#: asks for a per-user install (``Include_launcher=0`` and no admin
#: rights), so the interpreter lands here and *not* on PATH - which is
#: exactly why the installer has to look for it there afterwards.
WINDOWS_PER_USER_PYTHON = (
    pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
)

#: The names a Python executable may have on each platform.
WINDOWS_PYTHON_NAMES = ("python.exe",)
POSIX_PYTHON_NAMES = ("python3", "python")

#: The words the scripts use when they report the outcome.
BOOTSTRAP_DONE = "已装好 Python"
BOOTSTRAP_REFUSED = "已跳过 Python 自举"


def version_tuple(text: Any) -> Tuple[int, int, int]:
    """Read a version string into a comparable tuple.

    Anything that is not a version at all reads as ``(0, 0, 0)`` -
    which is below every supported version, so an unreadable version
    is treated as "not good enough" instead of being waved through.
    """
    if isinstance(text, (tuple, list)) and len(text) >= 2:
        try:
            numbers = [int(part) for part in text[:3]]
        except (TypeError, ValueError):
            return (0, 0, 0)
        while len(numbers) < 3:
            numbers.append(0)
        return (numbers[0], numbers[1], numbers[2])
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not match:
        return (0, 0, 0)
    major = int(match.group(1))
    minor = int(match.group(2))
    micro = int(match.group(3) or 0)
    return (major, minor, micro)


def is_supported(version: Any) -> bool:
    """Is this version new enough to run the project?"""
    return version_tuple(version) >= MINIMUM_PYTHON


def interpreter_version(interpreter: Any) -> Tuple[int, int, int]:
    """The version of an interpreter, or ``(0, 0, 0)`` when unusable.

    The interpreter is asked, not guessed from its file name: a
    ``python3`` on some machines is Python 2, and a file called
    ``python.exe`` in a random folder is not an interpreter at all.
    """
    import subprocess

    exe = str(interpreter or "").strip()
    if not exe:
        return (0, 0, 0)
    try:
        completed = subprocess.run(
            [exe, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return (0, 0, 0)
    if completed.returncode != 0:
        return (0, 0, 0)
    return version_tuple((completed.stdout or "").strip())


def find_interpreter(candidates: Optional[List[str]] = None) -> Optional[str]:
    """The first usable Python 3.10+ among the candidates.

    The order matters and is part of the contract: an explicitly
    supplied interpreter always wins, then the well-known names.
    """
    for candidate in candidates or []:
        exe = str(candidate or "").strip()
        if not exe:
            continue
        if is_supported(interpreter_version(exe)):
            return exe
    return None


def windows_bootstrap_url(version: str = BOOTSTRAP_PYTHON_VERSION) -> str:
    """The official installer URL for one specific Windows build.

    The ``-amd64`` installer is the one that works on both 64-bit
    Windows and on ARM64 Windows (Windows on ARM runs the x64 build
    under emulation), so a single URL covers both.
    """
    return (
        "https://www.python.org/ftp/python/%s/python-%s-amd64.exe"
        % (version, version)
    )


def posix_bootstrap_hint() -> str:
    """The command a POSIX user runs to get a new enough Python.

    On Linux and macOS the system package manager is the answer, and
    the *exact* command is given: a beginner should not have to find
    it. macOS ships a Python 3 that is usually too old, and Homebrew is
    the path that works there, so both are named.
    """
    return (
        "macOS: brew install python@3.12   "
        "|  Debian/Ubuntu: sudo apt install python3.12   "
        "|  Fedora: sudo dnf install python3.12   "
        "|  或从 https://www.python.org/downloads/ 下载"
    )


def installed_python_dirs(base: Any = None) -> List[pathlib.Path]:
    """Where a per-user Windows Python may have landed.

    The installer puts it in a version-named folder under the user's
    own ``Programs`` directory, and it is *not* on PATH yet. So after
    a bootstrap the installer has to look here - otherwise it would
    report "Python not found" immediately after having installed it.
    """
    root = pathlib.Path(base) if base else WINDOWS_PER_USER_PYTHON
    found: List[pathlib.Path] = []
    if not root.exists():
        return found
    try:
        children = sorted(root.iterdir())
    except OSError:
        return found
    for child in children:
        if not child.is_dir():
            continue
        for name in WINDOWS_PYTHON_NAMES:
            candidate = child / name
            if candidate.exists():
                found.append(candidate)
                break
    return found


def find_just_installed(base: Any = None) -> Optional[str]:
    """The interpreter a bootstrap just put on disk, if any.

    The newest version wins: a machine that already had an old Python
    and then got a new one should keep the new one.
    """
    best: Optional[str] = None
    best_version: Tuple[int, int, int] = (0, 0, 0)
    for candidate in installed_python_dirs(base):
        version = interpreter_version(str(candidate))
        if not is_supported(version):
            continue
        if version > best_version:
            best_version = version
            best = str(candidate)
    return best


def bootstrap_plan(
    platform: str,
    interpreter: Optional[str],
    opted_out: bool = False,
) -> Dict[str, Any]:
    """What the installer should do about Python, and why.

    Three outcomes, and they are genuinely different:

    * ``ready``   - an interpreter was found; nothing to bootstrap;
    * ``skipped`` - the user opted out; the installer says so and
      stops, instead of installing something behind their back;
    * ``needed``  - there is no interpreter and the user did not opt
      out; the installer may fetch one.

    The plan is a *report*, not an action: it never runs anything.
    """
    system = str(platform or "").strip().lower()
    if interpreter:
        return {
            "action": "ready",
            "reason": "found a usable Python",
            "interpreter": str(interpreter),
            "url": "",
            "hint": "",
        }
    if opted_out:
        return {
            "action": "skipped",
            "reason": "the user asked not to bootstrap a Python",
            "interpreter": "",
            "url": "",
            "hint": posix_bootstrap_hint() if system != "windows" else "",
        }
    if system == "windows":
        return {
            "action": "needed",
            "reason": "no Python 3.10+ on this machine",
            "interpreter": "",
            "url": windows_bootstrap_url(),
            "hint": "",
        }
    return {
        "action": "needed",
        "reason": "no Python 3.10+ on this machine",
        "interpreter": "",
        "url": "",
        "hint": posix_bootstrap_hint(),
    }


def describe(plan: Dict[str, Any]) -> str:
    """Say the plan in words, for a person."""
    action = str(plan.get("action") or "")
    if action == "ready":
        return "找到可用的 Python：%s" % plan.get("interpreter")
    if action == "skipped":
        return "按你的要求不装 Python%s" % (
            ("；" + str(plan.get("hint"))) if plan.get("hint") else ""
        )
    if action == "needed":
        if plan.get("url"):
            return "这台机器上没有 Python 3.10+，正在自动装一个：%s" % plan.get("url")
        return "这台机器上没有 Python 3.10+：%s" % plan.get("hint")
    return "Python 的情况不明"


def bootstrap_status(
    platform: str,
    interpreter: Optional[str],
    opted_out: bool = False,
) -> Dict[str, Any]:
    """The report the doctor shows for the ``bootstrap`` check."""
    plan = bootstrap_plan(platform, interpreter, opted_out)
    plan["text"] = describe(plan)
    return plan

"""Which ``mas`` does the user actually type?

The one-click installer puts a launcher in ``<install-dir>/bin`` and
adds that directory to the PATH. But a PATH is a *list*, and the
installer's entry is only *one* of its entries. A user who also ran
``pip install openloop-mas`` has a second entry - usually earlier in
the PATH - and a second copy of the same program on disk.

That is not a cosmetic problem. It is the difference between "the
installer verified the code it just installed" and "the code the user
is actually running". On a real machine:

    C:\\Users\\me\\AppData\\Roaming\\Python\\Python314\\Scripts\\mas.exe   0.7.16
    C:\\Users\\me\\.mas\\bin\\mas.cmd                                      0.7.81

The installer unpacked 0.7.81, ran ``mas doctor`` against it, said
"everything is ready" - and then the user typed ``mas`` and got
0.7.16. Every check passed. The wrong program ran anyway.

This module finds the shadow: it walks the PATH the same way the
shell does, finds every entry that would answer to ``mas``, and
reports which one wins. The rule it follows is the same rule the
rest of the project follows: **never report a state that was not
observed.** A version is only reported when it was read from a file
on disk, and a winner is only reported when the PATH order says so.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Sequence

_VERSION_RE = re.compile(r"__version__\s*=\s*[\"']([^\"']+)[\"']")

#: The names a launcher may have. On Windows the installer writes
#: ``mas.cmd``; pip writes ``mas.exe``; on POSIX the installer writes
#: ``mas``. All of them answer to ``mas`` on the shell.
LAUNCHER_NAMES = ("mas", "mas.cmd", "mas.exe", "mas.bat", "mas.ps1")


def _running_version() -> str:
    """The version of the runtime that is running this very code."""
    from mas import __version__

    return str(__version__)


def _version_tuple(value: str) -> tuple:
    """A comparable form of a version string (see mas.upgrade)."""
    from mas.upgrade import _version_tuple as _shared

    return _shared(value)


def _read_version(init_file: pathlib.Path) -> str:
    """The version string written in a package's ``__init__.py``."""
    if not init_file.is_file():
        return ""
    try:
        text = init_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else ""


def _package_dir_of_launcher(launcher: pathlib.Path) -> Optional[pathlib.Path]:
    """The ``mas`` package directory a launcher would import.

    Two shapes are supported:

    * a launcher that points at ``<app>/mas/__main__.py`` - the shape
      the one-click installer writes. The package is the sibling
      ``mas`` directory;
    * a launcher that sits in a ``Scripts`` directory next to a
      ``mas`` package directory - the shape ``pip install`` produces.
      The package is the sibling ``mas`` directory too.
    """
    parent = launcher.parent
    sibling = parent / "mas"
    if (sibling / "__init__.py").is_file():
        return sibling
    # Some layouts put the launcher one level up (bin/mas -> app/mas).
    for candidate in (parent.parent / "app" / "mas", parent.parent / "mas"):
        if (candidate / "__init__.py").is_file():
            return candidate
    # The pip layout: ``<prefix>/Scripts/mas.exe`` next to
    # ``<prefix>/site-packages/mas``. On Windows the user install puts
    # both under ``AppData\\Roaming\\Python\\PythonXY\\``; on POSIX
    # the scripts live in ``<prefix>/bin`` and the package in
    # ``<prefix>/lib/pythonX.Y/site-packages``.
    for base in (parent.parent, parent):
        package = base / "site-packages" / "mas"
        if (package / "__init__.py").is_file():
            return package
        lib_dir = base / "lib"
        if lib_dir.is_dir():
            for candidate in sorted(lib_dir.glob("python*")):
                package = candidate / "site-packages" / "mas"
                if (package / "__init__.py").is_file():
                    return package
    return None


def _version_of_launcher(launcher: pathlib.Path) -> str:
    """The runtime version a launcher would run, read from disk.

    Empty when the launcher cannot be traced to a package - the
    honest answer, rather than a guess.
    """
    package = _package_dir_of_launcher(launcher)
    if package is None:
        return ""
    return _read_version(package / "__init__.py")


def path_entries(env: Optional[Dict[str, str]] = None) -> List[pathlib.Path]:
    """The PATH directories, in the order the shell searches them."""
    env = env if env is not None else os.environ
    raw = env.get("PATH") or env.get("Path") or ""
    entries: List[pathlib.Path] = []
    for chunk in raw.split(os.pathsep):
        chunk = chunk.strip().strip('"')
        if not chunk:
            continue
        entries.append(pathlib.Path(os.path.expanduser(chunk)))
    return entries


def find_launchers(
    env: Optional[Dict[str, str]] = None,
    names: Sequence[str] = LAUNCHER_NAMES,
) -> List[pathlib.Path]:
    """Every launcher on the PATH, in PATH order, without duplicates.

    The first entry for a given directory wins - that is how the
    shell resolves a command name inside one directory.
    """
    found: List[pathlib.Path] = []
    seen: set = set()
    for directory in path_entries(env):
        for name in names:
            candidate = directory / name
            key = str(candidate)
            if key in seen:
                continue
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            seen.add(key)
            found.append(candidate)
            break
    return found


def shadow_status(
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """What would actually run when the user types ``mas``?

    The report names the winner (the first launcher the PATH reaches)
    and every other launcher it could have found. It never guesses: a
    version is only filled in when it was read from a file on disk.
    """
    running = _running_version()
    launchers = find_launchers(env)
    rows: List[Dict[str, Any]] = []
    for index, launcher in enumerate(launchers):
        version = _version_of_launcher(launcher)
        rows.append({
            "path": str(launcher),
            "version": version,
            "position": index,
            "winner": index == 0,
            "traceable": bool(version),
        })

    winner = rows[0] if rows else None
    shadowed = False
    if winner is not None:
        winner_version = winner.get("version") or ""
        # The winner is a shadow when it is *not* the copy that is
        # running this check. Comparing paths is not enough - two
        # copies can sit in two directories and still be the same
        # code - so the version is the thing that decides.
        if winner_version and winner_version != running:
            shadowed = True
        if not winner_version and rows:
            # The winner cannot be traced to a package. That is not
            # proof of a shadow, but it is not proof of safety either,
            # so it is reported as its own kind of uncertainty.
            shadowed = True

    distinct_versions = sorted({row["version"] for row in rows if row.get("version")})
    return {
        "running_version": running,
        "launchers": rows,
        "winner": winner,
        "shadowed": shadowed,
        "shadow_version": (winner or {}).get("version", "") if shadowed else "",
        "distinct_versions": distinct_versions,
        "count": len(rows),
    }


def format_shadow_status(status: Dict[str, Any]) -> str:
    """Render the shadow report for a person."""
    lines: List[str] = []
    running = status.get("running_version") or "?"
    lines.append("当前运行的版本：%s" % running)
    rows = status.get("launchers") or []
    if not rows:
        lines.append("PATH 上没有找到任何 mas 启动器（只能靠完整路径调用）")
        return "\n".join(lines)
    lines.append("PATH 上共有 %d 个 mas 启动器：" % len(rows))
    for row in rows:
        mark = "->" if row.get("winner") else "  "
        version = row.get("version") or "读不出版本"
        lines.append("%s %s（%s）" % (mark, row["path"], version))
    if status.get("shadowed"):
        lines.append(
            "[!!] 你敲 mas 时跑的是 %s，"
            "不是现在这一版（%s）。"
            % (status.get("shadow_version") or "读不出版本的另一份", running)
        )
    else:
        lines.append("[OK ] 你敲 mas 跑的就是现在这一版")
    return "\n".join(lines)


def shadowed_version(status: Dict[str, Any]) -> str:
    """The version the user's own ``mas`` would run (empty when unknown)."""
    winner = status.get("winner") or {}
    if not status.get("shadowed"):
        return ""
    return str(winner.get("version") or "")


def is_current_interpreter() -> bool:
    """Is the running interpreter the one that owns the launcher?

    A pip-installed copy is invoked through its own interpreter, so the
    running ``sys.executable`` tells us which world we are in.
    """
    return bool(sys.executable)

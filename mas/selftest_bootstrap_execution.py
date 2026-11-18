# -*- coding: utf-8 -*-
"""The twenty-seventh gate: the bootstrap path is *executed*, not described.

The gap
-------
The twenty-sixth gate compares the bootstrap rules in the module with
the copies inside the two installers, and fails when they drift. That
is a real check - but it checks the *text*.

It never runs the bootstrap. Neither does the CI: every install job
starts with ``actions/setup-python``, so the runner has a Python before
the installer looks for one. The branch that says "this machine has no
Python" is therefore executed **nowhere** - not in CI, not on a
developer's machine, not on a contributor's machine. Every machine
that runs the tests already has Python.

A path that is never executed is a path that breaks silently, and it
breaks exactly where it is hardest to debug: on a beginner's fresh
laptop, with nobody around who could tell them what the error meant.

What this gate does
-------------------
It runs the branch. The installer is told to behave as if the machine
had no Python at all, the interpreter is staged where a real bootstrap
would have put it, and the installer's **production lookup** has to
find it - unchanged code, unchanged path.

It watches that:

* the bootstrap branch is really entered (not skipped by a shortcut);
* the interpreter is found by the *production* lookup, not by a
  rehearsal-only shortcut;
* the installation that follows is complete - the runtime is in place,
  the launcher exists, the self-check runs;
* a Store stub is never treated as an interpreter;
* the user can still refuse the bootstrap, and refusing stops the
  install instead of silently continuing;
* the rehearsal fails when the lookup is broken - so a gate that
  cannot fail does not pass by accident;
* a Python that is *only* on PATH is still found.

The rehearsal runs in a *hostile* environment
-------------------------
The environment the rehearsal builds carries a ``PATHEXT`` that names no
executable at all (``PATHEXT=.CPL``). That is not a contrivance: it is
what a restricted environment really looks like, and it is what broke the
installer in this very round.

With such a ``PATHEXT``, the shell treats ``python.exe`` as a *document*:
calling it in a pipeline raises "Cannot run a document in the middle of a
pipeline", and calling it on its own produces no output and leaves
``$LASTEXITCODE`` empty. ``Get-Command`` reports that there is no such
command even though the file is sitting right there.

So the installer has to reach its interpreter without the shell's help:
the PATH is walked by the installer itself, and the interpreter is started
through .NET. A gate that ran the rehearsal in a *normal* environment would
never have noticed any of that - and the beginner on the fresh laptop
would.

The gate runs offline, on the machine it runs on, without touching it:
the interpreter is staged in a temporary folder and ``LOCALAPPDATA`` is
pointed at it for the length of the rehearsal.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

from mas.bootstrap import (
    MINIMUM_PYTHON,
    interpreter_version,
    is_store_stub,
)

#: The observations the gate makes.
BOOTSTRAP_EXECUTION_OBSERVATIONS = (
    "the bootstrap branch is really entered",
    "the interpreter is found by the production lookup",
    "the installation that follows is complete",
    "a Store stub is never treated as an interpreter",
    "the user can refuse the bootstrap and the install stops",
    "the rehearsal notices a broken lookup",
    "the installer finds a Python that is only on PATH",
)

#: The repository root, two levels up from this file.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _windows_installer() -> pathlib.Path:
    return REPO_ROOT / "install.ps1"


def _posix_installer() -> pathlib.Path:
    return REPO_ROOT / "install.sh"


def _is_windows() -> bool:
    return os.name == "nt"


def _usable_interpreter() -> str:
    """An interpreter this machine can hand to the rehearsal.

    The rehearsal needs a real interpreter to stage: a placeholder
    would let the installer *find* something it could not *run*, and
    the rehearsal would pass while proving nothing.
    """
    found = sys.executable
    if found and interpreter_version(found) >= MINIMUM_PYTHON:
        return found
    for name in ("python3", "python"):
        import shutil as _shutil

        candidate = _shutil.which(name)
        if candidate and interpreter_version(candidate) >= MINIMUM_PYTHON:
            return candidate
    raise RuntimeError("this machine has no usable Python 3.10+ to stage")


def _run_powershell(script: pathlib.Path, args: List[str], env: Dict[str, str]) -> Dict[str, Any]:
    """Run the Windows installer and report what happened."""
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=900,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return {"returncode": completed.returncode, "output": output}


def _run_bash(script: pathlib.Path, args: List[str], env: Dict[str, str]) -> Dict[str, Any]:
    """Run the POSIX installer and report what happened."""
    command = ["bash", str(script), *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=900,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return {"returncode": completed.returncode, "output": output}


#: The names a Python answers to on the PATH.
INTERPRETER_NAMES = (
    "python",
    "python2",
    "python3",
    "pythonw",
    "python2.7",
    "python3.10",
    "python3.11",
    "python3.12",
    "python3.13",
    "python3.14",
)


def _is_interpreter_name(name: str) -> bool:
    """Is this file name one of the names a Python answers to?"""
    stem = name.lower()
    if stem.endswith(".exe"):
        stem = stem[:-4]
    if stem in INTERPRETER_NAMES:
        return True
    # A versioned interpreter such as python3.12.
    return bool(stem.startswith("python") and stem[6:7] == "." and stem[7:].isdigit())


def _folder_holds_interpreter(folder: pathlib.Path) -> bool:
    """Does this folder hold an interpreter under any of its names?"""
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return any(_is_interpreter_name(name) for name in names)


#: The shadows the rehearsal built, so they can be cleaned up.
_SHADOWS: List[pathlib.Path] = []


def _make_shadow(folder: pathlib.Path) -> pathlib.Path:
    """Build a copy of ``folder`` without any interpreter in it.

    The shadow holds a link to every other file of the folder, so the
    machine keeps every tool it had - the shell, the coreutils, the
    package managers - and loses only the interpreters. Nothing on the
    machine is moved, renamed or deleted: the shadow is a separate
    folder that only exists for the length of the rehearsal.
    """
    import tempfile

    shadow = pathlib.Path(tempfile.mkdtemp(prefix="mas-shadow-path-"))
    _SHADOWS.append(shadow)
    try:
        for child in folder.iterdir():
            if _is_interpreter_name(child.name):
                continue
            target = shadow / child.name
            if target.exists():
                continue
            try:
                target.symlink_to(child)
                continue
            except OSError:
                pass
            # A machine without the privilege to create a symbolic link
            # still has to keep its tools reachable, so the shadow falls
            # back to a hard link and then to a copy. The rehearsal only
            # needs the tools to be *there*; it never writes through them.
            try:
                os.link(child, target)
                continue
            except OSError:
                pass
            try:
                if child.is_dir():
                    shutil.copytree(child, target, symlinks=True)
                else:
                    shutil.copy2(child, target)
            except (OSError, shutil.Error):
                continue
    except OSError:
        pass
    return shadow


def _shadow_path(path_value: str) -> str:
    r"""Return a PATH in which no interpreter can be found.

    A folder that holds an interpreter is replaced by a shadow of
    itself; every other folder is left exactly as it was. On Windows
    the folders are simply dropped: the installer there needs no
    external tool, and a shadow of ``C:\Windows\System32`` would be
    neither possible nor useful.
    """
    if os.name == "nt":
        kept = [
            entry
            for entry in path_value.split(os.pathsep)
            if entry and not _folder_holds_interpreter(pathlib.Path(entry))
        ]
        return os.pathsep.join(kept)

    entries: List[str] = []
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        folder = pathlib.Path(entry)
        if _folder_holds_interpreter(folder):
            entries.append(str(_make_shadow(folder)))
        else:
            entries.append(entry)
    return os.pathsep.join(entries)


def _clean_shadows() -> None:
    """Remove the shadows the rehearsal built."""
    while _SHADOWS:
        shadow = _SHADOWS.pop()
        shutil.rmtree(shadow, ignore_errors=True)


def _rehearsal_env(home: pathlib.Path, probe: pathlib.Path, interpreter: str) -> Dict[str, str]:
    """The environment the rehearsal runs in.

    ``PATH`` is emptied of every Python: the rehearsal has to look as
    if the machine had none. The interpreter is handed over separately,
    because a rehearsal that could see it on PATH would never enter
    the branch it is meant to exercise.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["MAS_PROBE_PYTHON"] = interpreter
    env["MAS_HOME"] = str(home / ".mas")
    env["MAS_FORCE_BOOTSTRAP"] = "1"
    env["MAS_BOOTSTRAP_PROBE"] = str(probe)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # A PATHEXT that names no executable at all. The shell then
    # treats ``python.exe`` as a document, and the installer has to
    # reach its interpreter without the shell's help - which is what
    # a restricted environment really looks like.
    env["PATHEXT"] = ".CPL"
    # Take every *interpreter* off the PATH, and leave the rest of the
    # machine alone. The rehearsal is only honest if the machine really
    # looks bare of Python - and it must still be able to run a shell.
    #
    # On a Linux runner the interpreter lives in /usr/bin next to bash
    # and the coreutils. Dropping that whole directory would take the
    # shell away with it, and the rehearsal would die with "bash: not
    # found" - a failure of the machine the gate built, not of the
    # installer it is supposed to test. A gate that cannot run is not
    # a gate.
    #
    # So the directory is replaced by a shadow: the same folder, with
    # every tool in it, except the interpreters.
    env["PATH"] = _shadow_path(env.get("PATH", ""))
    return env


def _bootstrap_observations(root: pathlib.Path) -> Dict[str, Any]:
    """Run the rehearsal and report what was observed."""
    home = root / "home"
    probe = root / "probe"
    home.mkdir(parents=True, exist_ok=True)
    probe.mkdir(parents=True, exist_ok=True)
    interpreter = _usable_interpreter()

    facts: Dict[str, Any] = {
        "platform": sys.platform,
        "is_windows": _is_windows(),
        "interpreter": interpreter,
        "probe": str(probe),
        "home": str(home),
    }

    env = _rehearsal_env(home, probe, interpreter)

    if _is_windows():
        script = _windows_installer()
        args = [
            "-InstallDir",
            str(home / ".mas"),
            "-SkipPath",
            "-SkipSkills",
            "-SkipDemo",
            "-ForceBootstrap",
            "-BootstrapProbe",
            str(probe),
        ]
        facts["result"] = _run_powershell(script, args, env)
    else:
        script = _posix_installer()
        args = []
        facts["result"] = _run_bash(script, args, env)

    output = str(facts["result"].get("output") or "")
    facts["output"] = output
    facts["entered_bootstrap"] = (
        "No Python 3.10+ on this machine" in output
        or "pretending this machine has no Python" in output
    )
    facts["found_by_lookup"] = (
        "rehearsal found the interpreter" in output
    )
    facts["runtime_installed"] = (home / ".mas" / "app" / "mas" / "__init__.py").exists()
    facts["launcher_installed"] = (home / ".mas" / "bin").exists() and any(
        (home / ".mas" / "bin").iterdir()
    )
    # A Store alias is recognised as a stub, and a real interpreter is
    # not. Both halves matter: a check that called everything a stub
    # would be just as wrong as one that called nothing a stub.
    stub_path = r"C:\Users\Someone\AppData\Local\Microsoft\WindowsApps\python3.exe"
    real_path = r"C:\Python312\python.exe"
    facts["store_stub_is_not_an_interpreter"] = (
        is_store_stub(stub_path) and not is_store_stub(real_path)
    )
    return facts


def _path_only_observation(root: pathlib.Path) -> Dict[str, Any]:
    """Can the installer find a Python that is *only* on PATH?

    This is the second half of the "the machine has no Python"
    story. The rehearsal above hides every Python, so the installer
    has to stage one. This observation does the opposite: it leaves a
    real interpreter on PATH - and nothing else - and asks the
    installer to find it.

    The environment is the same hostile one: a ``PATHEXT`` that names
    no executable. ``Get-Command`` therefore reports that there is no
    ``python`` on PATH at all - while the file is right there. An
    installer that trusted the shell would download a second Python.
    """
    home = root / "path-home"
    home.mkdir(parents=True, exist_ok=True)
    interpreter = _usable_interpreter()
    interpreter_dir = pathlib.Path(interpreter).resolve().parent
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["MAS_HOME"] = str(home / ".mas")
    env["PYTHONUTF8"] = "1"
    env["PATHEXT"] = ".CPL"
    # The interpreter's own folder is the *only* place on PATH that
    # holds a Python - and every other folder that holds one is taken
    # out of the way.
    #
    # The folder is replaced by a shadow of itself: the same folder
    # with every tool in it, except the interpreters. Dropping the
    # whole folder would take the shell and the coreutils with it on a
    # Linux runner, and the observation would die of "bash: not found"
    # instead of reporting what it went out to report.
    kept: List[str] = []
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        folder = pathlib.Path(entry)
        try:
            same = folder.resolve() == interpreter_dir
        except OSError:
            same = False
        if same:
            kept.append(str(folder))
        elif _folder_holds_interpreter(folder):
            kept.append(str(_make_shadow(folder)))
    env["PATH"] = os.pathsep.join(kept)
    if _is_windows():
        result = _run_powershell(
            _windows_installer(),
            [
                "-InstallDir",
                str(home / ".mas"),
                "-SkipPath",
                "-SkipSkills",
                "-SkipDemo",
            ],
            env,
        )
    else:
        result = _run_bash(_posix_installer(), [], env)
    output = str(result.get("output") or "")
    return {"output": output, "result": result}


def _bootstrap_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The gate's reading of the facts."""
    result = dict(facts.get("result") or {})
    code = int(result.get("returncode") or 0)
    return {
        "the bootstrap branch is really entered": bool(facts.get("entered_bootstrap")),
        "the interpreter is found by the production lookup": bool(
            facts.get("found_by_lookup")
        ),
        "the installation that follows is complete": bool(
            facts.get("runtime_installed")
            and facts.get("launcher_installed")
            and code == 0
        ),
        "a Store stub is never treated as an interpreter": bool(
            facts.get("store_stub_is_not_an_interpreter")
        ),
        # The refusal is checked against its own run (see
        # _refusal_observation), so it is carried in as a fact rather
        # than looked for in the rehearsal's output.
        "the user can refuse the bootstrap and the install stops": bool(
            facts.get("refusal_honoured")
        ),
        "the rehearsal notices a broken lookup": True,
        "the installer finds a Python that is only on PATH": bool(
            facts.get("path_only_found")
        ),
    }


def _bootstrap_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading, written without looking at the first.

    The two readings have to agree, or the gate does not know what it
    is looking at.
    """
    output = str(facts.get("output") or "")
    result = dict(facts.get("result") or {})
    code = int(result.get("returncode") or 0)
    lines = [line.strip() for line in output.splitlines()]

    entered = any(
        ("No Python 3.10+ on this machine" in line)
        or ("pretending this machine has no Python" in line)
        for line in lines
    )
    found = any("rehearsal found the interpreter" in line for line in lines)
    installed = bool(facts.get("runtime_installed"))
    launcher = bool(facts.get("launcher_installed"))
    stub = bool(facts.get("store_stub_is_not_an_interpreter"))
    return {
        "the bootstrap branch is really entered": entered,
        "the interpreter is found by the production lookup": found,
        "the installation that follows is complete": installed and launcher and code == 0,
        "a Store stub is never treated as an interpreter": stub,
        "the user can refuse the bootstrap and the install stops": bool(
            facts.get("refusal_honoured")
        ),
        "the rehearsal notices a broken lookup": True,
        "the installer finds a Python that is only on PATH": bool(
            facts.get("path_only_found")
        ),
    }


def _bootstrap_observations_with_empty_probe(root: pathlib.Path) -> Dict[str, Any]:
    """The rehearsal against a folder the lookup does not read.

    This is the counter-proof. The production lookup runs, unchanged,
    and it finds nothing - because the interpreter was staged
    somewhere else. The gate has to report that as a failure of the
    lookup.
    """
    home = root / "empty-home"
    probe = root / "empty-probe"
    elsewhere = root / "empty-probe-elsewhere"
    home.mkdir(parents=True, exist_ok=True)
    probe.mkdir(parents=True, exist_ok=True)
    elsewhere.mkdir(parents=True, exist_ok=True)
    interpreter = _usable_interpreter()
    env = _rehearsal_env(home, probe, interpreter)
    # The lookup reads %LOCALAPPDATA%\Programs\Python, and the
    # rehearsal points LOCALAPPDATA at its own folder. So the
    # blindness has to be *inside* that folder: the counter-proof
    # asks the rehearsal to stage nothing at all, and the lookup -
    # unchanged - then finds nothing.
    env["MAS_BOOTSTRAP_PROBE"] = str(elsewhere)
    env["MAS_PROBE_STAGE"] = "0"
    if _is_windows():
        result = _run_powershell(
            _windows_installer(),
            [
                "-InstallDir",
                str(home / ".mas"),
                "-SkipPath",
                "-SkipSkills",
                "-SkipDemo",
                "-ForceBootstrap",
                "-BootstrapProbe",
                str(elsewhere),
            ],
            env,
        )
    else:
        result = _run_bash(_posix_installer(), [], env)
    output = str(result.get("output") or "")
    return {
        "output": output,
        "result": result,
        "entered_bootstrap": (
            "No Python 3.10+ on this machine" in output
            or "pretending this machine has no Python" in output
        ),
        "found_by_lookup": "rehearsal found the interpreter" in output,
        "runtime_installed": False,
        "launcher_installed": False,
        "store_stub_is_not_an_interpreter": True,
    }


def _refusal_observation(root: pathlib.Path) -> Dict[str, Any]:
    """What happens when the user says *no* to the bootstrap."""
    home = root / "refuse-home"
    home.mkdir(parents=True, exist_ok=True)
    interpreter = _usable_interpreter()
    env = _rehearsal_env(home, root / "refuse-probe", interpreter)
    env["MAS_NO_BOOTSTRAP"] = "1"
    env["MAS_FORCE_BOOTSTRAP"] = "1"
    if _is_windows():
        # The refusal is given *both* ways on Windows: the switch and
        # the environment variable. A refusal that only one of them
        # honours is a refusal that half the machines ignore - and the
        # POSIX installer only knows the variable, so the two have to
        # agree on the name.
        result = _run_powershell(
            _windows_installer(),
            [
                "-InstallDir",
                str(home / ".mas"),
                "-SkipPath",
                "-SkipSkills",
                "-SkipDemo",
                "-ForceBootstrap",
                "-NoBootstrap",
            ],
            env,
        )
    else:
        result = _run_bash(_posix_installer(), [], env)
    return {"result": result}


def check_the_bootstrap_path_is_executed_and_not_just_described(
    iterations: int = 1,
) -> Dict[str, Any]:
    """The bootstrap path is executed, not just described.

    The gate proves it can fail: the rehearsal is run against an
    installer whose lookup is broken, and the gate has to notice that
    the rehearsal no longer finds the interpreter.
    """
    failures: List[str] = []
    rounds = 0

    if not _windows_installer().exists() and not _posix_installer().exists():
        return {
            "name": "bootstrap_execution",
            "title": "没有 Python 的机器上，这条路真的跑得通",
            "skipped": True,
            "invariant": "安装器在「没有 Python」的分支上真的跑完一次安装",
            "detail": "没有安装器可比对，跳过（不是通过）",
            "iterations": 0,
            "failures": [],
            "ok": True,
        }

    root = pathlib.Path(tempfile.mkdtemp(prefix="mas-bootstrap-exec-"))
    try:
        rounds = 1
        try:
            facts = _bootstrap_observations(root)
        except Exception as exc:  # noqa: BLE001 - the failure is the subject
            failures.append(
                "the rehearsal broke: %s: %s" % (type(exc).__name__, exc)
            )
            facts = {}

        # The PATH-only run is a third run: this time an interpreter
        # *is* on PATH and nothing else has to be staged. The
        # installer has to find it there.
        try:
            path_only = _path_only_observation(root)
            path_output = str(path_only.get("output") or "")
            path_code = int((path_only.get("result") or {}).get("returncode") or 0)
            # The installer has to say that it found the interpreter,
            # and it must not have gone down the bootstrap branch.
            found_on_path = (
                ("Python " in path_output)
                and ("No Python 3.10+ on this machine" not in path_output)
                and ("pretending this machine has no Python" not in path_output)
            )
            if not found_on_path:
                failures.append(
                    "the installer did not find the Python that was on PATH"
                )
            if path_code != 0:
                failures.append(
                    "the install failed on a machine that already had a Python"
                )
            facts["path_only_found"] = bool(found_on_path and path_code == 0)
        except Exception as exc:  # noqa: BLE001 - the failure is the subject
            failures.append(
                "the PATH-only check broke: %s: %s"
                % (type(exc).__name__, exc)
            )

        # The refusal is a separate run: the user says no, and the
        # installer must stop instead of quietly installing something.
        try:
            refusal = _refusal_observation(root)
            refusal_output = str((refusal.get("result") or {}).get("output") or "")
            refuses = (
                "MAS_NO_BOOTSTRAP=1" in refusal_output
                or "-NoBootstrap was given" in refusal_output
                or "MAS_NO_BOOTSTRAP" in refusal_output
            )
            refusal_code = int((refusal.get("result") or {}).get("returncode") or 0)
            if not refuses:
                failures.append(
                    "the refusal was not honoured - the installer did not say "
                    "that the bootstrap was skipped"
                )
            if refusal_code == 0:
                failures.append(
                    "the refusal did not stop the install - the installer "
                    "reported success"
                )
            # The refusal is re-read as a fact and put into the facts,
            # so that both readings of the gate judge the *same*
            # observation.
            facts["refusal_honoured"] = bool(refuses and refusal_code != 0)
        except Exception as exc:  # noqa: BLE001 - the failure is the subject
            failures.append(
                "the refusal check broke: %s: %s" % (type(exc).__name__, exc)
            )

        verdicts = _bootstrap_verdicts(facts)
        independent = _bootstrap_verdicts_independent(facts)
        for label in BOOTSTRAP_EXECUTION_OBSERVATIONS:
            if label not in verdicts:
                failures.append("the gate did not observe %s" % label)
                continue
            if not verdicts[label]:
                failures.append(label)
            if independent.get(label) != verdicts.get(label):
                failures.append(
                    "%s - the two readings disagree (%s vs %s)"
                    % (label, verdicts.get(label), independent.get(label))
                )

        # The gate proves it can fail.
        #
        # The counter-proof runs the *real* observation code against an
        # installer whose lookup cannot work: the rehearsal stages its
        # interpreter somewhere the production lookup does not read, so
        # the lookup - unchanged - finds nothing. The gate has to
        # notice that the rehearsal no longer finds the interpreter.
        # Otherwise "the interpreter is found by the production lookup"
        # would be a sentence that is always true, and therefore
        # watches nothing.
        try:
            broken_facts = _bootstrap_observations_with_empty_probe(root)
            broken = _bootstrap_verdicts(broken_facts)
        except Exception as exc:  # noqa: BLE001 - a crash counts as "noticed"
            broken = {}
            failures.append(
                "the counter-proof crashed instead of reporting: %s: %s"
                % (type(exc).__name__, exc)
            )
        if broken.get("the interpreter is found by the production lookup"):
            failures.append(
                "the gate did not notice that the lookup finds nothing - it "
                "cannot fail, so it does not watch"
            )

    finally:
        # The shadows are cleaned up before the temporary folder: they
        # live outside it, and a shadow left behind would keep a copy
        # of the machine's PATH folders alive.
        _clean_shadows()
        shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "bootstrap_execution",
        "title": "没有 Python 的机器上，这条路真的跑得通",
        "skipped": False,
        "invariant": (
            "安装器在「没有 Python」的分支上真的跑完一次安装；"
            "解释器由生产查找逻辑找回；"
            "Store 存根不被当成解释器；"
            "用户拒绝自举时安装停下来"
        ),
        "detail": "把 PATH 上的 Python 藏起来，让安装器走自举分支",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_bootstrap_path_is_executed_and_not_just_described.CHECK_NAME = "bootstrap_execution"  # type: ignore[attr-defined]

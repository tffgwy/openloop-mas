# -*- coding: utf-8 -*-
"""The twenty-second gate: the program the user types is the program
that was verified.

Every check in this project answers a question about the *code that is
running*. But the person at the keyboard does not run the code the
installer verified - they run whatever ``mas`` their PATH reaches first.
On a real machine that was:

    AppData\\Roaming\\Python\\Python314\\Scripts\\mas.exe   0.7.16
    .mas\\bin\\mas.cmd                                      0.7.81

The installer unpacked 0.7.81, ran ``mas doctor`` against it, and said
"everything is ready". Then the user typed ``mas`` and got 0.7.16. Every
check passed and the wrong program ran anyway.

So this gate watches the gap between "the code that was checked" and
"the code that is typed":

* the PATH is walked the way the shell walks it, and the winner is the
  copy the shell would really run;
* every launcher on the PATH is found, not just the first one;
* a version is read from a file on disk - a launcher that cannot be
  traced to a package is reported as *uncertain*, never as safe;
* ``mas doctor`` reports a shadow as a shadow, in its own bucket, and
  never says "everything is ready" while a shadow is there;
* ``mas shadow`` names the winner on the command line;
* a pip-managed copy is *reported*, not rewritten - rewriting it would
  make ``pip list`` start lying.

And the gate proves it can fail: the shadow detection is blinded on
purpose, and the gate has to notice that it can no longer tell a real
shadow from a hidden one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
SHADOW_OBSERVATIONS = (
    "every launcher on the PATH is found",
    "the winner is the copy the shell would run",
    "the version is read from disk, not guessed",
    "an untraceable winner is not called safe",
    "doctor reports the shadow in its own bucket",
    "the command line names the winner",
    "the upgrade reports a pip copy instead of rewriting it",
)


def _make_pip_copy(root: pathlib.Path) -> Dict[str, str]:
    """A ``pip install`` shaped copy: launcher, package and metadata."""
    base = root / "pip"
    package = base / "site-packages" / "mas"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        '__version__ = "0.7.16"\n', encoding="utf-8"
    )
    (base / "site-packages" / "openloop_mas-0.7.16.dist-info").mkdir(
        parents=True, exist_ok=True
    )
    scripts = base / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    launcher = scripts / "mas.exe"
    launcher.write_text("pip launcher\n", encoding="utf-8")
    return {
        "package": str(package),
        "scripts": str(scripts),
        "launcher": str(launcher),
    }


def _make_installer_copy(root: pathlib.Path) -> Dict[str, str]:
    """An installer shaped copy: ``<app>/mas`` plus ``<bin>/mas.cmd``."""
    from mas import __version__ as running

    base = root / "installed"
    package = base / "app" / "mas"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        '__version__ = "%s"\n' % running, encoding="utf-8"
    )
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "mas.cmd"
    launcher.write_text("installer launcher\n", encoding="utf-8")
    return {
        "package": str(package),
        "bin": str(bin_dir),
        "launcher": str(launcher),
    }


def _restore(name: str, previous: Any) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _shadow_observations(root: Any) -> Dict[str, Any]:
    """Watch what happens when the PATH holds more than one ``mas``."""
    from mas.shadow import shadow_status

    seen: Dict[str, Any] = {}
    pip_copy = _make_pip_copy(pathlib.Path(root))
    installed = _make_installer_copy(pathlib.Path(root))
    path_value = os.pathsep.join([pip_copy["scripts"], installed["bin"]])

    # The PATH as the shell sees it.
    status = shadow_status({"PATH": path_value})
    rows = status.get("launchers") or []
    seen["launcher_paths"] = [str(row.get("path") or "") for row in rows]
    seen["launcher_count"] = int(status.get("count") or 0)
    seen["winner_path"] = str((status.get("winner") or {}).get("path") or "")
    seen["winner_version"] = str((status.get("winner") or {}).get("version") or "")
    seen["versions"] = [str(v) for v in (status.get("distinct_versions") or [])]
    seen["shadowed"] = bool(status.get("shadowed"))
    seen["pip_launcher"] = pip_copy["launcher"]
    seen["installed_launcher"] = installed["launcher"]
    seen["pip_package"] = pip_copy["package"]

    # A launcher nobody can trace to a package is not safe - it is
    # unknown, and unknown is reported as unknown.
    lonely = pathlib.Path(root) / "lonely"
    lonely.mkdir(parents=True, exist_ok=True)
    (lonely / "mas.cmd").write_text("no package behind me\n", encoding="utf-8")
    lonely_status = shadow_status({"PATH": str(lonely)})
    seen["lonely_shadowed"] = bool(lonely_status.get("shadowed"))
    seen["lonely_version"] = str(
        (lonely_status.get("winner") or {}).get("version") or ""
    )

    previous_path = os.environ.get("PATH")
    previous_state = os.environ.get("MAS_STATE_DIR")
    os.environ["PATH"] = path_value
    os.environ["MAS_STATE_DIR"] = str(pathlib.Path(root) / "state")
    try:
        # The doctor has to see it too, and has to say so in its own
        # bucket - "not installed" would send the user to a command
        # that cannot possibly change which copy the PATH reaches.
        from mas.doctor import run_doctor, verdict

        results = run_doctor(allow_network=False)
        row = next(
            (r for r in results if r.get("check") == "shadow_runtime"), None
        )
        seen["doctor_status"] = str((row or {}).get("status") or "")
        seen["doctor_detail"] = str((row or {}).get("detail") or "")
        report = verdict(results)
        seen["doctor_state"] = str(report.get("state") or "")
        seen["doctor_headline"] = str(report.get("headline") or "")
        seen["doctor_bucket"] = [
            str(name) for name in (report.get("shadowed_runtime") or [])
        ]

        # The command line has to name the winner.
        from mas.cli import main

        buffer = io.StringIO()
        exit_code = 0
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            try:
                exit_code = int(main(["shadow", "--json"]) or 0)
            except SystemExit as exc:
                exit_code = int(exc.code or 0)
        seen["cli_exit"] = exit_code
        try:
            seen["cli_payload"] = json.loads(buffer.getvalue() or "{}")
        except ValueError:
            seen["cli_payload"] = {}
    finally:
        _restore("PATH", previous_path)
        _restore("MAS_STATE_DIR", previous_state)

    # A pip-managed copy is reported, not rewritten.
    from mas.upgrade import _pip_managed_package, upgrade_shadowed_runtimes

    seen["pip_managed"] = bool(
        _pip_managed_package(pathlib.Path(pip_copy["package"]))
    )
    previous_path = os.environ.get("PATH")
    os.environ["PATH"] = path_value
    try:
        upgrade = upgrade_shadowed_runtimes(pathlib.Path(root) / "state")
    finally:
        _restore("PATH", previous_path)
    seen["upgrade_removals"] = [
        str(item.get("path") or "") for item in (upgrade.get("removals") or [])
    ]
    seen["upgrade_replaced"] = [str(item) for item in (upgrade.get("replaced") or [])]
    seen["upgrade_errors"] = [str(item) for item in (upgrade.get("errors") or [])]

    return seen


def _shadow_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}
    paths = [str(p) for p in (facts.get("launcher_paths") or [])]
    verdicts["every launcher on the PATH is found"] = (
        int(facts.get("launcher_count") or 0) == 2
        and str(facts.get("pip_launcher") or "") in paths
        and str(facts.get("installed_launcher") or "") in paths
    )
    verdicts["the winner is the copy the shell would run"] = (
        str(facts.get("winner_path") or "") == str(facts.get("pip_launcher") or "")
        and bool(facts.get("shadowed"))
    )
    verdicts["the version is read from disk, not guessed"] = (
        str(facts.get("winner_version") or "") == "0.7.16"
        and "0.7.16" in [str(v) for v in (facts.get("versions") or [])]
    )
    verdicts["an untraceable winner is not called safe"] = (
        facts.get("lonely_shadowed") is True
        and str(facts.get("lonely_version") or "") == ""
    )
    verdicts["doctor reports the shadow in its own bucket"] = (
        str(facts.get("doctor_status") or "") == "warn"
        and "shadow_runtime" in [str(n) for n in (facts.get("doctor_bucket") or [])]
        and str(facts.get("doctor_state") or "") != "ready"
    )
    payload = facts.get("cli_payload") or {}
    cli_winner = str((payload.get("winner") or {}).get("path") or "")
    verdicts["the command line names the winner"] = (
        int(facts.get("cli_exit") or 0) == 1
        and cli_winner == str(facts.get("pip_launcher") or "")
        and bool(payload.get("shadowed"))
    )
    verdicts["the upgrade reports a pip copy instead of rewriting it"] = (
        facts.get("pip_managed") is True
        and str(facts.get("pip_launcher") or "")
        in [str(p) for p in (facts.get("upgrade_removals") or [])]
        and not [str(item) for item in (facts.get("upgrade_replaced") or [])]
        and not [str(item) for item in (facts.get("upgrade_errors") or [])]
    )
    return verdicts


def _shadow_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}
    paths = [str(p) for p in (facts.get("launcher_paths") or [])]
    pip_launcher = str(facts.get("pip_launcher") or "")
    verdicts["every launcher on the PATH is found"] = (
        len(paths) == 2 and pip_launcher in paths
    )
    verdicts["the winner is the copy the shell would run"] = (
        paths and paths[0] == pip_launcher and facts.get("shadowed") is True
    )
    verdicts["the version is read from disk, not guessed"] = (
        str(facts.get("winner_version") or "") == "0.7.16"
    )
    verdicts["an untraceable winner is not called safe"] = (
        facts.get("lonely_shadowed") is True
    )
    bucket = [str(n) for n in (facts.get("doctor_bucket") or [])]
    verdicts["doctor reports the shadow in its own bucket"] = (
        str(facts.get("doctor_status") or "") != "ok"
        and len(bucket) == 1
        and bucket[0] == "shadow_runtime"
    )
    payload = facts.get("cli_payload") or {}
    verdicts["the command line names the winner"] = (
        int(facts.get("cli_exit") or 0) != 0
        and str((payload.get("winner") or {}).get("path") or "") == pip_launcher
    )
    removals = [str(p) for p in (facts.get("upgrade_removals") or [])]
    verdicts["the upgrade reports a pip copy instead of rewriting it"] = (
        facts.get("pip_managed") is True
        and pip_launcher in removals
        and len([str(item) for item in (facts.get("upgrade_replaced") or [])]) == 0
    )
    return verdicts


def check_the_program_you_type_is_the_program_that_was_checked(
    iterations: int = 10,
) -> Dict[str, Any]:
    """The program the user types is the program that was checked.

    The gate proves it can fail: the shadow detection is blinded on
    purpose, and the gate has to notice.
    """
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-shadow-"))
        rounds += 1
        try:
            try:
                facts = _shadow_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the shadow check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}
            verdicts = _shadow_verdicts(facts)
            independent = _shadow_verdicts_independent(facts)
            for label in SHADOW_OBSERVATIONS:
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

            # The gate proves it can fail: the shadow detection is
            # blinded, and the gate has to notice that it can no longer
            # tell a real shadow from a hidden one.
            import mas.shadow as shadow_module

            real_status = shadow_module.shadow_status

            def _blind_status(env=None, **kwargs):
                return {
                    "running_version": "0.0.0",
                    "launchers": [],
                    "winner": None,
                    "shadowed": False,
                    "shadow_version": "",
                    "distinct_versions": [],
                    "count": 0,
                }

            try:
                shadow_module.shadow_status = _blind_status
                broken: Dict[str, Any] = {}
                try:
                    broken = _shadow_verdicts(_shadow_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                shadow_module.shadow_status = real_status
            if broken.get("doctor reports the shadow in its own bucket"):
                failures.append(
                    "round %d: the gate did not notice that the shadow is "
                    "hidden - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "shadow_guard",
        "title": "你敲 mas 跑的就是被检查过的那一份",
        "invariant": (
            "PATH 上有多份 mas 时，赢家是 shell 真会跑的那一份、版本从磁盘读出来；"
            "读不出版本就报「不确定」；doctor 用自己的一档汇报影子而不是说「没装」；"
            "pip 装的副本只报卸载命令、不偷改它的文件"
        ),
        "detail": "查 PATH 上有哪几份 mas：mas shadow",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_program_you_type_is_the_program_that_was_checked.CHECK_NAME = "shadow_guard"  # type: ignore[attr-defined]

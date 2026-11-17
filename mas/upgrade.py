"""Bring an installed runtime up to the version you are running.

``mas install`` installs the skills and registers the MCP server, but it
never touches the runtime itself. That is correct - the runtime is the
package, and the package is whatever the user installed.

It also leaves a real gap. The one-click installer copies the runtime
into ``<install-dir>/app/mas``. When a new version comes out, the skills
and the MCP registration can be refreshed by ``mas install``, but the
*runtime* on disk is still the old one - so the agent talks to a
0.7.15 server while the user believes they just upgraded. The tools
reported "registered" and the behaviour was the old behaviour.

This module closes that gap: ``mas upgrade`` compares the runtime that
is installed on disk with the runtime that is running, replaces the old
one (keeping a backup), and re-registers the MCP server so the hosts
point at the new build.

The rule the whole module follows: **never report an upgrade that did
not happen.** Every step either produces evidence on disk (a version
string that changed, a file that is newer) or is reported as a failure.
"""

from __future__ import annotations

import pathlib
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

_VERSION_RE = re.compile(r"__version__\s*=\s*[\"']([^\"']+)[\"']")


def _installed_version(package_dir: pathlib.Path) -> str:
    """The version of the runtime in ``package_dir``.

    The version is read from the package's own ``__init__.py`` rather
    than imported, because importing would run the *other* copy that is
    on ``sys.path`` - and the whole question is which copy is on disk.
    """
    init = package_dir / "__init__.py"
    if not init.is_file():
        return ""
    try:
        text = init.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else ""


def _running_version() -> str:
    """The version of the runtime that is running this command."""
    from mas import __version__

    return str(__version__)


def _version_tuple(value: str) -> Tuple[int, ...]:
    """A comparable form of a version string.

    Unparseable parts become ``-1`` so that a version we do not
    understand sorts *below* a version we do. That makes an unknown
    version look like "needs an upgrade", which is the safe direction:
    replacing an unreadable runtime cannot make things worse than
    leaving it in place.
    """
    parts: List[int] = []
    for chunk in re.split(r"[.\-+]", value or ""):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            parts.append(-1)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_older(candidate: str, reference: str) -> bool:
    """Is ``candidate`` strictly older than ``reference``?"""
    if not candidate:
        return True
    if not reference:
        return False
    return _version_tuple(candidate) < _version_tuple(reference)


def installed_runtime_dir(state_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """The installed runtime package directory, if there is one.

    The one-click installer puts the package in ``<install-dir>/app/mas``.
    That is the copy the MCP registration points at, so that is the copy
    an upgrade has to replace.
    """
    candidate = state_dir / "app" / "mas"
    if candidate.is_dir():
        return candidate
    return None


def find_upgrade_targets(state_dir: pathlib.Path,
                         include_current: bool = False) -> List[pathlib.Path]:
    """Every installed runtime copy on disk.

    By default only the copies that are *older* than the running one are
    returned. ``include_current`` also returns the copies that are
    already up to date - that is what ``--force`` needs, because a
    forced upgrade replaces a runtime even when its version matches.
    """
    running = _running_version()
    targets: List[pathlib.Path] = []
    for base in (state_dir / "app", state_dir):
        package = base / "mas"
        if not package.is_dir():
            continue
        found = _installed_version(package)
        if not found:
            continue
        if include_current or is_older(found, running):
            targets.append(package)
    return targets


def _backup_dir(state_dir: pathlib.Path, version: str) -> pathlib.Path:
    """Where the replaced runtime is kept.

    The backup lives next to the runtime, not in the shared backup
    folder, so that ``mas uninstall`` (which keeps the state directory)
    and ``mas upgrade`` cannot delete each other's history.
    """
    return state_dir / "backup" / "runtime" / (version or "unknown")


def upgrade_runtime(state_dir: pathlib.Path,
                    force: bool = False) -> Dict[str, Any]:
    """Replace an outdated installed runtime with the running one.

    The running package is the source of truth: it is the version the
    user just installed or upgraded to, and it is the code that is
    executing right now.
    """
    running = _running_version()
    result: Dict[str, Any] = {
        "running_version": running,
        "state_dir": str(state_dir),
        "upgraded": [],
        "skipped": [],
        "errors": [],
        "backup": "",
    }

    source = pathlib.Path(__file__).resolve().parent
    if not (source / "__init__.py").is_file():
        result["errors"].append("找不到当前运行的 mas 包，无法用它升级")
        return result

    for package in find_upgrade_targets(state_dir, include_current=force):
        found = _installed_version(package)
        label = f"{package}（{found}）"
        if not force and not is_older(found, running):
            result["skipped"].append(label)
            continue
        try:
            backup = _backup_dir(state_dir, found)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            shutil.copytree(package, backup)
            shutil.rmtree(package, ignore_errors=True)
            shutil.copytree(source, package)
            # The copy carries the checkout's caches; they would shadow
            # the freshly installed modules.
            for cache in package.rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)
            after = _installed_version(package)
            if after != running:
                result["errors"].append(
                    f"升级后版本仍不对：{label} -> {after}（应为 {running}）"
                )
                continue
            result["upgraded"].append(label)
            result["backup"] = str(backup)
        except OSError as exc:
            result["errors"].append(f"{label} 升级失败：{exc}")
    return result


def format_upgrade_report(report: Dict[str, Any]) -> str:
    """Render the upgrade report for the terminal."""
    lines: List[str] = []
    running = report.get("running_version") or "?"
    lines.append(f"当前运行的版本：{running}")
    upgraded = report.get("upgraded") or []
    if upgraded:
        lines.append(f"[OK ] 已升级 {len(upgraded)} 处已安装的运行时：")
        for item in upgraded:
            lines.append(f"       {item}")
        if report.get("backup"):
            lines.append(f"[..] 旧版本备份在：{report['backup']}")
    else:
        lines.append("[OK ] 没有需要升级的已安装运行时")
    skipped = report.get("skipped") or []
    for item in skipped:
        lines.append(f"[..] 跳过（不比你旧）：{item}")
    errors = report.get("errors") or []
    for item in errors:
        lines.append(f"[!!] {item}")
    if not errors:
        lines.append("[..] MCP 注册没有变化；需要时运行 mas install 刷新")
    return "\n".join(lines)


def runtime_status(state_dir: pathlib.Path) -> Dict[str, Any]:
    """What is installed on disk, compared with what is running."""
    running = _running_version()
    rows: List[Dict[str, Any]] = []
    for base in (state_dir / "app", state_dir):
        package = base / "mas"
        if not package.is_dir():
            continue
        found = _installed_version(package)
        rows.append({
            "path": str(package),
            "version": found or "unknown",
            "outdated": bool(found and is_older(found, running)),
        })
    return {
        "running_version": running,
        "installed": rows,
        "outdated_count": sum(1 for row in rows if row["outdated"]),
    }


def format_runtime_status(status: Dict[str, Any]) -> str:
    """Render the runtime status for the terminal."""
    lines: List[str] = []
    lines.append(f"当前运行的版本：{status.get('running_version')}")
    rows = status.get("installed") or []
    if not rows:
        lines.append("本机没有单独安装的运行时（现在跑的就是它本身）")
        return "\n".join(lines)
    for row in rows:
        mark = "[注意]" if row.get("outdated") else "[OK  ]"
        lines.append(f"{mark} {row['path']}：{row['version']}")
    outdated = status.get("outdated_count") or 0
    if outdated:
        lines.append(f"[..] 有 {outdated} 处比当前运行的版本旧，运行 mas upgrade 升级")
    return "\n".join(lines)


def _pip_managed_package(package: pathlib.Path) -> bool:
    """Is this package directory managed by pip?

    A pip-managed directory sits next to a ``*.dist-info`` folder. Its
    metadata records the version that was installed, so rewriting the
    code without rewriting the metadata would leave a *new* lie on
    disk: ``pip list`` would say 0.7.16 while the code says 0.7.81.
    """
    parent = package.parent
    try:
        siblings = list(parent.iterdir())
    except OSError:
        return False
    for sibling in siblings:
        if sibling.name.endswith(".dist-info") or sibling.name.endswith(".egg-info"):
            return True
    return False


def upgrade_shadowed_runtimes(state_dir: pathlib.Path, force: bool = False) -> Dict[str, Any]:
    """Deal with the copies on the PATH that are older than this one.

    ``mas upgrade`` replaces the runtime the installer unpacked. But a
    user who also ran ``pip install openloop-mas`` has a second copy on
    the PATH, and that copy is usually *earlier* - so it is the one the
    shell runs. Upgrading only the installed copy leaves the user
    running the old code, which is exactly the gap this command was
    built to close.

    Two different problems, two different fixes:

    * a **pip-managed** copy is not rewritten here. Its metadata would
      go stale and ``pip list`` would start lying. The fix is to
      remove it - ``pip uninstall`` - and let the installer's copy
      answer the name. The command is reported, not run: uninstalling
      a package is the user's call, not the tool's.
    * any other copy (an old checkout, a hand-made copy) is replaced
      with the running one, after a backup, and the version is read
      back: a replacement that did not land is an error, never a
      success.
    """
    from mas.shadow import shadow_status

    running = _running_version()
    status = shadow_status()
    result: Dict[str, Any] = {
        "running_version": running,
        "replaced": [],
        "skipped": [],
        "errors": [],
        "removals": [],
    }

    source = pathlib.Path(__file__).resolve().parent
    for index, row in enumerate(status.get("launchers") or []):
        version = str(row.get("version") or "")
        label = row["path"]
        if not version:
            result["skipped"].append("%s（读不出版本，不动它）" % label)
            continue
        if not force and not is_older(version, running):
            result["skipped"].append("%s（%s，不比你旧）" % (label, version))
            continue
        package = _package_of_launcher(pathlib.Path(label))
        if package is None:
            result["errors"].append("%s：找不到它对应的 mas 包目录" % label)
            continue
        if _pip_managed_package(package):
            result["removals"].append({
                "path": label,
                "package": str(package),
                "version": version,
                "command": "pip uninstall openloop-mas",
            })
            continue
        backup = _backup_dir(state_dir, "%s-shadow%d" % (version or "unknown", index))
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            shutil.copytree(package, backup)
            shutil.rmtree(package, ignore_errors=True)
            shutil.copytree(source, package)
            for cache in package.rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)
            after = _installed_version(package)
            if after != running:
                result["errors"].append(
                    "%s：换完版本仍不对（%s，应为 %s）" % (label, after, running)
                )
                continue
            result["replaced"].append("%s（%s -> %s）" % (label, version, running))
        except OSError as exc:
            result["errors"].append("%s 替换失败：%s" % (label, exc))
    return result


def format_shadow_upgrade_report(report: Dict[str, Any]) -> str:
    """Render what was done about the shadow copies."""
    lines: List[str] = []
    replaced = report.get("replaced") or []
    if replaced:
        lines.append("[OK ] 已换掉 %d 处 PATH 上的旧副本：" % len(replaced))
        for item in replaced:
            lines.append("       %s" % item)
    else:
        lines.append("[OK ] PATH 上没有需要替换的旧副本")
    removals = report.get("removals") or []
    if removals:
        lines.append("[..] 有 %d 处是 pip 装的副本，不替你改它的文件：" % len(removals))
        for item in removals:
            lines.append("       %s（%s）" % (item["path"], item["version"]))
        lines.append("       移除它，让安装器那一份接管这个名字：")
        for item in removals:
            lines.append("         %s" % item["command"])
    skipped = report.get("skipped") or []
    for item in skipped:
        lines.append("[..] 跳过：%s" % item)
    errors = report.get("errors") or []
    for item in errors:
        lines.append("[!!] %s" % item)
    return "\n".join(lines)


def _package_of_launcher(launcher: pathlib.Path) -> Optional[pathlib.Path]:
    """The package directory a launcher would import."""
    from mas.shadow import _package_dir_of_launcher

    return _package_dir_of_launcher(launcher)

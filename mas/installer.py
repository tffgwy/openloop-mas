"""Cross-platform self-installer: skills + MCP registration.

``pip install openloop-mas`` gives you the runtime, but a runtime alone is
not a working setup: the agent skills (so Codex / Claude Code know the
workflow) and the MCP server registration (so the tools actually appear)
live outside the Python package.

This module closes that gap. ``mas install`` does the same job as
``install.ps1`` / ``install.sh``, but it works from an installed wheel -
no clone, no shell script, same behaviour on every platform.

Design rules:

* **Never clobber.** An existing MCP entry is left alone; every file that
  is modified is backed up first.
* **Never fail the install.** A missing Codex config or an unwritable
  skill directory is reported, not raised - the runtime itself is already
  installed and usable.
* **No secrets.** Nothing written here contains an API key.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
from typing import Any, Dict, List, Tuple

MCP_SERVER_NAME = "mas_loop"

#: Skill folders shipped inside the package (``mas/skills/<name>/SKILL.md``).
PACKAGED_SKILLS = ("mas-loop", "mas-review", "mas-scaffold")


def package_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent


def skills_source() -> pathlib.Path | None:
    """The single source of truth for the bundled skills.

    ``mas/skills/`` is the *only* copy in the repository. It is the copy
    that travels inside the wheel, so ``pip install openloop-mas`` and a
    source checkout install byte-identical skills. A second copy used to
    live at the repository root, and the two could silently drift - the
    installed skill was then not the skill the repository documented.
    """
    candidate = package_root() / "skills"
    if candidate.is_dir():
        return candidate
    return None


def _home() -> pathlib.Path:
    return pathlib.Path.home()


def skill_targets() -> List[pathlib.Path]:
    """Where the skills are installed.

    Codex and Claude Code are the hosts the project documents explicitly,
    so their folders are always created - on a fresh machine the directory
    simply does not exist yet. The other hosts are opt-in: they are only
    touched when the user already uses them.
    """
    home = _home()
    always = [home / ".codex" / "skills", home / ".claude" / "skills"]
    optional = [home / ".agents" / "skills", home / ".gemini" / "skills"]
    targets = list(always)
    targets.extend(path for path in optional if path.exists())
    return targets


def _backup(path: pathlib.Path) -> pathlib.Path | None:
    """Copy ``path`` aside before it is modified.

    Both files and folders have to work here: the MCP registrations are
    files, but the skill folders are directories, and a backup that only
    handles files silently returns nothing for the thing you most want to
    keep.
    """
    import datetime

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        if path.is_dir():
            shutil.copytree(path, backup, dirs_exist_ok=True)
        else:
            shutil.copy2(path, backup)
    except OSError:
        return None
    return backup


def _host_label(target_root: pathlib.Path) -> str:
    """A readable label for a skill target directory.

    ``~/.codex/skills`` and ``~/.claude/skills`` both end in ``skills``,
    so the directory name alone cannot tell the hosts apart.
    """
    parts = target_root.parts
    if len(parts) >= 2 and parts[-1] == "skills":
        return parts[-2]
    return target_root.name


def skill_fingerprint(path: pathlib.Path) -> str:
    """A stable fingerprint of a skill folder's file contents.

    Hashing the *content* (not the mtime) is what makes drift detection
    meaningful: a file copied with a different timestamp but identical
    bytes is not drift, and a file edited by hand is drift even though
    its name is unchanged.
    """
    import hashlib

    digest = hashlib.sha256()
    if not path.is_dir():
        return "missing"
    files = sorted(p for p in path.rglob("*") if p.is_file())
    for item in files:
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def skill_sync_report() -> Dict[str, Any]:
    """Compare the packaged skills against every installed copy.

    The packaged copy is the truth. An installed copy that no longer
    matches it is *drift*: the user is running a skill that the runtime
    does not know about. This is the failure mode that made the 0.6.0
    copies survive a 0.7.0 upgrade unnoticed - nothing in the toolchain
    looked at the content.
    """
    source = skills_source()
    result: Dict[str, Any] = {"source": str(source) if source else "", "rows": []}
    if source is None:
        result["error"] = "未找到内置 skills 目录"
        return result
    names = [name for name in PACKAGED_SKILLS if (source / name).is_dir()]
    names.extend(
        sorted(p.name for p in source.iterdir() if p.is_dir() and p.name not in names)
    )
    for target_root in skill_targets():
        for name in names:
            expected = skill_fingerprint(source / name)
            dest = target_root / name
            actual = skill_fingerprint(dest) if dest.is_dir() else "missing"
            result["rows"].append(
                {
                    "host": _host_label(target_root),
                    "path": str(dest),
                    "skill": name,
                    "expected": expected,
                    "actual": actual,
                    "status": "match" if expected == actual else "drift",
                }
            )
    return result


def install_skills() -> Dict[str, Any]:
    """Copy the bundled skills into every target skill directory."""
    source = skills_source()
    result: Dict[str, Any] = {
        "source": str(source) if source else "",
        "installed": [],
        "skipped": [],
        "backups": [],
        "errors": [],
    }
    if source is None:
        result["errors"].append("未找到内置 skills 目录")
        return result
    # The known skills go first, in a stable order, so the report is
    # reproducible; anything else found in the source directory is added
    # afterwards (a third-party skill folder dropped into the package).
    names = [name for name in PACKAGED_SKILLS if (source / name).is_dir()]
    names.extend(
        sorted(
            p.name for p in source.iterdir() if p.is_dir() and p.name not in names
        )
    )
    for name in names:
        skill_dir = source / name
        for target_root in skill_targets():
            dest = target_root / skill_dir.name
            try:
                # A previous copy is replaced, not merged. Backing it up
                # first means an upgrade never silently discards a skill
                # the user edited by hand.
                existed = dest.is_dir()
                stale = existed and skill_fingerprint(dest) != skill_fingerprint(skill_dir)
                if stale:
                    backup = _backup(dest)
                    if backup is not None:
                        result["backups"].append(str(backup))
                    # Remove files that the new version no longer has;
                    # copying alone would leave stale content behind.
                    shutil.rmtree(dest, ignore_errors=True)
                dest.mkdir(parents=True, exist_ok=True)
                for item in skill_dir.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest / item.name)
                result["installed"].append(str(dest))
            except OSError as exc:
                result["errors"].append(f"{dest}: {exc}")
    return result


def _mcp_entry(python_exe: str) -> Dict[str, Any]:
    return {
        "command": python_exe,
        "args": ["-m", "mas", "serve"],
        "env": {"PYTHONPATH": str(package_root().parent)},
    }


def _codex_block_text(python_exe: str) -> str:
    """The exact ``[mcp_servers.mas_loop]`` block we want to be present."""
    return (
        f"\n[mcp_servers.{MCP_SERVER_NAME}]\n"
        f'command = "{python_exe.replace(chr(92), "/")}"\n'
        'args = ["-m", "mas", "serve"]\n'
        "startup_timeout_sec = 30.0\n"
        "tool_timeout_sec = 300.0\n"
        f'env = {{ PYTHONPATH = "{str(package_root().parent).replace(chr(92), "/")}" }}\n'
    )


def _strip_codex_block(text: str) -> str:
    """Remove the ``mas_loop`` block, keeping every other setting."""
    header = f"[mcp_servers.{MCP_SERVER_NAME}]"
    kept: List[str] = []
    skipping = False
    for line in text.splitlines():
        if line.strip() == header:
            skipping = True
            continue
        if skipping:
            if line.strip().startswith("["):
                skipping = False
            else:
                continue
        kept.append(line)
    return "\n".join(kept).rstrip("\n")


def _codex_block_fields(text: str) -> Tuple[str, str]:
    """Extract ``(command, PYTHONPATH)`` from the registered codex block."""
    import re

    header = f"[mcp_servers.{MCP_SERVER_NAME}]"
    lines = text.splitlines()
    block: List[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == header:
            inside = True
            continue
        if inside:
            if stripped.startswith("["):
                break
            block.append(stripped)
    command = ""
    pythonpath = ""
    for line in block:
        match = re.match(r'command\s*=\s*"(.*)"', line)
        if match:
            command = match.group(1)
        match = re.match(r'env\s*=\s*\{.*PYTHONPATH\s*=\s*"(.*)".*\}', line)
        if match:
            pythonpath = match.group(1)
    return command, pythonpath


def _runtime_is_usable(command: str, pythonpath: str) -> bool:
    """Can this registration actually start the MCP server?

    The question is not "does it equal what we would write right now" -
    it is "does it work". A registration is usable when

    * the interpreter exists on disk, and
    * the package path it points at actually contains the ``mas``
      package (or no path is given, in which case the interpreter's own
      site-packages has to provide it).

    Judging a registration by comparing it against the *currently
    imported* module would be wrong: running ``mas`` from a source
    checkout must not declare a perfectly good installation in
    ``~/.mas/app`` broken.
    """
    if not command:
        return False
    if not pathlib.Path(command).exists():
        return False
    if not pythonpath:
        # No explicit path: the interpreter has to be able to import mas
        # on its own (a pip-installed package).
        import subprocess

        try:
            proc = subprocess.run(
                [command, "-c", "import mas"], capture_output=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0
    root = pathlib.Path(pythonpath)
    return (root / "mas" / "__init__.py").exists()


def _codex_block_is_current(text: str, python_exe: str) -> bool:
    """Is the registered ``mas_loop`` block usable?

    Kept as the historical name; the meaning is "the registration can
    start the server", not "it matches this exact build".
    """
    command, pythonpath = _codex_block_fields(text)
    if not command:
        return False
    return _runtime_is_usable(command, pythonpath)


def _register_codex(python_exe: str) -> Tuple[str, str]:
    """Register the MCP server in ``~/.codex/config.toml``.

    An existing entry is only left alone when it can actually start the
    MCP server. A registration that names a Python that no longer exists
    - or a package path from an uninstalled copy - is dead: the server
    would fail to start while every self-check still says "registered".
    So a dead entry is replaced instead of trusted.
    """
    config = _home() / ".codex" / "config.toml"
    block = _codex_block_text(python_exe)
    try:
        config.parent.mkdir(parents=True, exist_ok=True)
        existing = config.read_text(encoding="utf-8") if config.exists() else ""
        if f"[mcp_servers.{MCP_SERVER_NAME}]" in existing:
            if _codex_block_is_current(existing, python_exe):
                return "already", str(config)
            # The entry cannot start the server: keep the other settings,
            # swap our block.
            _backup(config)
            config.write_text(
                _strip_codex_block(existing) + "\n" + block.lstrip("\n"),
                encoding="utf-8",
            )
            return "updated", str(config)
        if config.exists():
            _backup(config)
        with config.open("a", encoding="utf-8") as handle:
            handle.write(block)
        return "registered", str(config)
    except OSError as exc:
        return "error", f"{config}: {exc}"


def _claude_entry_is_current(entry: Any, python_exe: str) -> bool:
    """Is the registered Claude MCP entry usable?"""
    if not isinstance(entry, dict):
        return False
    command = str(entry.get("command") or "")
    env = entry.get("env")
    pythonpath = ""
    if isinstance(env, dict):
        pythonpath = str(env.get("PYTHONPATH") or "")
    return _runtime_is_usable(command, pythonpath)


def _register_claude(python_exe: str) -> Tuple[str, str]:
    """Register the MCP server in ``~/.claude.json``."""
    config = _home() / ".claude.json"
    try:
        data: Dict[str, Any] = {}
        if config.exists():
            try:
                loaded = json.loads(config.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError):
                data = {}
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            data["mcpServers"] = servers
        if MCP_SERVER_NAME in servers:
            if _claude_entry_is_current(servers[MCP_SERVER_NAME], python_exe):
                return "already", str(config)
            # Dead entry (missing interpreter / stale package path):
            # replace it instead of reporting a registration that cannot
            # start.
            _backup(config)
            servers[MCP_SERVER_NAME] = _mcp_entry(python_exe)
            config.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return "updated", str(config)
        if config.exists():
            _backup(config)
        servers[MCP_SERVER_NAME] = _mcp_entry(python_exe)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return "registered", str(config)
    except OSError as exc:
        return "error", f"{config}: {exc}"


def register_mcp(python_exe: str | None = None) -> Dict[str, Any]:
    """Register the MCP server with every host that supports it."""
    python = python_exe or sys.executable
    codex_state, codex_detail = _register_codex(python)
    claude_state, claude_detail = _register_claude(python)
    return {
        "python": python,
        "codex": {"state": codex_state, "detail": codex_detail},
        "claude": {"state": claude_state, "detail": claude_detail},
    }


def run_install(python_exe: str | None = None, skip_skills: bool = False,
                skip_mcp: bool = False) -> Dict[str, Any]:
    """Install the skills and register the MCP server."""
    report: Dict[str, Any] = {"skills": {}, "mcp": {}}
    if not skip_skills:
        report["skills"] = install_skills()
    if not skip_mcp:
        report["mcp"] = register_mcp(python_exe)
    return report


def format_report(report: Dict[str, Any]) -> str:
    """Render the install report for the terminal."""
    lines: List[str] = []
    skills = report.get("skills") or {}
    if skills:
        installed = skills.get("installed") or []
        lines.append(f"[OK ] skills installed: {len(installed)}")
        for backup in skills.get("backups") or []:
            lines.append(f"[..] previous copy backed up: {backup}")
        for error in skills.get("errors") or []:
            lines.append(f"[!!] {error}")
    mcp = report.get("mcp") or {}
    if mcp:
        for host in ("codex", "claude"):
            entry = mcp.get(host) or {}
            state = entry.get("state")
            detail = entry.get("detail")
            if state == "already":
                lines.append(f"[OK ] MCP already registered ({host}): {detail}")
            elif state == "updated":
                lines.append(f"[OK ] MCP registration refreshed ({host}): {detail}")
            elif state == "registered":
                lines.append(f"[OK ] MCP registered ({host}): {detail}")
            else:
                lines.append(f"[!!] MCP registration failed ({host}): {detail}")
    return "\n".join(lines)


def run_sync(dry_run: bool = False) -> Dict[str, Any]:
    """Report - and optionally repair - skill drift.

    ``dry_run`` answers the question "what is out of date?" without
    touching anything; without it the drifted copies are replaced with
    the packaged ones (the old copy is backed up first).
    """
    before = skill_sync_report()
    drifted = [row for row in (before.get("rows") or []) if row.get("status") == "drift"]
    result: Dict[str, Any] = {
        "source": before.get("source", ""),
        "drift": drifted,
        "fixed": [],
        "errors": [],
        "dry_run": dry_run,
    }
    if not drifted:
        return result
    if dry_run:
        return result
    install = install_skills()
    result["fixed"] = install.get("installed") or []
    result["backups"] = install.get("backups") or []
    result["errors"] = install.get("errors") or []
    after = skill_sync_report()
    result["after"] = after.get("rows") or []
    result["remaining"] = [
        row for row in result["after"] if row.get("status") == "drift"
    ]
    return result


def format_sync_report(report: Dict[str, Any]) -> str:
    """Render the sync report for the terminal."""
    lines: List[str] = []
    drift = report.get("drift") or []
    if not drift:
        lines.append("[OK ] 已安装的 skills 与包内版本一致，无需同步")
        return "\n".join(lines)
    lines.append(f"[!!] 发现 {len(drift)} 处漂移（已安装内容 != 包内版本）:")
    for row in drift:
        lines.append(
            f"       {row['host']}/{row['skill']}  {row['path']}"
        )
    if report.get("dry_run"):
        lines.append("[..] dry-run：未写入任何文件。运行 mas sync 修复。")
        return "\n".join(lines)
    fixed = report.get("fixed") or []
    lines.append(f"[OK ] 已同步 {len(fixed)} 处")
    for backup in report.get("backups") or []:
        lines.append(f"[..] 旧副本已备份: {backup}")
    for error in report.get("errors") or []:
        lines.append(f"[!!] {error}")
    remaining = report.get("remaining") or []
    if remaining:
        lines.append(f"[!!] 仍有 {len(remaining)} 处未对齐，请检查上面的路径权限")
    return "\n".join(lines)


def uninstall_skills() -> Dict[str, Any]:
    """Remove the MAS skill folders from every target skill directory."""
    result: Dict[str, Any] = {"removed": [], "errors": []}
    for target_root in skill_targets():
        for name in PACKAGED_SKILLS:
            dest = target_root / name
            if not dest.is_dir():
                continue
            try:
                shutil.rmtree(dest)
                result["removed"].append(str(dest))
            except OSError as exc:
                result["errors"].append(f"{dest}: {exc}")
    return result


def _unregister_codex() -> Tuple[str, str]:
    """Remove the MCP server block from ``~/.codex/config.toml``."""
    config = _home() / ".codex" / "config.toml"
    if not config.exists():
        return "absent", str(config)
    text = config.read_text(encoding="utf-8", errors="ignore")
    header = f"[mcp_servers.{MCP_SERVER_NAME}]"
    if header not in text:
        return "absent", str(config)
    kept: List[str] = []
    skipping = False
    for line in text.splitlines():
        if line.strip() == header:
            skipping = True
            continue
        if skipping:
            if line.strip().startswith("["):
                skipping = False
            else:
                continue
        kept.append(line)
    try:
        _backup(config)
        config.write_text(
            ("\n".join(kept).rstrip("\n") + "\n") if kept else "",
            encoding="utf-8",
        )
    except OSError as exc:
        return "error", f"{config}: {exc}"
    return "removed", str(config)


def _unregister_claude() -> Tuple[str, str]:
    """Remove the MCP server entry from ``~/.claude.json``."""
    config = _home() / ".claude.json"
    if not config.exists():
        return "absent", str(config)
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return "error", f"{config}: {exc}"
    if not isinstance(data, dict):
        return "absent", str(config)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or MCP_SERVER_NAME not in servers:
        return "absent", str(config)
    servers.pop(MCP_SERVER_NAME, None)
    try:
        _backup(config)
        config.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        return "error", f"{config}: {exc}"
    return "removed", str(config)


def unregister_mcp() -> Dict[str, Any]:
    """Remove the MCP server registration from every supported host."""
    codex_state, codex_detail = _unregister_codex()
    claude_state, claude_detail = _unregister_claude()
    return {
        "codex": {"state": codex_state, "detail": codex_detail},
        "claude": {"state": claude_state, "detail": claude_detail},
    }


def run_uninstall(skip_skills: bool = False, skip_mcp: bool = False) -> Dict[str, Any]:
    """Remove the skills and the MCP registration.

    The runtime itself (the pip package, the ``~/.mas`` state directory) is
    left alone on purpose: ``pip uninstall openloop-mas`` handles the
    package, and ``~/.mas`` holds the ledger - the history of every closed
    loop - which is the one thing that cannot be regenerated.
    """
    report: Dict[str, Any] = {"skills": {}, "mcp": {}}
    if not skip_skills:
        report["skills"] = uninstall_skills()
    if not skip_mcp:
        report["mcp"] = unregister_mcp()
    return report


def format_uninstall_report(report: Dict[str, Any]) -> str:
    """Render the uninstall report for the terminal."""
    lines: List[str] = []
    skills = report.get("skills") or {}
    if skills:
        removed = skills.get("removed") or []
        lines.append(f"[OK ] skill folders removed: {len(removed)}")
        for path in removed:
            lines.append(f"       {path}")
        for error in skills.get("errors") or []:
            lines.append(f"[!!] {error}")
    mcp = report.get("mcp") or {}
    if mcp:
        for host in ("codex", "claude"):
            entry = mcp.get(host) or {}
            state = entry.get("state")
            detail = entry.get("detail")
            if state == "removed":
                lines.append(f"[OK ] MCP registration removed ({host}): {detail}")
            elif state == "absent":
                lines.append(f"[OK ] no MCP registration to remove ({host}): {detail}")
            else:
                lines.append(f"[!!] MCP removal failed ({host}): {detail}")
    lines.append("[..] the runtime and ~/.mas (ledger, tickets, config) were kept")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(format_report(run_install()))

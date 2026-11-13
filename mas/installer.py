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
    """Copy ``path`` aside before it is modified."""
    import datetime

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        shutil.copy2(path, backup)
    except OSError:
        return None
    return backup


def install_skills() -> Dict[str, Any]:
    """Copy the bundled skills into every target skill directory."""
    source = skills_source()
    result: Dict[str, Any] = {
        "source": str(source) if source else "",
        "installed": [],
        "skipped": [],
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


def _register_codex(python_exe: str) -> Tuple[str, str]:
    """Register the MCP server in ``~/.codex/config.toml``."""
    config = _home() / ".codex" / "config.toml"
    block = (
        f"\n[mcp_servers.{MCP_SERVER_NAME}]\n"
        f'command = "{python_exe.replace(chr(92), "/")}"\n'
        'args = ["-m", "mas", "serve"]\n'
        "startup_timeout_sec = 30.0\n"
        "tool_timeout_sec = 300.0\n"
        f'env = {{ PYTHONPATH = "{str(package_root().parent).replace(chr(92), "/")}" }}\n'
    )
    try:
        config.parent.mkdir(parents=True, exist_ok=True)
        existing = config.read_text(encoding="utf-8") if config.exists() else ""
        if f"[mcp_servers.{MCP_SERVER_NAME}]" in existing:
            return "already", str(config)
        if config.exists():
            _backup(config)
        with config.open("a", encoding="utf-8") as handle:
            handle.write(block)
        return "registered", str(config)
    except OSError as exc:
        return "error", f"{config}: {exc}"


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
            return "already", str(config)
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
            elif state == "registered":
                lines.append(f"[OK ] MCP registered ({host}): {detail}")
            else:
                lines.append(f"[!!] MCP registration failed ({host}): {detail}")
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

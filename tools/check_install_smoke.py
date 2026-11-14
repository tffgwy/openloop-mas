"""Version-agnostic smoke check for an installed runtime.

``verify_install.py`` asserts the *current* registration format. The
standalone installer can also be pointed at an *already published* wheel,
whose installer predates that format - the runtime works, the contract
just is not the current one yet. Asserting the current format against an
old release would make the check fail for a runtime that is perfectly
usable.

So this script asserts only what every release must satisfy:

* the runtime is on disk and starts;
* the MCP server is registered in both host configurations;
* the agent skills are installed.

Usage:
    python tools/check_install_smoke.py <home-dir>
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    home = pathlib.Path(argv[1]).expanduser().resolve()
    app = home / ".mas" / "app"
    entry = app / "mas" / "__main__.py"

    if not entry.exists():
        print(f"[FAIL] the runtime was not installed at {entry}")
        return 1
    print(f"[OK] runtime installed: {app}")

    version = subprocess.run(
        [sys.executable, "-X", "utf8", str(entry), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if version.returncode != 0:
        print(f"[FAIL] the installed runtime does not start: {version.stderr[-400:]}")
        return 1
    print(f"[OK] the runtime starts: {version.stdout.strip()}")

    codex = home / ".codex" / "config.toml"
    if not codex.exists() or "[mcp_servers.mas_loop]" not in codex.read_text(encoding="utf-8"):
        print(f"[FAIL] mas_loop is not registered in {codex}")
        return 1
    print("[OK] mas_loop is registered in ~/.codex/config.toml")

    claude = home / ".claude.json"
    if not claude.exists():
        print(f"[FAIL] {claude} was not created")
        return 1
    data = json.loads(claude.read_text(encoding="utf-8"))
    if "mas_loop" not in data.get("mcpServers", {}):
        print("[FAIL] mas_loop is not registered in ~/.claude.json")
        return 1
    print("[OK] mas_loop is registered in ~/.claude.json")

    for skill in ("mas-loop", "mas-review", "mas-scaffold"):
        for host in (".codex", ".claude"):
            path = home / host / "skills" / skill
            if not path.exists():
                print(f"[FAIL] skill {skill} missing for {host}: {path}")
                return 1
    print("[OK] the agent skills are installed for both hosts")

    print("[OK] the standalone install is usable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

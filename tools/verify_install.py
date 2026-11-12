"""Assert that a *fresh* install actually produced a working setup.

The installer used to register the MCP server only when
``~/.codex/config.toml`` already existed. On a brand new machine that
file does not exist, so the step was skipped silently and the user got a
"successful" install with no MCP tools and no skills - which looks
exactly like a successful install.

This script is what the ``install`` CI workflow runs against a pristine
HOME. It checks the three things that used to break:

1. the MCP server is registered in both host configs;
2. the agent skills are actually on disk;
3. the registered MCP server really starts and answers.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


def check_files(home: pathlib.Path) -> None:
    codex = home / ".codex" / "config.toml"
    claude = home / ".claude.json"

    assert codex.exists(), f"~/.codex/config.toml was not created: {codex}"
    text = codex.read_text(encoding="utf-8")
    assert "[mcp_servers.mas_loop]" in text, (
        "mas_loop is not registered in ~/.codex/config.toml"
    )

    assert claude.exists(), f"~/.claude.json was not created: {claude}"
    data = json.loads(claude.read_text(encoding="utf-8"))
    assert "mas_loop" in data.get("mcpServers", {}), (
        "mas_loop is not registered in ~/.claude.json"
    )

    for skill in ("mas-loop", "mas-review", "mas-scaffold"):
        for host in (".codex", ".claude"):
            path = home / host / "skills" / skill
            assert path.exists(), f"skill {skill} missing for {host}: {path}"

    print("[OK] install assertions passed")


def check_mcp(home: pathlib.Path) -> None:
    app = home / ".mas" / "app"
    assert app.exists(), f"runtime not installed: {app}"

    env = dict(os.environ, PYTHONPATH=str(app))
    proc = subprocess.Popen(
        [sys.executable, "-m", "mas", "serve"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "verify-install", "version": "1"},
                },
            }
        )
        reply = json.loads(proc.stdout.readline())
        info = reply.get("result", {}).get("serverInfo", {})
        assert info.get("name") == "openloop-mas", reply

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        reply = json.loads(proc.stdout.readline())
        names = [tool["name"] for tool in reply.get("result", {}).get("tools", [])]
        assert "mas_run" in names, names
        assert "mas_doctor" in names, names
        print(f"[OK] MCP server answered with {len(names)} tools: {names}")
    finally:
        proc.terminate()


def check_runtime(home: pathlib.Path) -> None:
    """The installed runtime must work, not just sit on disk."""
    app = home / ".mas" / "app"
    env = dict(os.environ, PYTHONPATH=str(app))
    for args in (
        ["--version"],
        ["doctor", "--no-network"],
        ["demo"],
        ["bench", "--repeats", "1"],
    ):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", *args],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"mas {' '.join(args)} failed with {result.returncode}: "
            f"{(result.stdout + result.stderr)[-600:]}"
        )
        print(f"[OK] mas {' '.join(args)}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_install.py <home-dir>")
        return 2
    home = pathlib.Path(sys.argv[1]).expanduser().resolve()
    check_files(home)
    check_mcp(home)
    check_runtime(home)
    print("[OK] fresh-install verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

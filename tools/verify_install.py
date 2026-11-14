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
import re
import subprocess
import sys


def _run_in_installed_env(
    home: pathlib.Path,
    args: list[str],
    cwd: pathlib.Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run the *installed* runtime, never the source checkout.

    ``python -m mas`` puts the current working directory first on
    ``sys.path``. If the verifier runs from the repository root, the
    repository's ``mas/`` package shadows the freshly installed one and
    every assertion below would be testing the source tree instead of
    the install product - a green CI run that proves nothing about what
    a user actually gets.

    The fix is to run from a directory that contains no ``mas`` package
    and to point ``PYTHONPATH`` at the installed runtime.
    """
    app = home / ".mas" / "app"
    env = dict(os.environ, PYTHONPATH=str(app))
    if extra_env:
        env.update(extra_env)
    # A scratch directory outside any checkout: importing `mas` there can
    # only ever resolve to the installed copy.
    scratch = home / ".verify-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(app / "mas" / "__main__.py"), *args],
        env=env,
        cwd=str(cwd or scratch),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


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

    scratch = home / ".verify-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(app))
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(app / "mas" / "__main__.py"), "serve"],
        cwd=str(scratch),
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


def check_config_isolation(home: pathlib.Path) -> None:
    """The installation must not read somebody else's configuration.

    The runtime keeps the *user* configuration in a well-known place
    (``~/.mas/config.json``) and the *state* in a state directory. An
    installation into a custom directory therefore has to point at its
    own files explicitly - otherwise the CLI and the agent read two
    different configurations and the agent silently talks to another
    provider.
    """
    install_dir = home / ".mas"
    config_file = install_dir / "config.json"
    assert config_file.exists(), f"the installation has no configuration: {config_file}"
    expected = json.loads(config_file.read_text(encoding="utf-8"))
    expected_provider = str(expected.get("provider") or "mock")

    # The launcher sets MAS_CONFIG_PATH; the runtime has to honour it.
    result = _run_in_installed_env(
        home,
        ["doctor", "--no-network", "--json"],
        extra_env={
            "MAS_CONFIG_PATH": str(config_file),
            "MAS_STATE_DIR": str(install_dir),
        },
    )
    assert result.returncode == 0, result.stderr[-400:]
    payload = json.loads(result.stdout)
    rows = {row["check"]: row for row in payload["checks"]}
    provider_row = rows.get("provider")
    assert provider_row is not None, payload
    assert expected_provider in provider_row["detail"], (
        f"doctor reports {provider_row['detail']!r} but the installation is "
        f"configured for {expected_provider!r} - the configuration leaked"
    )
    config_row = rows.get("config")
    assert config_row is not None, payload
    assert str(config_file) in config_row["detail"], (
        f"doctor reads {config_row['detail']!r}, not {config_file}"
    )
    print(f"[OK] the runtime reads the installation's own configuration: {config_file}")

    # The MCP registration has to carry the same location, otherwise the
    # host starts the server against the well-known configuration.
    codex_text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    env = _toml_env(codex_text)
    assert env.get("MAS_CONFIG_PATH"), (
        "the codex MCP registration does not carry MAS_CONFIG_PATH: " + codex_text
    )
    assert env.get("MAS_STATE_DIR"), (
        "the codex MCP registration does not carry MAS_STATE_DIR: " + codex_text
    )

    claude_data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    claude_env = claude_data["mcpServers"]["mas_loop"].get("env") or {}
    assert claude_env.get("MAS_CONFIG_PATH"), claude_data
    assert claude_env.get("MAS_STATE_DIR"), claude_data
    print("[OK] both MCP registrations carry the configuration location")

    # The registration has to point at *this* installation. A registration
    # that points at a source checkout works right up until the checkout
    # is deleted - and until then it silently serves the wrong code.
    app = home / ".mas" / "app"
    for label, pythonpath in (
        ("codex", env.get("PYTHONPATH", "")),
        ("claude", claude_env.get("PYTHONPATH", "")),
    ):
        assert pythonpath, f"the {label} registration carries no PYTHONPATH"
        assert pathlib.Path(pythonpath).resolve() == app.resolve(), (
            f"the {label} registration points at {pythonpath!r}, not the "
            f"installed runtime {app}"
        )
    print(f"[OK] both MCP registrations point at the installed runtime: {app}")

    # The registered command has to be startable *right now*. A host that
    # cannot start the server shows an empty tool list, which looks like
    # the agent has no tools rather than like a broken installation.
    command = _toml_command(codex_text)
    assert command, codex_text
    assert pathlib.Path(command).exists(), (
        f"the registered interpreter does not exist: {command}"
    )
    entry = _toml_args(codex_text)
    assert entry, codex_text
    assert pathlib.Path(entry[0]).exists(), (
        f"the registered entry point does not exist: {entry[0]}"
    )
    print(f"[OK] the registered command can start: {command} {entry[0]}")


def _toml_command(text: str) -> str:
    """Read the ``command`` out of the registered codex MCP block."""
    for line in _codex_lines(text):
        match = re.match(r'command\s*=\s*"(.*)"', line)
        if match:
            return match.group(1)
    return ""


def _toml_args(text: str) -> list[str]:
    """Read the ``args`` list out of the registered codex MCP block."""
    for line in _codex_lines(text):
        match = re.match(r"args\s*=\s*\[(.*)\]\s*$", line)
        if match:
            return re.findall(r'"([^"]*)"', match.group(1))
    return []


def _codex_lines(text: str) -> list[str]:
    """The lines of the ``[mcp_servers.mas_loop]`` block, without the header."""
    inside = False
    block: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[mcp_servers.mas_loop]":
            inside = True
            continue
        if inside:
            if stripped.startswith("["):
                break
            block.append(stripped)
    return block


def _toml_env(text: str) -> dict[str, str]:
    """Read the env mapping out of the registered codex MCP block.

    Every key is matched on its own: a greedy match over the whole line
    captures the tail of it and produces a garbage path.
    """
    env: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[mcp_servers.mas_loop]":
            inside = True
            continue
        if inside:
            if stripped.startswith("["):
                break
            match = re.match(r"env\s*=\s*\{(.*)\}\s*$", stripped)
            if match:
                for key, value in re.findall(r'(\w+)\s*=\s*"([^"]*)"', match.group(1)):
                    env[key] = value
    return env


def check_runtime(home: pathlib.Path) -> None:
    """The installed runtime must work, not just sit on disk."""
    app = home / ".mas" / "app"
    # The installed version must match the package version, otherwise a
    # stale runtime copy in ~/.mas/app could silently shadow the new one.
    version_result = _run_in_installed_env(home, ["--version"])
    assert version_result.returncode == 0, version_result.stderr[-400:]
    # Read the expected version straight out of the *installed* runtime,
    # not from this process' import path - the parent interpreter does not
    # have the freshly installed package on its sys.path.
    init_file = app / "mas" / "__init__.py"
    assert init_file.exists(), f"runtime package missing: {init_file}"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_file.read_text(encoding="utf-8"))
    assert match, f"cannot read __version__ from {init_file}"
    installed_version = match.group(1)

    expected = f"openloop-mas {installed_version}"
    assert expected in version_result.stdout, (
        f"version mismatch: expected {expected!r}, got {version_result.stdout.strip()!r}"
    )
    print(f"[OK] mas --version -> {version_result.stdout.strip()}")

    # The import must resolve to the *installed* package, not to a source
    # checkout that happens to be the current directory.
    probe = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", "import mas; print(mas.__file__)"],
        env=dict(os.environ, PYTHONPATH=str(app)),
        cwd=str(home / ".verify-scratch"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    mas_file = probe.stdout.strip()
    assert mas_file.startswith(str(app)), (
        "the 'mas' package resolved outside the installed runtime: " + mas_file
    )
    print(f"[OK] 'import mas' resolves to the installed runtime: {mas_file}")

    for args in (
        ["doctor", "--no-network"],
        ["demo"],
        ["bench", "--repeats", "1"],
    ):
        result = _run_in_installed_env(home, args)
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
    check_config_isolation(home)
    check_runtime(home)
    print("[OK] fresh-install verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

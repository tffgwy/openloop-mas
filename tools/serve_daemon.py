"""Helper for running the OpenLoop MAS HTTP/SSE transport as a long-lived service.

The runtime itself is a plain foreground process (`mas http`). This helper
adds the three things a long-lived deployment actually needs, without
requiring any third-party package:

1. A **stable token file** (``~/.mas/http_token.txt``) generated once, so the
   token survives restarts and does not have to be pasted into a unit file.
2. **Fail-closed by construction**: the service only ever binds to loopback;
   putting it on a LAN address is deliberately refused, because the only
   supported way to expose it is a TLS-terminating reverse proxy.
3. **A portable launcher** for systemd (Linux), launchd (macOS) and the
   Windows Startup folder, so the same token file is reused by all of them.

Usage:
    python tools/serve_daemon.py token                 # print the token
    python tools/serve_daemon.py install --port 8768    # write a launcher
    python tools/serve_daemon.py status --port 8768
    python tools/serve_daemon.py uninstall

No third-party imports: the standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import secrets
import shutil
import socket
import sys

TOKEN_FILE = pathlib.Path.home() / ".mas" / "http_token.txt"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8768


def read_or_create_token() -> str:
    """Return the persisted token, creating it on first use."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass  # Windows does not honour POSIX modes.
    return token


def mas_command() -> str:
    """Locate the `mas` launcher produced by the installer."""
    candidate = pathlib.Path.home() / ".mas" / "bin" / "mas.cmd"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("mas")
    if found:
        return found
    return "mas"


def launcher_path() -> pathlib.Path:
    system = os.name
    if system == "nt":
        startup = pathlib.Path.home() / (
            "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
        )
        return startup / "OpenLoop-MAS-Http.cmd"
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library/LaunchAgents/dev.openloop.mas.http.plist"
    return pathlib.Path.home() / ".config/systemd/user/openloop-mas-http.service"


def wrapper_path() -> pathlib.Path:
    """Path of the POSIX shell wrapper that reads the token file."""
    if sys.platform == "darwin":
        return pathlib.Path.home() / ".mas" / "run-http.sh"
    return pathlib.Path.home() / ".config" / "openloop-mas" / "run-http.sh"


def _shell_wrapper(port: int) -> str:
    """A POSIX shell wrapper that reads the token from the token file.

    Both systemd and launchd can invoke a wrapper script, so the token is
    never written into a unit file - it stays in ``~/.mas/http_token.txt``
    with 0600 permissions, and the unit only ever references the path.
    """
    return (
        "#!/bin/sh\n"
        "# OpenLoop MAS HTTP/SSE transport (persistent).\n"
        "# Binds loopback only; expose it through a TLS reverse proxy.\n"
        "set -eu\n"
        f'TOKEN_FILE="{TOKEN_FILE}"\n'
        "if [ ! -r \"$TOKEN_FILE\" ]; then\n"
        '  echo "[ERROR] token file not readable: $TOKEN_FILE" >&2\n'
        "  exit 1\n"
        "fi\n"
        'MAS_HTTP_TOKEN="$(cat "$TOKEN_FILE")"\n'
        'export MAS_HTTP_TOKEN\n'
        'if [ -z "$MAS_HTTP_TOKEN" ]; then\n'
        '  echo "[ERROR] token file is empty: $TOKEN_FILE" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'exec "{mas_command()}" http --host {DEFAULT_HOST} --port {port}\n'
    )


def launcher_body(port: int) -> str:
    if os.name == "nt":
        return (
            "@echo off\r\n"
            "REM OpenLoop MAS HTTP/SSE transport (persistent).\r\n"
            "REM Binds loopback only; expose it through a TLS reverse proxy.\r\n"
            "setlocal\r\n"
            'set "MAS_HTTP_TOKEN_FILE=%USERPROFILE%\\.mas\\http_token.txt"\r\n'
            'for /f "usebackq delims=" %%T in ("%MAS_HTTP_TOKEN_FILE%") do set "MAS_HTTP_TOKEN=%%T"\r\n'
            "if not defined MAS_HTTP_TOKEN (\r\n"
            '  echo [ERROR] MAS_HTTP_TOKEN missing or empty.\r\n'
            "  exit /b 1\r\n"
            ")\r\n"
            f'"{mas_command()}" http --host {DEFAULT_HOST} --port {port}\r\n'
            "endlocal\r\n"
        )
    if sys.platform == "darwin":
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            "  <key>Label</key><string>dev.openloop.mas.http</string>\n"
            "  <key>ProgramArguments</key>\n  <array>\n"
            f"    <string>/bin/sh</string>\n"
            f"    <string>{wrapper_path()}</string>\n"
            "  </array>\n"
            "  <key>RunAtLoad</key><true/>\n"
            "  <key>KeepAlive</key><true/>\n"
            "</dict>\n</plist>\n"
        )
    return (
        "[Unit]\n"
        "Description=OpenLoop MAS HTTP/SSE transport\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/bin/sh {wrapper_path()}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def port_is_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_token(_args: argparse.Namespace) -> int:
    print(read_or_create_token())
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    read_or_create_token()
    path = launcher_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(launcher_body(args.port), encoding="utf-8")
    written = [path]
    if os.name != "nt":
        wrapper = wrapper_path()
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(_shell_wrapper(args.port), encoding="utf-8")
        try:
            os.chmod(wrapper, 0o700)
        except OSError:
            pass
        written.append(wrapper)
    print(f"launcher written: {path}")
    for extra in written[1:]:
        print(f"wrapper written : {extra}")
    print(f"token file     : {TOKEN_FILE}")
    print(f"bound to       : {DEFAULT_HOST}:{args.port}")
    print("next: expose it through a TLS proxy, e.g.")
    print(f"  tailscale serve --bg --https={args.port} http://{DEFAULT_HOST}:{args.port}")
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    removed = []
    for path in (launcher_path(), wrapper_path()):
        if path.exists() and path.is_file():
            path.unlink()
            removed.append(str(path))
    if removed:
        for item in removed:
            print(f"removed: {item}")
    else:
        print("nothing to remove")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = launcher_path()
    state = {
        "launcher": str(path),
        "launcher_exists": path.exists(),
        "token_file": str(TOKEN_FILE),
        "token_file_exists": TOKEN_FILE.exists(),
        "listening": port_is_open(DEFAULT_HOST, args.port),
        "host": DEFAULT_HOST,
        "port": args.port,
    }
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_token = sub.add_parser("token", help="print (or create) the persisted token")
    p_token.set_defaults(func=cmd_token)

    p_install = sub.add_parser("install", help="write a persistent launcher")
    p_install.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove the launcher")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_status = sub.add_parser("status", help="show the current deployment state")
    p_status.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

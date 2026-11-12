r"""Register OpenLoop MAS as a custom MCP server inside OpenMausBot.

The script is idempotent: it rewrites the ``openloop-mas`` entry, backs up
``config.json`` first, and finishes with a handshake test against the running
OpenMausBot server when one can be reached.
"""

import json
import os
import pathlib
import shutil
import sys
import urllib.error
import urllib.request
from typing import List

SERVER_NAME = "openloop-mas"


def _expand(path: str) -> pathlib.Path:
    return pathlib.Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def _find_mas_app() -> pathlib.Path:
    """Locate the directory that has to be on ``PYTHONPATH``.

    The installer puts the package into ``<install dir>/app/mas``, so the
    importable directory is ``<install dir>/app`` - not ``<install dir>``.
    The previous candidates pointed at the *package* directory, which made
    ``-m mas`` fail inside OpenMausBot.
    """
    candidates = [
        pathlib.Path.home() / ".mas" / "app",
        pathlib.Path(__file__).resolve().parent.parent,
    ]
    for candidate in candidates:
        if (candidate / "mas" / "mcp_server.py").exists():
            return candidate
    raise SystemExit(
        "Cannot find the OpenLoop MAS package. Run install.ps1 / install.sh first, "
        "or point MAS_APP_DIR at it."
    )


def _state_dir(app_dir: pathlib.Path) -> pathlib.Path:
    """The directory that holds the configuration and the ledger.

    ``app`` is the *code* directory; the state lives one level up, next to
    it. Pointing ``MAS_STATE_DIR`` at the code directory would silently
    create a second, empty configuration and a second ledger.
    """
    return app_dir.parent


def _find_python() -> str:
    override = os.environ.get("MAS_PYTHON")
    if override and pathlib.Path(override).exists():
        return override
    return sys.executable


def _config_path() -> pathlib.Path:
    override = os.environ.get("OPENMAUSBOT_CONFIG")
    if override:
        return _expand(override)
    return pathlib.Path.home() / ".openmausbot" / "config.json"


def _candidate_ports() -> List[int]:
    """Ports an OpenMausBot server may be listening on.

    8799 is the default; the Windows launcher in this project uses 28799
    because 8799/18799 are usually taken by other local services.
    """
    ports: List[int] = []
    for env_name in ("OPENMAUSBOT_PORT", "OMB_PORT"):
        value = os.environ.get(env_name)
        if value and value.isdigit():
            ports.append(int(value))
    for extra in (28799, 8799, 18799):
        if extra not in ports:
            ports.append(extra)
    return ports


def _omb_port() -> int:
    """The first candidate port that actually answers."""
    for port in _candidate_ports():
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port, timeout=3
            ):
                return port
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return _candidate_ports()[0]


def _register(config_path: pathlib.Path, app_dir: pathlib.Path) -> dict:
    if not config_path.exists():
        raise SystemExit(
            "OpenMausBot config not found at %s. Run `openmausbot setup` first." % config_path
        )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    servers = data.setdefault("mcpServers", {})
    servers[SERVER_NAME] = {
        "command": _find_python(),
        "args": ["-X", "utf8", "-m", "mas", "serve"],
        "env": {
            "PYTHONPATH": str(app_dir),
            "PYTHONUTF8": "1",
            "MAS_STATE_DIR": str(_state_dir(app_dir)),
            "MAS_CONFIG_PATH": str(_state_dir(app_dir) / "config.json"),
        },
        "enabled": True,
    }
    backup = config_path.with_name(config_path.name + ".bak")
    shutil.copy2(config_path, backup)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup": str(backup), "servers": list(servers.keys())}


def _handshake(port: int) -> None:
    url = "http://127.0.0.1:%d/api/mcp/servers/%s/test" % (port, SERVER_NAME)
    try:
        request = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("handshake: skipped (%s)" % exc)
        print("hint: start OpenMausBot, then re-run this script to verify.")
        return
    tools = [tool.get("name") for tool in payload.get("tools", [])]
    if payload.get("ok") and tools:
        print("handshake: ok, %d tools -> %s" % (len(tools), ", ".join(tools)))
    else:
        print("handshake: failed -> %s" % json.dumps(payload, ensure_ascii=False)[:400])


def main() -> int:
    app_dir = _expand(os.environ.get("MAS_APP_DIR", "")) if os.environ.get("MAS_APP_DIR") else _find_mas_app()
    config_path = _config_path()
    print("mas package : %s" % app_dir)
    print("omb config  : %s" % config_path)
    result = _register(config_path, app_dir)
    print("backup      : %s" % result["backup"])
    print("mcpServers  : %s" % ", ".join(result["servers"]))
    print("")
    print("Restart OpenMausBot so it mounts the server, then run this again to verify.")
    _handshake(_omb_port())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

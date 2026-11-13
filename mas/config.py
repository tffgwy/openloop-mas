"""Configuration handling: defaults, persistence and environment overrides."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import tempfile
from typing import Any, Dict

# The directory name that tempfile.mkdtemp() generates: "tmp" plus 8
# random letters, digits or underscores.
_TEMP_DIR_NAME_RE = re.compile(r"^tmp[A-Za-z0-9_]{8}$")

APP_NAME = "openloop-mas"
CONFIG_FILENAME = "config.json"
DEFAULT_STATE_DIR = "~/.mas"

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": 1,
    # provider: mock | openai_compat | anthropic
    "provider": "mock",
    "model": "mock-1",
    "base_url": "",
    "api_key": "",
    "api_key_env": "MAS_API_KEY",
    "timeout_sec": 120,
    "max_retries": 2,
    "state_dir": DEFAULT_STATE_DIR,
    # v0.7.15: the directory that file-level machine checks resolve their
    # paths against. It defaults to the current working directory, so a
    # check written without any configuration still refers to the project
    # the user is standing in. The path is deliberately *not* expanded to
    # an absolute path here - it is resolved at check time against the
    # run's own workspace.
    "workspace": "",
    "team": "default",
    "language": "zh",
    "budget": {
        "max_iterations": 8,
        "deadline_sec": 900,
        "max_tokens": 200000,
    },
    "stagnation_limit": 3,
    # v0.2 orchestration: how many workers race in parallel, how many
    # independent reviewers verify the artifact and how their verdicts are
    # aggregated. The defaults reproduce v0.1 behaviour exactly.
    "orchestration": {
        "parallel_workers": 1,
        "reviewers": 1,
        "review_mode": "all",
    },
    # v0.2 failover: additional provider backends tried in order when the
    # primary one fails. Each entry is a full provider spec.
    "fallbacks": [],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def state_dir(config: Dict[str, Any] | None = None) -> pathlib.Path:
    raw = (config or {}).get("state_dir") or DEFAULT_STATE_DIR
    return pathlib.Path(os.path.expanduser(os.path.expandvars(str(raw)))).resolve()


def config_path(config: Dict[str, Any] | None = None) -> pathlib.Path:
    return state_dir(config) / CONFIG_FILENAME


def user_config_path() -> pathlib.Path:
    """Where the *user's* configuration lives.

    The configuration file is intentionally NOT stored next to the run
    ledger. The ledger follows ``state_dir`` (which the user may point at a
    dedicated drive or a per-project directory), while the configuration
    describes how the tool itself behaves and must always be found at the
    same well-known place. Storing it inside ``state_dir`` meant that
    picking a custom state directory silently made every later run fall
    back to the defaults.

    ``MAS_CONFIG_PATH`` overrides the location explicitly (used by CI and
    by the test suite to stay isolated).
    """
    explicit = os.environ.get("MAS_CONFIG_PATH")
    if explicit:
        return pathlib.Path(os.path.expanduser(os.path.expandvars(explicit))).resolve()
    return pathlib.Path(os.path.expanduser(DEFAULT_STATE_DIR)).resolve() / CONFIG_FILENAME


def load_config(
    path: str | os.PathLike | None = None,
    *,
    apply_env: bool = True,
) -> Dict[str, Any]:
    """Load configuration merged on top of the defaults.
    Environment variables override file values so CI and one-off runs never
    need to touch the on-disk config:
      MAS_PROVIDER, MAS_MODEL, MAS_BASE_URL, MAS_API_KEY, MAS_STATE_DIR,
      MAS_TEAM, MAS_LANGUAGE

    Pass ``apply_env=False`` to get the *persisted* view only - what the
    configuration file actually says, without the process environment mixed
    in. Anything that writes the configuration back to disk must use the
    persisted view, otherwise a temporary ``MAS_STATE_DIR`` or an API key
    coming from the environment gets frozen into the user's config file.
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    env_state = os.environ.get("MAS_STATE_DIR")
    if env_state:
        config["state_dir"] = env_state
    target = pathlib.Path(path) if path else user_config_path()
    if not target.exists():
        # v0.1 kept the configuration next to the state directory. The
        # installers still do that when they are pointed at a custom
        # directory, so keep reading that location when the well-known one
        # does not exist. The legacy file only wins when it actually
        # exists - otherwise the defaults are used, and a caller that
        # points at a fresh directory never picks up somebody else's
        # settings by accident.
        legacy = config_path(config)
        if legacy.exists() and legacy != target:
            target = legacy
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        if isinstance(loaded, dict):
            config = _deep_merge(config, loaded)
    if not apply_env:
        return config
    overrides = {
        "provider": os.environ.get("MAS_PROVIDER"),
        "model": os.environ.get("MAS_MODEL"),
        "base_url": os.environ.get("MAS_BASE_URL"),
        "api_key": os.environ.get("MAS_API_KEY"),
        "team": os.environ.get("MAS_TEAM"),
        "language": os.environ.get("MAS_LANGUAGE"),
    }
    for key, value in overrides.items():
        if value:
            config[key] = value
    if os.environ.get("MAS_STATE_DIR"):
        config["state_dir"] = os.environ["MAS_STATE_DIR"]
    return config


def load_persisted_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load only what the configuration file says, ignoring the environment.

    This is the view that must be written back to disk. It keeps the
    environment authoritative for anything it overrides, so a temporary
    ``MAS_STATE_DIR`` or an environment-provided API key never becomes a
    permanent part of the user's configuration.
    """
    return load_config(path, apply_env=False)


def persistable_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Strip environment-owned values so the config can be written to disk.

    Two things must never be frozen into the configuration file:

    * the API key - the wizard already promises "配置里不会存明文密钥";
    * a ``state_dir`` that only exists in the environment.

    The API key is replaced by the name of the environment variable that
    holds it, which is exactly what ``resolve_api_key`` reads back.
    """
    payload = copy.deepcopy(config)
    env_name = str(payload.get("api_key_env") or "MAS_API_KEY")
    env_value = os.environ.get(env_name, "")
    if payload.get("api_key") and (not env_value or payload["api_key"] == env_value):
        payload["api_key"] = ""
    payload["api_key_env"] = env_name
    state = str(payload.get("state_dir") or "")
    if os.environ.get("MAS_STATE_DIR") and state == os.environ["MAS_STATE_DIR"]:
        payload["state_dir"] = DEFAULT_STATE_DIR
    elif state and _is_ephemeral_state_dir(state):
        # A state directory inside the system temp folder is a run-time
        # injection, not a deliberate choice: the temp folder is cleaned
        # up by the OS, so a ledger kept there would vanish. Never persist
        # it - fall back to the well-known location.
        payload["state_dir"] = DEFAULT_STATE_DIR
    return payload


def _is_ephemeral_state_dir(state: str) -> bool:
    """True when ``state`` is an auto-generated temporary directory.

    Only the ``tmpXXXXXXXX`` shape that :func:`tempfile.mkdtemp` produces
    counts. A directory the user named themselves inside the temp folder
    is a deliberate choice and is kept.
    """
    try:
        resolved = pathlib.Path(os.path.expanduser(os.path.expandvars(state))).resolve()
        temp_root = pathlib.Path(tempfile.gettempdir()).resolve()
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False
    try:
        relative = resolved.relative_to(temp_root)
    except ValueError:
        return False
    parts = relative.parts
    if len(parts) != 1:
        return False
    name = parts[0]
    # tempfile.mkdtemp() uses 8 random characters from letters, digits and
    # underscore, so the whole name is exactly "tmp" plus 8 characters.
    return _TEMP_DIR_NAME_RE.match(name) is not None


def save_config(config: Dict[str, Any], path: str | os.PathLike | None = None) -> pathlib.Path:
    """Write the configuration to disk.

    The payload is passed through :func:`persistable_config` first, so an
    environment-provided API key or a temporary ``MAS_STATE_DIR`` is never
    persisted.
    """
    target = pathlib.Path(path) if path else user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(persistable_config(config), ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(payload + "\n", encoding="utf-8")
    return target


def resolve_api_key(config: Dict[str, Any]) -> str:
    env_name = config.get("api_key_env") or "MAS_API_KEY"
    env_value = os.environ.get(env_name, "")
    if env_value:
        return env_value
    return str(config.get("api_key") or "")

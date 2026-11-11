"""Configuration handling: defaults, persistence and environment overrides."""

from __future__ import annotations

import copy
import json
import os
import pathlib
from typing import Any, Dict

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
    "team": "default",
    "language": "zh",
    "budget": {
        "max_iterations": 8,
        "deadline_sec": 900,
        "max_tokens": 200000,
    },
    "stagnation_limit": 3,
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


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load configuration merged on top of the defaults.
    Environment variables override file values so CI and one-off runs never
    need to touch the on-disk config:
      MAS_PROVIDER, MAS_MODEL, MAS_BASE_URL, MAS_API_KEY, MAS_STATE_DIR,
      MAS_TEAM, MAS_LANGUAGE
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    env_state = os.environ.get("MAS_STATE_DIR")
    if env_state:
        config["state_dir"] = env_state
    target = pathlib.Path(path) if path else config_path(config)
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        if isinstance(loaded, dict):
            config = _deep_merge(config, loaded)
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


def save_config(config: Dict[str, Any], path: str | os.PathLike | None = None) -> pathlib.Path:
    target = pathlib.Path(path) if path else config_path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(payload + "\n", encoding="utf-8")
    return target


def resolve_api_key(config: Dict[str, Any]) -> str:
    env_name = config.get("api_key_env") or "MAS_API_KEY"
    env_value = os.environ.get(env_name, "")
    if env_value:
        return env_value
    return str(config.get("api_key") or "")

"""Team templates: the role prompts and default acceptance criteria."""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

TEAM_DIR = pathlib.Path(__file__).parent / "teams"

FALLBACK_TEAM: Dict[str, Any] = {
    "name": "default",
    "description": "通用闭环团队：规划 / 执行 / 验证 / 复盘 / 主控",
    "language": "zh",
    "roles": {},
    "default_criteria": [],
}


def list_teams() -> List[Dict[str, Any]]:
    teams: List[Dict[str, Any]] = []
    if TEAM_DIR.exists():
        for path in sorted(TEAM_DIR.glob("*.json")):
            data = _read(path)
            if data:
                teams.append(
                    {
                        "name": data.get("name", path.stem),
                        "description": data.get("description", ""),
                        "file": str(path),
                    }
                )
    return teams


def load_team(name: str | None) -> Dict[str, Any]:
    if not name:
        return dict(FALLBACK_TEAM)
    path = TEAM_DIR / f"{name}.json"
    data = _read(path) if path.exists() else None
    return data or dict(FALLBACK_TEAM)


def _read(path: pathlib.Path) -> Dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

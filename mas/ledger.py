"""Append-only JSONL ledger.

Every meaningful step of a loop writes exactly one entry. The ledger is the
audit trail of the closed loop: it is what makes a run replayable, reviewable
and - after the fact - provable.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

from mas.util import ensure_dir, now_iso, short_sha


class Ledger:
    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        ensure_dir(self.path.parent)

    def append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entry)
        payload.setdefault("ts", now_iso())
        existing = self.entries()
        payload["seq"] = len(existing) + 1
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def tail(self, count: int = 20) -> List[Dict[str, Any]]:
        rows = self.entries()
        return rows[-count:] if count > 0 else rows

    def summary(self) -> Dict[str, Any]:
        rows = self.entries()
        roles: Dict[str, int] = {}
        for row in rows:
            role = str(row.get("role", "unknown"))
            roles[role] = roles.get(role, 0) + 1
        return {
            "path": str(self.path),
            "entries": len(rows),
            "roles": roles,
            "sha256_12": short_sha(self.path.read_text(encoding="utf-8")) if self.path.exists() else "",
        }

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
        # Cache of the entry count, keyed by the file identity we last saw.
        # Appending used to re-read the whole ledger for every single entry,
        # which turns a run that writes N entries into O(N^2) of I/O - a
        # long ledger makes every run progressively slower. The cache is
        # invalidated whenever the file changed underneath us (another
        # process appended, or the file was rotated), so the sequence
        # numbers stay exactly as before.
        self._count: int | None = None
        self._stat_key: tuple[int, int] | None = None

    def _stat_key_now(self) -> tuple[int, int]:
        try:
            info = self.path.stat()
        except OSError:
            return (-1, -1)
        return (info.st_size, info.st_mtime_ns)

    def _entry_count(self) -> int:
        """Number of ledger entries, cached until the file changes."""
        key = self._stat_key_now()
        if self._count is not None and key == self._stat_key:
            return self._count
        count = len(self.entries())
        self._count = count
        self._stat_key = key
        return count

    def append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entry)
        payload.setdefault("ts", now_iso())
        payload["seq"] = self._entry_count() + 1
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        # Our own write changed the file: keep the cached count but record
        # the new identity so a later append does not re-scan.
        self._count = payload["seq"]
        self._stat_key = self._stat_key_now()
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

"""Human escalation tickets.

When a loop cannot close itself - it runs out of budget, stalls, or needs a
decision that is not the agent's to make - it opens a ticket instead of
spinning. The ticket carries the full context so a human can resume the
loop with one command.
"""

from __future__ import annotations

import json
import pathlib
import secrets
import time
from typing import Any, Dict, List, Optional

from mas.util import ensure_dir, now_iso


class TicketStore:
    def __init__(self, directory: str | pathlib.Path) -> None:
        self.directory = pathlib.Path(directory)
        ensure_dir(self.directory)

    def _file(self, ticket_id: str) -> pathlib.Path:
        return self.directory / f"{ticket_id}.json"

    def create(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        ticket_id = f"T-{stamp}-{secrets.token_hex(2)}"
        ticket = {
            "id": ticket_id,
            "kind": kind,
            "state": "waiting_human",
            "created_at": now_iso(),
            "resolved_at": None,
            "decision": None,
            "note": "",
            "payload": payload,
        }
        self._file(ticket_id).write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ticket

    def list(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        tickets: List[Dict[str, Any]] = []
        for path in sorted(self.directory.glob("T-*.json")):
            ticket = self._read(path)
            if ticket and (state is None or ticket.get("state") == state):
                tickets.append(ticket)
        return tickets

    def get(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        path = self._file(ticket_id)
        return self._read(path) if path.exists() else None

    def resolve(self, ticket_id: str, decision: str, note: str = "") -> Dict[str, Any]:
        ticket = self.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket: {ticket_id}")
        ticket["state"] = "resolved"
        ticket["decision"] = decision
        ticket["note"] = note
        ticket["resolved_at"] = now_iso()
        self._file(ticket_id).write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ticket

    @staticmethod
    def _read(path: pathlib.Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

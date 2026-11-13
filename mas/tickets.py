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

    def append_attempt(
        self,
        ticket_id: str,
        payload: Dict[str, Any],
        reason: str = "",
    ) -> Dict[str, Any]:
        """Record a failed attempt on an existing ticket.

        A resumed loop that escalates again must not open a second ticket:
        the human queue lists open problems, and one problem listed twice
        makes the queue wrong. The new evidence goes onto the ticket that
        is already waiting.
        """
        ticket = self.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket: {ticket_id}")
        old = ticket.get("payload") or {}
        merged = dict(old)
        merged.update(payload or {})
        merged["attempts_total"] = int(old.get("attempts_total") or 0) + 1
        merged["reason"] = reason or merged.get("reason", "")
        # The failure itself is recorded once, by reopen(); here we only
        # carry the fresh evidence onto the ticket.
        ticket["payload"] = merged
        ticket["state"] = "waiting_human"
        ticket["decision"] = None
        ticket["resolved_at"] = None
        self._file(ticket_id).write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ticket

    def reopen(
        self,
        ticket_id: str,
        note: str = "",
        reason: str = "",
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Put a ticket back into the human queue after a failed resume.

        A resume that does not close the loop leaves the ticket
        ``waiting_human`` on purpose: the escalation is still open, so
        it has to stay visible in ``mas tickets``. The failed attempt is
        recorded on the ticket so the next person sees what happened
        instead of an unchanged, context-free ticket.
        """
        ticket = self.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket: {ticket_id}")
        ticket["state"] = "waiting_human"
        ticket["decision"] = None
        ticket["resolved_at"] = None
        if note:
            ticket["note"] = note
        payload = ticket.get("payload") or {}
        attempts = list(payload.get("resume_attempts") or [])
        attempts.append(
            {
                "at": now_iso(),
                "reason": reason,
                "status": (result or {}).get("status"),
                "iterations": (result or {}).get("iterations"),
                "issues": list((result or {}).get("issues") or []),
                "error": (result or {}).get("error", ""),
                "run_id": (result or {}).get("run_id"),
            }
        )
        payload["resume_attempts"] = attempts
        ticket["payload"] = payload
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

"""MCP (Model Context Protocol) server over stdio.

The transport is newline delimited JSON-RPC 2.0, which is what the MCP stdio
transport specifies. The implementation is intentionally dependency free so
that a beginner only needs Python - no ``pip install`` step.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from mas import __version__
from mas.config import load_config, save_config
from mas.doctor import run_doctor, summarize
from mas.engine import LoopEngine
from mas.teams import list_teams, load_team

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "openloop-mas"


def _tool(name: str, description: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": schema}


_EMPTY_OBJECT = {"type": "object", "properties": {}}

TOOLS: List[Dict[str, Any]] = [
    _tool(
        "mas_run",
        "Run a multi-agent loop until it closes. Returns the closure status, "
        "per-criterion verdicts and the ledger path.",
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "任务目标"},
                "criteria": {
                    "type": "array",
                    "description": "验收标准列表；可用 check 字段写机检规则",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "desc": {"type": "string"},
                            "check": {"type": "object"},
                            "required": {"type": "boolean"},
                        },
                    },
                },
                "title": {"type": "string"},
                "team": {"type": "string"},
            },
            "required": ["goal"],
        },
    ),
    _tool(
        "mas_status",
        "Show the local OpenLoop MAS state: config, run count, open tickets.",
        _EMPTY_OBJECT,
    ),
    _tool(
        "mas_ledger",
        "Read the append-only run ledger (JSONL). Optionally filter by run_id.",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "tail": {"type": "integer", "default": 50},
            },
        },
    ),
    _tool(
        "mas_tickets",
        "List human escalation tickets.",
        {
            "type": "object",
            "properties": {"state": {"type": "string", "description": "waiting_human | resolved"}},
        },
    ),
    _tool(
        "mas_ticket_resolve",
        "Approve or reject an escalation ticket.",
        {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "decision": {"type": "string", "description": "approved | rejected"},
                "note": {"type": "string"},
            },
            "required": ["ticket_id", "decision"],
        },
    ),
    _tool(
        "mas_resume",
        "Resume an escalated loop with a human note.",
        {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    ),
    _tool(
        "mas_teams",
        "List the bundled team templates.",
        _EMPTY_OBJECT,
    ),
    _tool(
        "mas_doctor",
        "Run the installation self-check.",
        _EMPTY_OBJECT,
    ),
    _tool(
        "mas_config",
        "Read or update the local configuration.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "get | set"},
                "values": {"type": "object"},
            },
        },
    ),
]


def _engine(config: Dict[str, Any], team: Optional[str] = None) -> LoopEngine:
    team_spec = load_team(team or config.get("team"))
    return LoopEngine(config, team=team_spec)


def handle_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    config = load_config()
    arguments = arguments or {}

    if name == "mas_run":
        engine = _engine(config, arguments.get("team"))
        result = engine.run(
            goal=str(arguments.get("goal", "")),
            criteria=arguments.get("criteria") or [],
            title=str(arguments.get("title", "")),
        )
        return result.to_dict()

    if name == "mas_status":
        engine = _engine(config)
        tickets = engine.tickets.list()
        return {
            "config": {
                "provider": config.get("provider"),
                "model": config.get("model"),
                "team": config.get("team"),
                "state_dir": config.get("state_dir"),
            },
            "ledger": engine.ledger.summary(),
            "open_tickets": [t.get("id") for t in tickets if t.get("state") == "waiting_human"],
        }

    if name == "mas_ledger":
        engine = _engine(config)
        rows = engine.ledger.tail(int(arguments.get("tail", 50)))
        run_id = arguments.get("run_id")
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        return {"path": str(engine.ledger.path), "entries": rows}

    if name == "mas_tickets":
        engine = _engine(config)
        state = arguments.get("state")
        return {"tickets": engine.tickets.list(state)}

    if name == "mas_ticket_resolve":
        engine = _engine(config)
        ticket = engine.tickets.resolve(
            str(arguments.get("ticket_id")),
            str(arguments.get("decision", "approved")),
            str(arguments.get("note", "")),
        )
        return {"ticket": ticket}

    if name == "mas_resume":
        engine = _engine(config)
        result = engine.resume(str(arguments.get("ticket_id")), str(arguments.get("note", "")))
        return result.to_dict()

    if name == "mas_teams":
        return {"teams": list_teams()}

    if name == "mas_doctor":
        return summarize(run_doctor(config))

    if name == "mas_config":
        action = str(arguments.get("action", "get"))
        if action == "set":
            values = arguments.get("values") or {}
            merged = dict(config)
            merged.update(values)
            save_config(merged)
            return {"config": merged}
        return {"config": config}

    raise KeyError(f"unknown tool: {name}")


def _response(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = str(message.get("method", ""))
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": "OpenLoop MAS: run goal -> work -> verify -> reflect loops that close.",
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        try:
            payload = handle_tool(name, arguments)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            is_error = False
        except Exception as exc:  # noqa: BLE001 - reported to the client
            text = json.dumps({"error": str(exc)}, ensure_ascii=False)
            is_error = True
        return _response(
            request_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )
    if request_id is None:
        return None
    return _error(request_id, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        response = handle_message(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())

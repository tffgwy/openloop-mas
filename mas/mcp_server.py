"""MCP (Model Context Protocol) server over stdio.

The transport is newline delimited JSON-RPC 2.0, which is what the MCP stdio
transport specifies. The implementation is intentionally dependency free so
that a beginner only needs Python - no ``pip install`` step.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

from mas import __version__
from mas.config import load_config, load_persisted_config, save_config
from mas.doctor import run_doctor, summarize
from mas.drafts_reading import is_waiting_for_human
from mas.engine import LoopEngine
from mas.report import (
    artifact_rows,
    build_report,
    closure_verdict_for,
    format_artifacts,
    format_closure,
    ledger_markdown,
    render_markdown,
    replay_trace,
)
from mas.teams import list_teams, load_team
from mas.undos import draft_histories
from mas.util import force_utf8_output

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
                    "description": (
                        "验收标准列表；可用 check 字段写机检规则。"
                        "文本机检：contains/not_contains/min_chars/max_chars/"
                        "regex/lines_min/section/json_keys；"
                        "文件机检（读真实磁盘）：file_exists/file_missing/"
                        "file_contains/file_not_contains/file_regex/"
                        "file_lines_min/file_min_bytes/file_max_bytes/"
                        "file_sha256/file_mtime_newer"
                    ),
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
                "workspace": {
                    "type": "string",
                    "description": "文件级机检（file_exists/file_contains 等）解析路径的工作区目录",
                },
                "parallel": {
                    "type": "integer",
                    "description": "并行候选的 worker 数量（>1 时并发产出多个视角）",
                },
                "perspective": {
                    "type": "string",
                    "description": "候选视角：direct/thorough/concise/structured/example_first/risk_first",
                },
                "reviewers": {
                    "type": "integer",
                    "description": "独立评审者数量（>1 时启用对抗评审聚合）",
                },
                "review_mode": {
                    "type": "string",
                    "description": "多评审聚合规则：all / majority / any",
                },
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
        "mas_undos",
        "List the approvals that were taken back: which of the things a "
        "human approved had to be undone, and how many lines each undo "
        "took back.",
        {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "工作区文件夹（默认本机 artifacts 目录）",
                },
                "draft_id": {
                    "type": "string",
                    "description": "只看某个草稿（可选）",
                },
            },
        },
    ),
    _tool(
        "mas_tickets",
        "List human escalation tickets. Each ticket comes with a brief: "
        "what it is about, what it is stuck on, why the loop stopped and "
        "the command that continues it.",
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
                # A takeover needs a budget of its own: the run that
                # opened the ticket stopped at a ceiling, and
                # continuing with the same ceiling stops at the same
                # place again. These three give *this* run a different
                # ceiling without changing the machine-wide default.
                "max_iterations": {"type": "integer"},
                "deadline_sec": {"type": "integer"},
                "max_tokens": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
    ),
    _tool(
        "mas_handback",
        "Hand a human-written deliverable back into the loop. The same "
        "acceptance criteria from the ticket decide it; a deliverable "
        "that fails stays in the human queue with the failing criteria "
        "recorded on the ticket.",
        {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                # The deliverable itself, or a file that holds it.
                "artifact": {
                    "type": "string",
                    "description": "人工交付物的正文（与 file 二选一）",
                },
                "file": {
                    "type": "string",
                    "description": "人工交付物所在的文件（与 artifact 二选一）",
                },
                "note": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    ),
    _tool(
        "mas_report",
        "Render a Markdown closure report from the run ledger. The report "
        "re-derives every sealed conclusion against the files on the disk, "
        "so a closure that was edited afterwards is reported as drifted "
        "instead of resolved - the JSON payload carries the same "
        "reconciliation under \"seal\".",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "as_markdown": {"type": "boolean", "default": True},
            },
        },
    ),
    _tool(
        "mas_replay",
        "Replay a run from the ledger as an ordered trace.",
        {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
        },
    ),
    _tool(
        "mas_export",
        "Export the raw ledger as a Markdown table.",
        {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
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
        "mas_upgrade",
        "Replace an outdated installed runtime with the running one. "
        "The one-click installer copies the runtime into the install "
        "directory; refreshing the skills and the MCP registration does "
        "not change that copy, so an old runtime can keep serving while "
        "every check reports 'registered'. This replaces it and reports "
        "what was replaced.",
        {
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "description": "版本相同也强制替换（默认 false）",
                },
            },
        },
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
    _tool(
        "mas_drafts",
        "List the pending drafts of a workspace, or show one of them. "
        "A draft is a proposed change to a file that already exists; "
        "the file itself is untouched until the change is approved.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list | show"},
                "draft_id": {"type": "string", "description": "草稿编号，例如 D-1a2b3c4d"},
                "state": {"type": "string", "description": "pending | approved | rejected"},
                "workspace": {"type": "string", "description": "工作区路径；默认是本机所有运行的工作区"},
            },
        },
    ),
    _tool(
        "mas_draft_stage",
        "Propose a change to a file that already exists, without "
        "changing the file. The change becomes a draft that a human "
        "reviews and approves. Creating a *new* file is not a draft: "
        "use the normal write path for that.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要修改的已有文件路径（相对工作区）"},
                "content": {"type": "string", "description": "改写后的完整正文"},
                "reason": {"type": "string", "description": "为什么改（人会看到这句）"},
                "workspace": {"type": "string", "description": "工作区路径"},
            },
            "required": ["path", "content"],
        },
    ),
    _tool(
        "mas_draft_resolve",
        "Approve or reject a draft. Approving writes the change back to "
        "the original file; rejecting leaves the file byte-identical. "
        "This needs the human token, so an agent cannot approve its own "
        "proposal - or a one-shot ticket signed for this one draft.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "approve | reject"},
                "draft_id": {"type": "string", "description": "草稿编号"},
                "token": {"type": "string", "description": "人类令牌：mas drafts token"},
                "ticket": {
                    "type": "string",
                    "description": "一次性票据：mas drafts ticket <草稿编号> 签发，只对那一个草稿生效",
                },
                "note": {"type": "string", "description": "给人看的备注"},
                "workspace": {"type": "string", "description": "工作区路径"},
                "force": {
                    "type": "boolean",
                    "description": "明知文件在草稿等待期间被改过，仍要写回",
                },
            },
            "required": ["action", "draft_id"],
        },
    ),
    _tool(
        "mas_draft_compare",
        "Show the three versions of one file side by side: the baseline "
        "the draft was made against, the file as it is on disk now, and "
        "what the draft proposes. It also names the lines the draft "
        "would take away from the human - the ones that would be lost "
        "by a forced approval. It changes nothing.",
        {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "description": "草稿编号"},
                "workspace": {"type": "string", "description": "工作区路径"},
            },
            "required": ["draft_id"],
        },
    ),
    _tool(
        "mas_draft_undo",
        "Take an approved change back: the file goes back to the bytes "
        "it held before the draft was made. The draft itself stays, so "
        "the same change can be approved again. It needs the human "
        "token or a one-shot ticket, and it refuses to run when the "
        "file was changed after the approval unless force is set - "
        "because undoing then would throw the newer work away.",
        {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "description": "草稿编号"},
                "token": {"type": "string", "description": "人类令牌：mas drafts token"},
                "ticket": {
                    "type": "string",
                    "description": "一次性票据：mas drafts ticket <草稿编号> 签发",
                },
                "note": {"type": "string", "description": "给人看的备注"},
                "workspace": {"type": "string", "description": "工作区路径"},
                "force": {
                    "type": "boolean",
                    "description": "明知文件在批准之后被改过，仍要撤销",
                },
            },
            "required": ["draft_id"],
        },
    ),
    _tool(
        "mas_audit",
        "Check the chain of human decisions: has an approval or an undo "
        "been changed after it was written? The chain lives next to the "
        "workspace and every entry links to the one before it by hash; "
        "the main ledger holds a second copy that has to agree. The "
        "answer names what disagrees and where.",
        {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "工作区路径"},
            },
        },
    ),
    _tool(
        "mas_files",
        "Index a folder: every file it holds, named by its own header, "
        "with the facts the file carries (pages of a PDF, family of a "
        "font, duration of a recording). With no argument it indexes "
        "every run workspace on this machine.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要索引的文件夹；默认索引所有运行工作区"},
                "workspace": {
                    "type": "string",
                    "description": "要索引的工作区文件夹（与 path 二选一）",
                },
            },
        },
    ),
    _tool(
        "mas_artifacts",
        "List the deliverables this machine has produced. The ledger is a "
        "log of calls and the report is a review of a run - neither "
        "answers the question that matters a week later: what did I "
        "actually get? This lists every deliverable with the task it "
        "came from, where it sits, how big it is, and whether the file "
        "is still there.",
        {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "只看某一次运行（默认列出全部）",
                },
            },
        },
    ),
    _tool(
        "mas_closure",
        "Judge whether the work is actually closed. The report shows every "
        "number - runs, threads, tickets, drafts, seals, audit chains - but "
        "a reader holding the report still has to work out the verdict "
        "themselves. This computes it: closed means nothing is left waiting "
        "(no unclosed run, no open problem, no ticket waiting for a person, "
        "no pending draft, no drifted seal, no broken decision chain), and "
        "when it is not closed it names exactly which part is open. It "
        "reads the existing records; it never runs a new loop.",
        {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "只判定某一次运行（默认判定整个工作区）",
                },
            },
        },
    ),
    _tool(
        "mas_seal",
        "Check whether the closed loops still hold. Every closure is "
        "sealed to the bytes it was decided on - the acceptance criteria, "
        "the deliverable hash, the hash of every file the run wrote. This "
        "re-derives the verdict from that sealed evidence and reports the "
        "drift: files edited after the fact, or a verdict that can no "
        "longer be recomputed.",
        {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "只检查某一次运行（默认检查全部）",
                },
            },
        },
    ),
    _tool(
        "mas_preview",
        "Show what one file holds, read from the file itself: the words "
        "of a PDF or a Word document, the facts of an image or a model. "
        "A file with no text says so instead of showing a blank screen.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要预览的文件路径（相对路径按 workspace 解析）"},
                "workspace": {
                    "type": "string",
                    "description": "相对路径所属的工作区文件夹",
                },
                "max_chars": {"type": "integer", "description": "最多返回多少字符（默认 4000）"},
            },
            "required": ["path"],
        },
    ),
]


def _engine(config: Dict[str, Any], team: Optional[str] = None) -> LoopEngine:
    team_spec = load_team(team or config.get("team"))
    return LoopEngine(config, team=team_spec)


def _state_dir(config: Dict[str, Any]) -> pathlib.Path:
    """The state directory, expanded."""
    raw = str(config.get("state_dir") or "")
    return pathlib.Path(os.path.expanduser(os.path.expandvars(raw)))




def drafts_of_machine(
    config: Dict[str, Any], ledger_rows: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Every draft of every workspace this machine owns.

    The walk lives in ``mas.drafts`` - one implementation, shared with
    the CLI, so the two surfaces cannot drift apart on which drafts are
    waiting. A report that says "no drafts" while a change waits on
    the disk is the exact failure this project keeps fixing.
    """
    from mas.drafts import all_drafts_of_machine

    return all_drafts_of_machine(
        _state_dir(config),
        config.get("workspace"),
        ledger_rows,
    )


def _workspace_base(config: Dict[str, Any], workspace: Any) -> pathlib.Path:
    """The folder the draft tools look into.

    A named workspace wins. Otherwise the artifacts root of the state
    directory is used, which is where every run's workspace lives.
    """
    named = str(workspace or "").strip()
    if named:
        return pathlib.Path(named).expanduser()
    return _state_dir(config) / "artifacts"


def _all_workspace_roots(config: Dict[str, Any]) -> List[pathlib.Path]:
    """Every run workspace of this machine."""
    root = _state_dir(config) / "artifacts"
    if not root.exists():
        return []
    return [child for child in sorted(root.iterdir()) if child.is_dir()]


def _workspace_roots(config: Dict[str, Any], workspace: Any) -> List[pathlib.Path]:
    """The workspaces a caller asked about.

    A named workspace wins - the caller said which folder it means.
    Otherwise every run workspace of this machine is scanned, because a
    draft stuck on a conflict is a decision the human still owes and it
    must not stay invisible just because nobody named the folder.
    """
    named = str(workspace or "").strip()
    if named:
        return [pathlib.Path(named).expanduser()]
    return _all_workspace_roots(config)


def _add_draft_history(
    draft: Dict[str, Any],
    histories: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Put what the audit record says about a draft onto the draft.

    The draft file holds only the newest state of a draft, so it
    cannot say "this was approved and then taken back". After a second
    approval the state is ``approved`` again, and the wrongness of the
    first approval is gone from the file - it lives only in the audit
    chain.

    The command line shows that fact next to the draft. So does this
    surface, and both read it from the same place: one reading, one
    answer, whatever surface the reader came in through.

    A draft with no history gets ``0`` and ``false`` - the record
    says nothing happened, which is not the same as "it was never
    taken back", but it is the truth about the record.
    """
    history = histories.get(str(draft.get("id") or "")) or {}
    draft["approvals"] = int(history.get("approvals") or 0)
    draft["undos"] = int(history.get("undos") or 0)
    draft["ever_undone"] = bool(history.get("ever_undone"))
    draft["reverted_line_count"] = history.get("reverted_line_count")
    # Whether the change still waits for a person. A draft whose
    # approval was taken back waits again: the undo put the baseline
    # back on the disk and the change can be approved a second time.
    #
    # This is the field an agent needs. "The state is ``undone``"
    # does not say whether there is work left for the human - and an
    # agent that reads only ``state`` reads a draft that nobody has
    # decided yet as one that is already handled.
    draft["waiting_for_human"] = is_waiting_for_human(draft.get("state"))
    return draft


def handle_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    config = load_config()
    arguments = arguments or {}

    if name == "mas_run":
        workspace = str(arguments.get("workspace") or "").strip()
        if workspace:
            # File-level checks resolve their paths against this
            # workspace. It is applied here - not inside the engine - so
            # the MCP caller and the CLI behave the same way.
            config["workspace"] = workspace
        engine = _engine(config, arguments.get("team"))
        parallel = arguments.get("parallel")
        reviewers = arguments.get("reviewers")
        result = engine.run(
            goal=str(arguments.get("goal", "")),
            criteria=arguments.get("criteria") or [],
            title=str(arguments.get("title", "")),
            parallel=int(parallel) if parallel else None,
            reviewers=int(reviewers) if reviewers else None,
            review_mode=str(arguments.get("review_mode")) if arguments.get("review_mode") else None,
            perspective=str(arguments.get("perspective") or ""),
        )
        return result.to_dict()

    if name == "mas_status":
        engine = _engine(config)
        tickets = engine.tickets.list()
        payload = {
            "config": {
                "provider": config.get("provider"),
                "model": config.get("model"),
                "team": config.get("team"),
                "state_dir": config.get("state_dir"),
            },
            "ledger": engine.ledger.summary(),
            # The ids stay - they are the handle every other tool
            # takes. But an id on its own is not a handover: it says
            # *which* ticket, not *what is stuck* or *how to continue*.
            # The briefs below carry that.
            "open_tickets": [t.get("id") for t in tickets if t.get("state") == "waiting_human"],
        }
        # The waiting tickets, as something a reader can act on.
        #
        # The gap: the status tool listed ticket ids and stopped there.
        # An agent asking "what needs me?" got a list of handles and
        # had to call a second tool to learn what any of them meant -
        # and an agent with only the status tool could never learn.
        #
        # The same facts the command line prints ("mas tickets") come
        # from one function, ``mas.ticket_brief.ticket_brief``, so the
        # two surfaces cannot describe the same ticket differently.
        try:
            from mas.ticket_brief import ticket_briefs

            waiting = [t for t in tickets if t.get("state") == "waiting_human"]
            payload["waiting_tickets"] = ticket_briefs(waiting)
            payload["waiting_tickets_count"] = len(waiting)
            # One line per ticket, for a reader that wants prose
            # instead of fields.
            payload["waiting_tickets_text"] = "\n".join(
                "  %s [%s] %s"
                % (
                    b.get("id") or "",
                    b.get("kind") or "",
                    b.get("blocked_on") or b.get("reason_reading") or b.get("title") or "",
                )
                for b in payload["waiting_tickets"]
            )
        except Exception:  # noqa: BLE001 - status must never crash
            payload["waiting_tickets"] = []
            payload["waiting_tickets_count"] = 0
            payload["waiting_tickets_text"] = ""
        # The closure, in the same grain every other surface uses.
        #
        # The gap: the MCP status tool reported the ticket queue and
        # the ledger and said nothing about closure at all. An agent
        # asking "is this done?" - the first question anyone asks a
        # status tool - got no answer from the very tool that looks
        # like the place to ask it, and had to go and call a different
        # tool to find out.
        #
        # And when it did, the two surfaces disagreed: the command
        # line counted attempts while the verdict counted problems, so
        # a machine that had finished its work could still report an
        # open attempt. The counts below come from the same function
        # the command line reads, so they cannot drift.
        try:
            rows = engine.ledger.entries()
            verdict = closure_verdict_for(rows, tickets)
            counts = verdict.get("counts") or {}
            payload["closure"] = {
                "ok": bool(verdict.get("ok")),
                "empty": bool(verdict.get("empty")),
                "headline": str(verdict.get("headline") or ""),
                "reasons": list(verdict.get("reasons") or []),
                # The counts, by grain. The names say which is which.
                "attempts": int(counts.get("runs") or 0),
                "attempts_closed": int(counts.get("closed") or 0),
                "attempts_not_closed": int(counts.get("not_closed") or 0),
                # The failed attempts that are still waiting.
                "attempts_open": int(counts.get("not_closed_open") or 0),
                # The failed attempts a later attempt replaced.
                "attempts_superseded": int(counts.get("not_closed_superseded") or 0),
                "problems": int(counts.get("problems") or 0),
                "problems_open": int(counts.get("problems_open") or 0),
                "problems_closed": int(counts.get("problems_closed") or 0),
                # The two rates, each naming its own grain.
                "attempt_closure_rate": float(verdict.get("closure_rate") or 0.0),
                "problem_closure_rate": float(verdict.get("problem_closure_rate") or 0.0),
                "open_threads": list(verdict.get("open_threads") or []),
            }
            payload["closure_text"] = format_closure(verdict)
        except Exception:  # noqa: BLE001 - status must never crash
            payload["closure"] = {
                "ok": False,
                "empty": True,
                "headline": "无法判定闭合。",
                "reasons": [],
                "attempts": 0,
                "attempts_closed": 0,
                "attempts_not_closed": 0,
                "attempts_open": 0,
                "attempts_superseded": 0,
                "problems": 0,
                "problems_open": 0,
                "problems_closed": 0,
                "attempt_closure_rate": 0.0,
                "problem_closure_rate": 0.0,
                "open_threads": [],
            }
        # A draft that sits on a conflict is a decision the human still
        # owes. It is invisible from the tickets - those are for the
        # *loop* to ask a question - so an agent reading the status
        # would never learn that the work is stuck on it.
        try:
            from mas.drafts import draft_conflict, drafts_in

            roots = _workspace_roots(config, arguments.get("workspace"))
            stuck: List[Dict[str, Any]] = []
            for root in roots:
                for draft in drafts_in(root, "pending"):
                    clash = draft_conflict(root, draft)
                    if clash.get("conflict"):
                        stuck.append(
                            {
                                "draft_id": draft.get("id"),
                                "path": clash.get("path") or draft.get("path"),
                                "expected_sha256": clash.get("expected_sha256"),
                                "current_sha256": clash.get("current_sha256"),
                            }
                        )
            payload["conflicting_drafts"] = stuck
            payload["conflicting_draft_count"] = len(stuck)

            # The drafts that were approved and then taken back. They
            # are not "open" - the human already dealt with them - but
            # they are also not nothing: an undone change says the
            # approval was wrong, and the draft is still there, ready
            # to be approved again once the reason is understood.
            undone_rows: List[Dict[str, Any]] = []
            for root in roots:
                for draft in drafts_in(root, "undone"):
                    undone_rows.append(
                        {
                            "draft_id": draft.get("id"),
                            "path": draft.get("path"),
                            "undone_at": draft.get("undone_at"),
                            "note": draft.get("undo_note") or "",
                        }
                    )
            payload["undone_drafts"] = undone_rows
            payload["undone_draft_count"] = len(undone_rows)
            # The undone drafts are also **waiting**. The undo put
            # the baseline back on the disk and the change can be
            # approved again, so the change is live and nobody has
            # said yes to it. Reporting them only under "undone"
            # gave an agent a number to look at and no answer to
            # "is there work left for the human?".
            payload["undone_waiting_count"] = len(undone_rows)

            # The chain of human decisions. An agent that is asked
            # "was the approval right?" needs to see that an undo
            # happened at all - and it needs to know whether the record
            # of that undo still holds.
            from mas.audit import audit_report

            try:
                audit = audit_report(
                    _workspace_base(config, arguments.get("workspace")),
                    engine.ledger.entries(),
                )
                payload["audit"] = {
                    "chain_count": int(audit.get("chain_count") or 0),
                    "entries": int(audit.get("entries") or 0),
                    "ok": bool(audit.get("ok")),
                    "problems": list(audit.get("problems") or []),
                }
            except Exception:  # noqa: BLE001 - status must never crash
                payload["audit"] = {
                    "chain_count": 0,
                    "entries": 0,
                    "ok": True,
                    "problems": [],
                }
        except Exception:  # noqa: BLE001 - status must never crash
            payload["conflicting_drafts"] = []
            payload["conflicting_draft_count"] = 0
            payload["undone_drafts"] = []
            payload["undone_draft_count"] = 0
        return payload

    if name == "mas_audit":
        from mas.audit import audit_report

        engine = _engine(config)
        base = _workspace_base(config, arguments.get("workspace"))
        report = audit_report(base, engine.ledger.entries())
        report["ok"] = bool(report.get("ok"))
        return report

    if name == "mas_ledger":
        engine = _engine(config)
        rows = engine.ledger.tail(int(arguments.get("tail", 50)))
        run_id = arguments.get("run_id")
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        return {"path": str(engine.ledger.path), "entries": rows}

    if name == "mas_undos":
        # The same answer the command line gives. An agent that asks
        # "which approvals were wrong?" gets the same list a person
        # gets - read from the audit chain, not from the draft's own
        # state, which cannot hold it.
        from mas.undos import draft_history, undo_report, workspace_rows

        workspace = str(arguments.get("workspace") or "").strip()
        base = pathlib.Path(workspace).expanduser() if workspace else _workspace_base(config, "")
        rows = workspace_rows(base)
        draft_id = str(arguments.get("draft_id") or "").strip()
        if draft_id:
            history = draft_history(rows, draft_id)
            return {
                "workspace": str(base),
                "draft_id": draft_id,
                "approvals": history.get("approvals"),
                "undos": history.get("undos"),
                "ever_undone": history.get("ever_undone"),
                "records": history.get("records"),
            }
        report = undo_report(rows)
        return {
            "workspace": str(base),
            "records": report.get("records"),
            "unmatched": report.get("unmatched"),
            "totals": report.get("totals"),
        }

    if name == "mas_tickets":
        engine = _engine(config)
        state = arguments.get("state")
        raw = engine.tickets.list(state)
        # The queue, read the way every other surface reads it.
        #
        # The gap: this tool returned the raw ticket dicts - the id,
        # the state, and a payload of raw fields. An agent could read
        # the title and the raw reason, but *why* the loop stopped and
        # *how to continue* only existed in the command line's human
        # text layout. This tool is the one an agent asks; the handover
        # has to live here.
        #
        # The raw tickets stay exactly where they were, under
        # "tickets": that is the engine's own record and anything
        # already reading it must keep working. The briefs are added
        # beside them.
        briefs = []
        try:
            from mas.ticket_brief import ticket_briefs

            briefs = ticket_briefs(raw)
        except Exception:  # noqa: BLE001 - the queue must never crash
            briefs = []
        return {
            "tickets": raw,
            "briefs": briefs,
            "briefs_count": len(briefs),
            "briefs_text": "\n".join(
                "  %s [%s] %s"
                % (
                    b.get("id") or "",
                    b.get("kind") or "",
                    b.get("blocked_on")
                    or b.get("reason_reading")
                    or b.get("title")
                    or "",
                )
                for b in briefs
            ),
        }

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
        result = engine.resume(
            str(arguments.get("ticket_id")),
            str(arguments.get("note", "")),
            max_iterations=arguments.get("max_iterations"),
            deadline_sec=arguments.get("deadline_sec"),
            max_tokens=arguments.get("max_tokens"),
        )
        return result.to_dict()

    if name == "mas_handback":
        engine = _engine(config)
        ticket_id = str(arguments.get("ticket_id"))
        text = str(arguments.get("artifact") or "")
        file_path = str(arguments.get("file") or "").strip()
        if file_path:
            path = pathlib.Path(file_path).expanduser()
            if not path.exists():
                return {"error": f"file not found: {file_path}"}
            text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {"error": "handback needs an artifact or a file"}
        try:
            result = engine.handback(
                ticket_id,
                text,
                str(arguments.get("note", "")),
            )
        except KeyError as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        return result.to_dict()

    if name == "mas_report":
        engine = _engine(config)
        rows = engine.ledger.entries()
        run_id = arguments.get("run_id")
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        # The draft queue is read too, for the same reason the CLI
        # reads it: a run that proposed a change instead of making it
        # leaves a draft behind, and a report that never reads it says
        # "closed" while the file on the disk is still the old one.
        drafts = drafts_of_machine(config, rows)
        if run_id:
            wanted = {
                str(item.get("id") or "")
                for row in rows
                if str(row.get("role") or "") == "draft"
                for item in (row.get("drafts") or [])
                if isinstance(item, dict)
            }
            drafts = [d for d in drafts if str(d.get("id") or "") in wanted]
        report = build_report(rows, engine.tickets.list(), drafts=drafts)
        if arguments.get("as_markdown", True):
            return {"markdown": render_markdown(report), "totals": report["totals"]}
        return report

    if name == "mas_replay":
        engine = _engine(config)
        rows = engine.ledger.entries()
        run_id = arguments.get("run_id")
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        # The queue is read here too.
        #
        # The gap this closes
        # ------------------
        # The MCP replay built its threads from the ledger alone, so it
        # reported a problem as open while the report - reading the
        # queue - reported the same problem as closed. An agent driving
        # Claude, GPT or Kimi through MCP got a different answer from
        # the same machine than a human got from the terminal, and
        # neither of them could tell the other was wrong.
        #
        # The replay and the report now read the queue through the same
        # function, so they cannot drift apart.
        return replay_trace(rows, engine.tickets.list())

    if name == "mas_export":
        engine = _engine(config)
        rows = engine.ledger.entries()
        run_id = arguments.get("run_id")
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        return {"markdown": ledger_markdown(rows), "entries": len(rows)}

    if name == "mas_teams":
        return {"teams": list_teams()}

    if name == "mas_doctor":
        return summarize(run_doctor(config))

    if name == "mas_upgrade":
        from mas.upgrade import upgrade_runtime

        state = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        ).resolve()
        return upgrade_runtime(state, force=bool(arguments.get("force")))

    if name == "mas_config":
        action = str(arguments.get("action", "get"))
        if action == "set":
            values = arguments.get("values") or {}
            # Write back the persisted view, not the environment-merged one,
            # so a temporary MAS_STATE_DIR or an environment-provided API key
            # never becomes permanent.
            merged = dict(load_persisted_config())
            merged.update(values)
            save_config(merged)
            return {"config": merged}
        return {"config": config}

    if name == "mas_drafts":
        from mas.drafts import draft_roots, drafts_in, find_draft

        action = str(arguments.get("action", "list"))
        state = arguments.get("state")
        base = _workspace_base(config, arguments.get("workspace"))
        if action == "show":
            draft_id = str(arguments.get("draft_id") or "")
            found = find_draft(base, draft_id)
            if found is None:
                return {"error": "draft_not_found", "message": "draft not found: %s" % draft_id}
            draft = dict(found[1])
            _add_draft_history(draft, draft_histories(draft_roots(base), [draft_id]))
            return {"draft": draft}
        rows = drafts_in(base, str(state) if state else None)
        # The history of each draft, read from the audit chain.
        #
        # The gap this closes: the command line showed "this approval
        # was taken back" next to a draft, and this tool did not. An
        # agent asked "is this draft still good?" read the draft's own
        # state - ``approved`` - and never learned that the approval
        # had been taken back once and given again. The draft file
        # cannot hold that fact: it keeps only the newest state, so
        # after a second approval the wrongness of the first one is
        # gone from it.
        #
        # So the reading is done in one place (``mas.undos``) and
        # both surfaces call it.
        histories = draft_histories(
            draft_roots(base),
            [str(row.get("id") or "") for row in rows if row.get("id")],
        )
        for row in rows:
            _add_draft_history(row, histories)
        waiting = [
            str(row.get("id") or "")
            for row in rows
            if row.get("waiting_for_human")
        ]
        return {
            "workspace": str(base),
            "drafts": rows,
            "count": len(rows),
            # The changes that still wait for a person, and the
            # subset of them whose approval was taken back. An agent
            # that only reads ``state`` cannot tell "never decided"
            # from "decided once, then pulled back" - both look like
            # a state it has to interpret on its own.
            "waiting_count": len(waiting),
            "waiting_draft_ids": waiting,
            "undone_waiting_count": len(
                [
                    row
                    for row in rows
                    if row.get("waiting_for_human")
                    and str(row.get("state") or "") == "undone"
                ]
            ),
        }

    if name == "mas_draft_stage":
        from mas.drafts import stage_draft

        base = _workspace_base(config, arguments.get("workspace"))
        relative = str(arguments.get("path") or "").strip()
        content = str(arguments.get("content") or "")
        if not relative:
            return {"error": "path_required", "message": "a draft needs a path"}
        try:
            draft = stage_draft(
                base,
                relative,
                content.encode("utf-8"),
                reason=str(arguments.get("reason") or ""),
                source="mcp",
            )
        except ValueError as exc:
            return {"error": "draft_refused", "message": str(exc)}
        return {"draft": draft, "message": "drafted: %s" % draft.get("path")}

    if name == "mas_draft_resolve":
        from mas.drafts import (
            DraftConflict,
            approve_draft,
            draft_conflict,
            find_draft,
            peek_ticket,
            redeem_ticket,
            reject_draft,
            verify_human_token,
        )

        action = str(arguments.get("action", "")).strip().lower()
        draft_id = str(arguments.get("draft_id") or "")
        if action not in ("approve", "reject"):
            return {"error": "unknown_action", "message": "action must be approve or reject"}
        base = _workspace_base(config, arguments.get("workspace"))
        force = bool(arguments.get("force"))
        found = find_draft(base, draft_id)
        if found is None:
            return {"error": "draft_not_found", "message": "draft not found: %s" % draft_id}
        owner = found[0]
        draft = found[1]
        ticket = str(arguments.get("ticket") or "").strip()
        if ticket:
            # A ticket is the narrow door: one draft, one use, one
            # expiry. It opens the door for exactly the decision the
            # human signed for - and nothing else.
            #
            # The ticket is *looked at* first, not spent. A ticket is
            # one-shot, so a question that fails for some other
            # reason - a ticket for another draft, an expired one -
            # must not cost the human the decision they handed over.
            peeked = peek_ticket(
                _state_dir(config), draft_id, ticket, action=action
            )
            if not peeked.get("ok"):
                return {
                    "error": "ticket_refused",
                    "reason": peeked.get("reason"),
                    "message": (
                        "the ticket does not open this door: %s" % peeked.get("reason")
                    ),
                }
        elif not verify_human_token(_state_dir(config), arguments.get("token")):
            return {
                "error": "human_token_required",
                "message": (
                    "approving a draft needs the human token or a one-shot ticket; "
                    "run `mas drafts token` or `mas drafts ticket <id>` "
                    "on the machine that owns the files"
                ),
            }

        # The clash is looked for before anything is spent. Nothing is
        # written, no ticket is burned, and the draft keeps waiting -
        # the human is told what moved and can decide.
        if action == "approve" and not force:
            clash = draft_conflict(owner, draft)
            if clash.get("conflict"):
                return {
                    "error": "draft_conflict",
                    "message": (
                        "the file changed while the draft was waiting "
                        "(expected %s, found %s); approve with force to "
                        "overwrite it anyway"
                        % (
                            str(clash.get("expected_sha256") or "")[:12],
                            str(clash.get("current_sha256") or "")[:12],
                        )
                    ),
                    "path": clash.get("path") or draft.get("path"),
                    "expected_sha256": clash.get("expected_sha256"),
                    "current_sha256": clash.get("current_sha256"),
                    "draft_id": draft_id,
                    "state": draft.get("state"),
                }

        if ticket:
            redeemed = redeem_ticket(
                _state_dir(config), draft_id, ticket, action=action
            )
            if not redeemed.get("ok"):
                return {
                    "error": "ticket_refused",
                    "reason": redeemed.get("reason"),
                    "message": (
                        "the ticket does not open this door: %s" % redeemed.get("reason")
                    ),
                }
        note = str(arguments.get("note") or "")
        try:
            if action == "approve":
                draft = approve_draft(owner, draft_id, note, force=force)
                message = "written back: %s" % draft.get("path")
            else:
                draft = reject_draft(owner, draft_id, note)
                message = "dropped: %s" % draft.get("path")
        except KeyError:
            return {"error": "draft_not_found", "message": "draft not found: %s" % draft_id}
        except DraftConflict as exc:
            clash = dict(getattr(exc, "clash", {}) or {})
            return {
                "error": "draft_conflict",
                "message": str(exc),
                "path": clash.get("path") or draft.get("path"),
                "expected_sha256": clash.get("expected_sha256"),
                "current_sha256": clash.get("current_sha256"),
                "draft_id": draft_id,
                "state": draft.get("state"),
            }
        except ValueError as exc:
            return {"error": "draft_refused", "message": str(exc)}
        return {"draft": draft, "message": message}

    if name == "mas_draft_compare":
        from mas.drafts import draft_compare, find_draft

        base = _workspace_base(config, arguments.get("workspace"))
        draft_id = str(arguments.get("draft_id") or "").strip()
        if not draft_id:
            return {"error": "draft_id_required", "message": "a comparison needs a draft id"}
        found = find_draft(base, draft_id)
        if found is None:
            return {"error": "draft_not_found", "message": "draft not found: %s" % draft_id}
        owner = found[0]
        draft = found[1]
        try:
            comparison = draft_compare(owner, draft)
        except Exception as exc:  # noqa: BLE001 - a report must not crash
            return {
                "error": "compare_failed",
                "message": "%s: %s" % (type(exc).__name__, exc),
            }
        comparison["workspace"] = str(base)
        return {"comparison": comparison}

    if name == "mas_draft_undo":
        from mas.drafts import (
            DraftUndoConflict,
            draft_undo_plan,
            find_draft,
            peek_ticket,
            redeem_ticket,
            undo_draft,
            verify_human_token,
        )

        draft_id = str(arguments.get("draft_id") or "").strip()
        if not draft_id:
            return {"error": "draft_id_required", "message": "an undo needs a draft id"}
        base = _workspace_base(config, arguments.get("workspace"))
        force = bool(arguments.get("force"))
        found = find_draft(base, draft_id)
        if found is None:
            return {"error": "draft_not_found", "message": "draft not found: %s" % draft_id}
        owner = found[0]
        draft = found[1]

        # The plan comes first: it says what the undo would do and
        # what it would cost, and it changes nothing.
        plan = draft_undo_plan(owner, draft)
        if not force:
            reason = str(plan.get("reason") or "")
            if reason == "not_approved":
                return {
                    "error": "draft_not_approved",
                    "message": "draft %s is %s, only an approved draft can be undone"
                    % (draft_id, draft.get("state")),
                }
            if reason == "no_baseline":
                return {
                    "error": "no_baseline",
                    "message": (
                        "draft %s kept no baseline, so there is nothing to undo to"
                        % draft_id
                    ),
                }
            if reason == "file_missing":
                return {
                    "error": "file_missing",
                    "message": "the file %s is gone" % draft.get("path"),
                }
            if reason == "file_changed":
                lost = list(plan.get("would_lose") or [])
                return {
                    "error": "undo_conflict",
                    "message": (
                        "the file changed after the draft was approved "
                        "(approved %s, found %s); undoing now would throw the "
                        "newer work away - undo with force to do it anyway"
                        % (
                            str(plan.get("approved_sha256") or "")[:12],
                            str(plan.get("current_sha256") or "")[:12],
                        )
                    ),
                    "path": draft.get("path"),
                    "approved_sha256": plan.get("approved_sha256"),
                    "current_sha256": plan.get("current_sha256"),
                    "would_lose": lost,
                    "would_lose_count": len(lost),
                    "draft_id": draft_id,
                    "state": draft.get("state"),
                }

        ticket = str(arguments.get("ticket") or "").strip()
        if ticket:
            peeked = peek_ticket(_state_dir(config), draft_id, ticket, action="approve")
            if not peeked.get("ok"):
                return {
                    "error": "ticket_refused",
                    "reason": peeked.get("reason"),
                    "message": "the ticket does not open this door: %s"
                    % peeked.get("reason"),
                }
        elif not verify_human_token(_state_dir(config), arguments.get("token")):
            return {
                "error": "human_token_required",
                "message": (
                    "undoing a draft needs the human token or a one-shot ticket; "
                    "run `mas drafts token` or `mas drafts ticket <id>` "
                    "on the machine that owns the files"
                ),
            }

        if ticket:
            redeemed = redeem_ticket(_state_dir(config), draft_id, ticket, action="approve")
            if not redeemed.get("ok"):
                return {
                    "error": "ticket_refused",
                    "reason": redeemed.get("reason"),
                    "message": "the ticket does not open this door: %s"
                    % redeemed.get("reason"),
                }
        note = str(arguments.get("note") or "")
        try:
            undone = undo_draft(owner, draft_id, note, force=force)
        except KeyError:
            return {"error": "draft_not_found", "message": "draft not found: %s" % draft_id}
        except DraftUndoConflict as exc:
            return {
                "error": "undo_conflict",
                "message": str(exc),
                "path": draft.get("path"),
                "approved_sha256": plan.get("approved_sha256"),
                "current_sha256": plan.get("current_sha256"),
                "draft_id": draft_id,
            }
        except ValueError as exc:
            return {"error": "undo_refused", "message": str(exc)}
        return {
            "draft": undone,
            "plan": plan,
            "message": "undone: %s" % undone.get("path"),
        }

    if name == "mas_files":
        from mas.resources import describe_folder, format_summary

        named = str(arguments.get("path") or "").strip()
        workspace = str(arguments.get("workspace") or "").strip()
        if named:
            targets = [(named, pathlib.Path(named).expanduser())]
        elif workspace:
            # The caller names a workspace, so that one folder is indexed
            # - the same way the draft tools read one workspace. Without
            # this the tool would silently index the artifacts root and
            # report a folder the caller never asked about.
            base = _workspace_base(config, workspace)
            targets = [(str(base), base)]
        else:
            targets = [
                (str(root), root) for root in _all_workspace_roots(config)
            ]
        indexes = []
        for label, target in targets:
            index = describe_folder(target)
            index["label"] = label
            indexes.append(index)
        return {
            "folders": indexes,
            "count": len(indexes),
            "files": sum(int(i.get("count") or 0) for i in indexes),
            "summaries": [format_summary(i) for i in indexes],
        }

    if name == "mas_artifacts":
        engine = _engine(config)
        rows = engine.ledger.entries()
        run_id = arguments.get("run_id")
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        # The same implementation the CLI uses, so the two can never
        # tell the user two different stories about what was produced.
        items = artifact_rows(rows)
        return {
            "artifacts": items,
            "count": len(items),
            "missing": len([item for item in items if not item.get("exists")]),
            "reading": format_artifacts(items),
        }

    if name == "mas_closure":
        engine = _engine(config)
        rows = engine.ledger.entries()
        run_id = str(arguments.get("run_id") or "").strip()
        if run_id:
            rows = [row for row in rows if row.get("run_id") == run_id]
        tickets = engine.tickets.list()
        if run_id:
            wanted = {
                str(row.get("ticket_id") or "")
                for row in rows
                if str(row.get("ticket_id") or "")
            }
            tickets = [t for t in tickets if str(t.get("id") or "") in wanted]
        drafts = drafts_of_machine(config, rows)
        if run_id:
            wanted_drafts = {
                str(item.get("id") or "")
                for row in rows
                if str(row.get("role") or "") == "draft"
                for item in (row.get("drafts") or [])
                if isinstance(item, dict)
            }
            drafts = [d for d in drafts if str(d.get("id") or "") in wanted_drafts]
        verdict = closure_verdict_for(rows, tickets, drafts=drafts)
        if arguments.get("as_markdown", True):
            return {
                "markdown": format_closure(verdict),
                "ok": verdict["ok"],
                "empty": verdict["empty"],
                "reasons": verdict["reasons"],
                "counts": verdict["counts"],
            }
        return verdict

    if name == "mas_seal":
        from mas.seal import seal_report

        rows = engine.ledger.entries()
        run_id = str(arguments.get("run_id") or "").strip()
        if run_id:
            rows = [row for row in rows if str(row.get("run_id") or "") == run_id]
        return seal_report(rows)

    if name == "mas_preview":
        from mas.resources import preview_file

        raw = str(arguments.get("path") or "").strip()
        target = pathlib.Path(raw).expanduser() if raw else pathlib.Path("")
        workspace = str(arguments.get("workspace") or "").strip()
        if workspace and target and not target.is_absolute():
            # A relative path is read against the workspace the caller
            # named. Otherwise the tool would look in the process folder
            # and report a file that plainly exists.
            target = _workspace_base(config, workspace) / target
        if not str(target) or not target.exists():
            return {"error": "file_not_found", "message": "file not found: %s" % target}
        if target.is_dir():
            return {"error": "not_a_file", "message": "that is a folder: %s" % target}
        return preview_file(target, max_chars=int(arguments.get("max_chars") or 4000))

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
    force_utf8_output()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""v0.7.85: asking *which approvals were taken back*.

The gap
-------
v0.7.77 made the undo put the file back, byte for byte. v0.7.78 made
the undo leave a trace: a hash-linked entry in the audit chain and a
copy in the main ledger. Both are true, and neither of them answers
the question a reader actually has.

The question is not "did an undo happen" - the ledger shows that in
one more line. The question is **"which approvals were wrong?"** A
ledger is a river of events; an audit needs a *list*. A reader who
wants to know "which of the things I approved had to be taken back"
currently has to read the whole ledger, spot the undo lines, and
work out by hand which approval each one cancels - which is exactly
the work the tool should be doing.

The second gap is quieter and worse. A draft that was approved, then
taken back, then approved again looks *approved*. The draft file
holds only the newest state, so "this was once wrong" disappears from
the state of the draft. The chain remembers it, but nothing shows it
to the reader.

What this module does
---------------------
It reads the decision rows - the ones the chain and the ledger both
hold - and pairs each undo with the approval it cancels. The pairing
is *positional*, not by guesswork: the undo takes back the most
recent approval that is still standing, so the pairs are read off a
stack, exactly as the file's own history unfolds.

The rules that keep this honest:

* **The chain is the source.** The rows are read from the audit
  chain of a workspace, or from the ledger rows the chain wrote.
  Nothing here guesses: an undo with no standing approval is
  reported as unmatched instead of being paired with a neighbour.
* **A missing count is a missing count.** ``reverted_line_count`` is
  zero when the change really was empty, and the field is absent
  when the record predates v0.7.85 - those are different facts and
  are not merged.
* **No state is invented.** This module reads; it never writes and
  never changes a draft's state.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

#: The event that means "a human approved a change".
APPROVE_EVENT = "draft_approve"

#: The event that means "a human took that approval back".
UNDO_EVENT = "draft_undo"

#: The events that are human decisions about a file.
DECISION_EVENTS = (APPROVE_EVENT, UNDO_EVENT)


def _event_of(row: Dict[str, Any]) -> str:
    """The event name of a row - chain entries and ledger rows differ."""
    return str(row.get("event") or row.get("role") or "").strip()


def _group_key(row: Dict[str, Any]) -> Tuple[str, str]:
    """Which decision this row is about.

    The workspace is part of the key: the ledger holds the decisions
    of every workspace on the machine, and two workspaces could in
    principle hold a draft with the same id. Pairing them would be a
    cross-workspace mistake that looks like a fact.
    """
    return (
        str(row.get("workspace") or ""),
        str(row.get("draft_id") or ""),
    )


def _order_of(row: Dict[str, Any]) -> int:
    """The position of a row, when it has one."""
    try:
        return int(row.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def decision_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The human decisions among the rows, in the order they happened."""
    picked: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if _event_of(row) in DECISION_EVENTS:
            picked.append(row)
    # A stable sort keeps the rows of one draft in the order they were
    # written; a row without a sequence number keeps its place.
    picked.sort(key=_order_of)
    return picked


def _record(approve: Dict[str, Any], undo: Dict[str, Any]) -> Dict[str, Any]:
    """One taken-back approval, described."""
    reverted = undo.get("reverted_line_count")
    lost = undo.get("would_lose_count")
    return {
        "draft_id": str(undo.get("draft_id") or approve.get("draft_id") or ""),
        "path": str(undo.get("path") or approve.get("path") or ""),
        "workspace": str(undo.get("workspace") or approve.get("workspace") or ""),
        "approve_seq": approve.get("seq"),
        "approve_record_set": str(approve.get("record_set") or "ledger"),
        "approve_ts": str(approve.get("ts") or ""),
        "approve_note": str(approve.get("note") or ""),
        "approve_sha256": str(approve.get("approved_sha256") or ""),
        "undo_seq": undo.get("seq"),
        "undo_record_set": str(undo.get("record_set") or "ledger"),
        "undo_ts": str(undo.get("ts") or ""),
        "undo_note": str(undo.get("note") or ""),
        "forced": bool(undo.get("forced")),
        "would_lose_count": int(lost) if isinstance(lost, (int, float)) else 0,
        # ``None`` means "the record does not say" - which is a
        # different fact from "the change was empty".
        "reverted_line_count": (
            int(reverted) if isinstance(reverted, (int, float)) else None
        ),
        "audit_sha256": str(undo.get("audit_sha256") or ""),
    }


def pair_decisions(
    rows: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pair each undo with the approval it takes back.

    The undo cancels the *most recent approval that is still
    standing*, so the pairs come off a stack. An undo with nothing
    standing to cancel is returned separately instead of being
    paired with a neighbour - that would invent a fact.
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    order: List[Tuple[str, str]] = []
    for row in decision_rows(rows):
        key = _group_key(row)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    records: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for key in order:
        standing: List[Dict[str, Any]] = []
        for row in groups[key]:
            event = _event_of(row)
            if event == APPROVE_EVENT:
                standing.append(row)
            elif event == UNDO_EVENT:
                if not standing:
                    unmatched.append(row)
                    continue
                records.append(_record(standing.pop(), row))
    return records, unmatched


def undo_totals(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the taken-back approvals add up to."""
    drafts = {str(record.get("draft_id") or "") for record in records}
    reverted = 0
    unknown = 0
    for record in records:
        count = record.get("reverted_line_count")
        if isinstance(count, (int, float)):
            reverted += int(count)
        else:
            unknown += 1
    return {
        "undone": len(records),
        "drafts": len(drafts),
        "reverted_lines": reverted,
        # The records written before v0.7.85 do not say how many lines
        # were reverted. They are counted, not guessed at.
        "records_without_line_count": unknown,
        "forced": sum(1 for record in records if record.get("forced")),
    }


def undo_report(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The whole answer: which approvals were taken back, and by how much."""
    records, unmatched = pair_decisions(rows)
    return {
        "records": records,
        "unmatched": unmatched,
        "totals": undo_totals(records),
    }


def draft_history(
    rows: Iterable[Dict[str, Any]],
    draft_id: str,
) -> Dict[str, Any]:
    """What happened to one draft: how often approved, how often taken back."""
    target = str(draft_id or "")
    records, _ = pair_decisions(rows)
    mine = [record for record in records if record.get("draft_id") == target]
    approvals = 0
    undos = 0
    for row in decision_rows(rows):
        if str(row.get("draft_id") or "") != target:
            continue
        if _event_of(row) == APPROVE_EVENT:
            approvals += 1
        elif _event_of(row) == UNDO_EVENT:
            undos += 1
    return {
        "draft_id": target,
        "approvals": approvals,
        "undos": undos,
        # "Was ever taken back" is the fact the draft's own state
        # cannot hold: after a second approval the state is
        # "approved" again, and the wrongness of the first one is
        # only visible here.
        "ever_undone": undos > 0,
        "records": mine,
    }


def chain_rows(workspace: Any) -> List[Dict[str, Any]]:
    """The decision rows of one workspace's audit chain."""
    from mas.audit import chain_for

    chain = chain_for(workspace)
    if chain is None:
        return []
    return list(chain.entries())


def workspace_rows(base: Any) -> List[Dict[str, Any]]:
    """The decision rows of every audit chain under a folder.

    Each row is tagged with the workspace it belongs to and with the
    record set it came from. The chain's own sequence number is not
    the ledger's - the chain also holds the proposal, which the ledger
    does not - so a row that does not say where it came from would
    show two different numbers for the same decision in two different
    commands.
    """
    from mas.audit import chains_in

    rows: List[Dict[str, Any]] = []
    for chain in chains_in(base):
        workspace = str(getattr(chain, "workspace", "") or "")
        for entry in chain.entries():
            row = dict(entry)
            row.setdefault("workspace", workspace)
            row["record_set"] = "chain"
            # The chain entry carries its own hash under "sha256"; the
            # ledger's copy carries it under "audit_sha256". The record
            # names one field, so both are filled in here.
            row.setdefault("audit_sha256", str(entry.get("sha256") or ""))
            rows.append(row)
    return rows


def _set_name(value: Any) -> str:
    """Which record set a number belongs to."""
    name = str(value or "").strip()
    if name == "chain":
        return "审计链"
    return "账本"


def format_undos(report: Dict[str, Any]) -> str:
    """Say which approvals were taken back - in words, for a person."""
    records = list(report.get("records") or [])
    totals = dict(report.get("totals") or {})
    if not records:
        return "还没有任何被撤销的批准：目前每一次批准都还站着。"

    lines: List[str] = []
    drafts = int(totals.get("drafts") or 0)
    lines.append(
        "被撤销的批准：%d 次（涉及 %d 个草稿）"
        % (int(totals.get("undone") or 0), drafts)
    )
    reverted = int(totals.get("reverted_lines") or 0)
    unknown = int(totals.get("records_without_line_count") or 0)
    if reverted:
        lines.append("  这些撤销一共让 %d 行改动退回原样" % reverted)
    if unknown:
        lines.append(
            "  另有 %d 条旧记录没有写明撤销了多少行（旧版本留下的，不猜）" % unknown
        )
    forced = int(totals.get("forced") or 0)
    if forced:
        lines.append("  其中 %d 次是在文件已被改动的情况下强制撤销的" % forced)
    lines.append("")
    for record in records:
        lines.append("  %s  %s" % (record.get("draft_id") or "?", record.get("path") or ""))
        lines.append(
            "      批准：%s（%s第 %s 条）"
            % (
                record.get("approve_ts") or "-",
                _set_name(record.get("approve_record_set")),
                record.get("approve_seq") or "-",
            )
        )
        note = str(record.get("undo_note") or "")
        lines.append(
            "      撤销：%s（%s第 %s 条）%s"
            % (
                record.get("undo_ts") or "-",
                _set_name(record.get("undo_record_set")),
                record.get("undo_seq") or "-",
                ("　理由：%s" % note) if note else "",
            )
        )
        count = record.get("reverted_line_count")
        if isinstance(count, (int, float)):
            lines.append("      这次撤销让 %d 行改动退回原样" % int(count))
        if record.get("forced"):
            lines.append("      强制撤销：文件在这之后被改过，撤销会覆盖那些改动")
    unmatched = list(report.get("unmatched") or [])
    if unmatched:
        lines.append("")
        lines.append("另有 %d 条撤销找不到对应的批准（记录不完整，不替它配对）：" % len(unmatched))
        for row in unmatched:
            lines.append(
                "  %s  %s  %s"
                % (
                    row.get("draft_id") or "?",
                    row.get("path") or "",
                    row.get("ts") or "",
                )
            )
    return "\n".join(lines)

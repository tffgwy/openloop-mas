# -*- coding: utf-8 -*-
"""v0.7.78: a tamper-evident chain of human decisions.

The gap
-------
The ledger is append-only, and that is a promise about *behaviour*:
every writer only ever appends. It is not a promise about the bytes
on the disk. Anybody - a person, a script, a bug - can open
``runs.jsonl`` in an editor and change a line, and the file stays
perfectly readable. An audit trail of a loop that lets a human
approve, and later *take back*, an edit has to survive that.

So this module keeps a second record whose entries are chained by
hash: every entry carries the hash of the one before it and the hash
of its own content. Change any byte of an entry and the entry's own
hash stops matching; delete an entry and the next entry's link to it
breaks. The chain does not make the history true - it makes a
*changed* history visible.

What it does not do
-------------------
A chain is not a notarisation. Anybody who rewrites the whole chain
from the tampered entry onwards can produce a chain that verifies
again, because nothing outside the chain remembers what the head used
to be. That is why the undo events are written **twice**: once into
this chain, and once into the main ledger, which keeps its own copy
of the fields. Two copies that have to agree make a silent rewrite of
both harder, and the cross-check says exactly where the two disagree.

The scope of the claim is deliberate: this detects a changed entry, a
deleted entry, and a copy that was edited on its own. It does not
detect a determined rewrite of every copy at once. That is an honest
limit, and it is better than a promise that cannot be kept.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Dict, List, Optional

from mas.util import ensure_dir, now_iso

#: The file inside a workspace's draft folder that holds the chain.
AUDIT_FILENAME = "audit.jsonl"

#: The "hash of nothing" - the predecessor of the first entry.
GENESIS = "0" * 64

#: The events that also get a copy in the main ledger.
LEDGER_EVENTS = ("draft_approve", "draft_undo")

#: The ledger role of each of those events.
LEDGER_ROLES = ("draft_approve", "draft_undo")

#: The fields that are copied into the main ledger. The copy has to
#: match the chain entry exactly; that is what makes the copy
#: checkable.
COPY_FIELDS = (
    "draft_id",
    "path",
    "baseline_sha256",
    "approved_sha256",
    "current_sha256",
    "note",
    "would_lose_count",
    "forced",
)

#: What the events read like, for a person.
EVENT_READING: Dict[str, str] = {
    "draft_stage": "起草改动",
    "draft_approve": "批准改动",
    "draft_reject": "拒绝改动",
    "draft_undo": "撤销改动",
}


def event_reading(event: Any) -> str:
    """The plain reading of an event name, or the name itself."""
    key = str(event or "").strip()
    if not key:
        return ""
    return EVENT_READING.get(key, key)


def canonical(payload: Dict[str, Any]) -> str:
    """The canonical text of an entry - the bytes the hash is over.

    The keys are sorted and the separators are tight, so the same
    entry always hashes to the same value on every platform. A hash
    that changed because of the *machine* rather than because of the
    *content* would make the whole chain useless.
    """
    body = {k: v for k, v in payload.items() if k != "sha256"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def entry_hash(prev_sha256: str, payload: Dict[str, Any]) -> str:
    """The hash of one entry, chained to the one before it."""
    text = "%s\n%s" % (str(prev_sha256 or ""), canonical(payload))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditChain:
    """An append-only chain of events whose tampering is visible."""

    #: The file name used when the chain is created.
    AUDIT_FILENAME = AUDIT_FILENAME

    def __init__(self, path: str | pathlib.Path, workspace: str = "") -> None:
        self.path = pathlib.Path(path)
        ensure_dir(self.path.parent)
        # The workspace the chain belongs to. It scopes the cross-check
        # against the ledger: the ledger holds the decisions of *every*
        # workspace on the machine, so a chain may only be compared with
        # the ledger rows that name its own workspace.
        self.workspace = str(workspace or "")
        if not self.workspace and self.path.parent.name == ".mas-drafts":
            self.workspace = str(self.path.parent.parent)

    # ------------------------------------------------------------------ read
    def entries(self) -> List[Dict[str, Any]]:
        """Every entry of the chain, in order."""
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def head(self) -> str:
        """The hash of the newest entry - the chain's current tip."""
        rows = self.entries()
        if not rows:
            return GENESIS
        return str(rows[-1].get("sha256") or GENESIS)

    def summary(self) -> Dict[str, Any]:
        """A short description of the chain, for a status page."""
        rows = self.entries()
        events: Dict[str, int] = {}
        for row in rows:
            name = str(row.get("event") or "unknown")
            events[name] = events.get(name, 0) + 1
        report = self.verify()
        return {
            "path": str(self.path),
            "entries": len(rows),
            "events": events,
            "head": self.head(),
            "ok": bool(report.get("ok")),
            "problems": list(report.get("problems") or []),
        }

    # ----------------------------------------------------------------- write
    def append(self, event: str, **fields: Any) -> Dict[str, Any]:
        """Add one event to the chain.

        The event is hashed *before* it is written, and the hash
        covers both the content and the hash of the entry before it.
        """
        rows = self.entries()
        payload: Dict[str, Any] = {"event": str(event or "event"), "ts": now_iso()}
        payload.update(fields)
        payload["seq"] = len(rows) + 1
        if rows:
            payload["prev_sha256"] = str(rows[-1].get("sha256") or GENESIS)
        else:
            payload["prev_sha256"] = GENESIS
        payload["sha256"] = entry_hash(payload["prev_sha256"], payload)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    # ---------------------------------------------------------------- verify
    def verify(self) -> Dict[str, Any]:
        """Check the chain: is every entry still the entry that was written?

        Three things are checked, and each of them catches a different
        kind of damage:

        * the sequence numbers are gapless - a deleted entry shows up
          here;
        * every entry points at the hash of the entry before it - a
          rewritten entry shows up here;
        * every entry hashes to the hash it carries - an edited entry
          shows up here.
        """
        rows = self.entries()
        problems: List[str] = []
        previous = GENESIS
        for index, row in enumerate(rows):
            number = index + 1
            seq = row.get("seq")
            if seq != number:
                problems.append(
                    "第 %d 条的序号是 %s（应为 %d）" % (number, seq, number)
                )
            if str(row.get("prev_sha256") or "") != previous:
                problems.append("第 %d 条与上一条的链接断了" % number)
            recomputed = entry_hash(row.get("prev_sha256") or "", row)
            if recomputed != str(row.get("sha256") or ""):
                problems.append("第 %d 条的内容被改过" % number)
            previous = str(row.get("sha256") or "")
        return {
            "ok": not problems,
            "entries": len(rows),
            "head": previous,
            "problems": problems,
        }


def _occurrence_keys(
    items: List[Dict[str, Any]],
    draft_key: str,
    event_key: str,
) -> Dict[str, Dict[str, Any]]:
    """Index items by *who they are*, not by what they hash to.

    A chain entry and its ledger copy are matched by the identity of
    the decision: which draft it was about, what kind of decision it
    was, and which time it was that decision for the same draft (the
    same draft can be approved twice, once taken back and once again).
    The hash is *checked* against the match - it is not the key,
    because a changed hash is exactly the thing that has to be noticed,
    and using it as the key would hide the change behind a "no such
    entry" message.

    The counter is per (draft, event) pair, so an event that never
    reaches the ledger - the proposal, which is not a human decision -
    cannot shift the numbering of the ones that do.
    """
    counts: Dict[str, int] = {}
    index: Dict[str, Dict[str, Any]] = {}
    for item in items:
        draft = str(item.get(draft_key) or "")
        event = str(item.get(event_key) or "")
        pair = (draft, event)
        number = counts.get(pair, 0)
        counts[pair] = number + 1
        key = "%s\x1f%s\x1f%d" % (draft, event, number)
        index[key] = item
    return index


def verify_against_ledger(
    chain: AuditChain,
    ledger_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Check the main ledger's copy of the decisions against the chain.

    The decision is written twice: into the chain next to the
    workspace, and into the main ledger that ``mas ledger`` shows. The
    two copies have to say the same thing. This looks for the ways
    they can stop saying the same thing:

    * a ledger row whose chain entry is missing (the chain was edited,
      or the row was invented);
    * a row whose fields or hash differ from the chain entry (one of
      the two was edited on its own);
    * a chain entry that never reached the ledger (the ledger was
      edited).
    """
    problems: List[str] = []
    seen_problems: set[str] = set()

    def _complain(message: str) -> None:
        if message not in seen_problems:
            seen_problems.add(message)
            problems.append(message)

    entries = chain.entries()
    workspace = str(getattr(chain, "workspace", "") or "")

    rows = []
    for row in ledger_rows or []:
        if str(row.get("role") or "") not in LEDGER_ROLES:
            continue
        # The ledger holds the decisions of every workspace on this
        # machine. A row that names another workspace is not this
        # chain's business - comparing them would report a problem
        # that does not exist.
        if workspace and str(row.get("workspace") or "") not in ("", workspace):
            continue
        rows.append(row)

    chain_index = _occurrence_keys(entries, "draft_id", "event")
    ledger_index = _occurrence_keys(rows, "draft_id", "role")

    for key, row in ledger_index.items():
        entry = chain_index.get(key)
        if entry is None:
            _complain(
                "账本里的%s（%s）在审计链里找不到对应事件"
                % (event_reading(row.get("role")), row.get("draft_id"))
            )
            continue
        # The hash is checked here, not used as the key: a copy that
        # was edited on its own keeps its place in the ledger, and the
        # mismatch has to be named as exactly that.
        if str(row.get("audit_sha256") or "") != str(entry.get("sha256") or ""):
            _complain(
                "账本与审计链不一致：%s 的哈希不同（链里的那条被改过，或账本里的副本被改过）"
                % row.get("draft_id")
            )
        for field in COPY_FIELDS:
            if field in row and row.get(field) != entry.get(field):
                _complain(
                    "账本与审计链不一致：%s 的 %s"
                    % (row.get("draft_id"), field)
                )

    for key, entry in chain_index.items():
        if str(entry.get("event") or "") not in LEDGER_EVENTS:
            continue
        if key not in ledger_index:
            _complain(
                "审计链里的%s（%s，第 %s 条）没有进账本"
                % (
                    event_reading(entry.get("event")),
                    entry.get("draft_id"),
                    entry.get("seq"),
                )
            )
    return {"ok": not problems, "problems": problems}


def chain_for(workspace: Any) -> Optional[AuditChain]:
    """The chain of one workspace, or ``None`` when it has none yet."""
    base = pathlib.Path(workspace)
    path = base / ".mas-drafts" / AUDIT_FILENAME
    if not path.exists():
        return None
    return AuditChain(path)


def chain_roots(base: Any) -> List[pathlib.Path]:
    """The folders that may hold a chain.

    A run writes into ``<state_dir>/artifacts/<run_id>/``, so the chain
    sits one folder deeper than the artifacts root the user usually
    names. The named folder is the first candidate and its sub-folders
    follow, so a reader does not have to know the id of the run.
    """
    root = pathlib.Path(base)
    roots = [root]
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name != ".mas-drafts":
                roots.append(child)
    except OSError:
        pass
    return roots


def chains_in(base: Any) -> List[AuditChain]:
    """Every chain under ``base``."""
    found: List[AuditChain] = []
    for root in chain_roots(base):
        chain = chain_for(root)
        if chain is not None:
            found.append(chain)
    return found


def audit_report(
    base: Any,
    ledger_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Check every chain under ``base`` - and the copies in the ledger.

    The report answers three questions:

    * does every chain still verify (no entry changed, none deleted);
    * does every chain entry that belongs in the ledger have its copy
      there;
    * does every ledger copy still say what the chain says.

    A problem is a *finding*, not a crash: a broken chain has to be
    reportable, and reporting it must never be the thing that fails.
    """
    chains = chains_in(base)
    rows = list(ledger_rows or [])
    chains_report: List[Dict[str, Any]] = []
    problems: List[str] = []
    for chain in chains:
        summary = chain.summary()
        cross = verify_against_ledger(chain, rows)
        summary["cross_check"] = cross
        chains_report.append(summary)
        for problem in summary.get("problems") or []:
            problems.append("%s: %s" % (chain.path, problem))
        for problem in cross.get("problems") or []:
            problems.append("%s: %s" % (chain.path, problem))
    return {
        "workspace": str(base),
        "chains": chains_report,
        "chain_count": len(chains_report),
        "entries": sum(int(c.get("entries") or 0) for c in chains_report),
        "ok": not problems,
        "problems": problems,
    }

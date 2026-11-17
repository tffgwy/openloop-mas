# -*- coding: utf-8 -*-
"""v0.7.72: the draft layer - an edit becomes a draft, not a fait accompli.

The gap
-------
A run writes straight into the workspace. That is right for a *new*
file: nothing is lost when the loop creates ``notes/finding.txt``. It
is wrong for an *existing* file: the loop overwrites the human's own
work without ever showing what it changed. The reader of this project
asked for the MOKI rule - "the change goes into a draft first, and only
a human confirmation writes it back to the original file" - and the
engine had no way to honour it.

What this module does
---------------------
It is a draft store for *edits to existing files*:

* ``stage_draft`` takes the bytes a candidate wants to write over an
  existing file and records them as a **draft**: the original file is
  not touched, and the draft carries a readable diff of what would
  change.
* ``approve_draft`` writes the draft back to the original file - and
  only then. Nothing else writes.
* ``reject_draft`` drops the draft. The original file is exactly as it
  was, because it was never changed.
* ``list_drafts`` / ``get_draft`` report what is waiting.

Two properties are load-bearing:

* **Nothing is written before the approval.** A draft that is never
  approved leaves the file byte-identical.
* **A rejection changes nothing.** The draft is disposable; the
  original is not.

Why the draft lives next to the workspace
-----------------------------------------
The draft is stored *inside* the workspace, under ``.mas-drafts/``, so
that it travels with the folder it belongs to. A draft that lives in a
central store is a draft that gets lost when the folder is copied, and
a draft that gets lost is a change that can never be reviewed.
"""
from __future__ import annotations

import contextlib
import datetime
import difflib
import hashlib
import json
import os
import pathlib
import secrets
from typing import Any, Dict, List, Optional, Tuple

from mas.audit import COPY_FIELDS, AuditChain
from mas.util import ensure_dir, now_iso, short_sha

#: The folder inside a workspace that holds the drafts.
DRAFTS_DIRNAME = ".mas-drafts"

#: The file inside the state directory that holds the human token.
HUMAN_TOKEN_FILENAME = "human.token"

#: The states a draft can be in.
DRAFT_PENDING = "pending"
DRAFT_APPROVED = "approved"
DRAFT_REJECTED = "rejected"

#: The state of a draft whose change was written back and then taken
#: back again. The draft is not dead - it can be approved a second
#: time - but the *undo* is the newest fact about it.
DRAFT_UNDONE = "undone"


class DraftUndoConflict(ValueError):
    """The file moved *after* the approval, so the undo would cost.

    An undo is meant to repair a bad approval. If the file was edited
    after that approval, undoing would throw the newer work away -
    the same loss the undo exists to prevent, just moved. The
    exception carries the facts so the human can see what would be
    lost before deciding.
    """

    def __init__(self, plan: Dict[str, Any], draft: Dict[str, Any]) -> None:
        self.plan = dict(plan or {})
        self.draft = dict(draft or {})
        self.draft_id = str(self.draft.get("id") or "")
        self.path = str(self.draft.get("path") or "")
        super().__init__(
            "the file %s changed after the draft was approved "
            "(approved %s, found %s) - undoing now would throw the "
            "newer work away; undo with force to do it anyway"
            % (
                self.path,
                str(self.plan.get("approved_sha256") or "")[:12],
                str(self.plan.get("current_sha256") or "")[:12],
            )
        )


class DraftConflict(ValueError):
    """The file moved under the draft while the draft was waiting.

    Approving would throw the newer work away, so the approval is
    refused. The exception carries the facts - which file, which hash
    was expected, which hash is there now - so a caller can tell a
    human exactly what happened.
    """

    def __init__(self, clash: Dict[str, Any], draft: Dict[str, Any]) -> None:
        self.clash = dict(clash or {})
        self.draft = dict(draft or {})
        self.draft_id = str(self.draft.get("id") or "")
        self.path = str(self.draft.get("path") or "")
        super().__init__(
            "the file %s changed since this draft was made "
            "(expected %s, found %s) - approving now would throw the "
            "newer work away; re-stage the draft or approve with --force"
            % (
                self.path,
                str(self.clash.get("expected_sha256") or "")[:12],
                str(self.clash.get("current_sha256") or "")[:12],
            )
        )


def drafts_dir(workspace: Any) -> pathlib.Path:
    """The folder that holds the drafts of one workspace."""
    return pathlib.Path(workspace) / DRAFTS_DIRNAME


def _draft_file(workspace: Any, draft_id: str) -> pathlib.Path:
    return drafts_dir(workspace) / ("%s.json" % draft_id)


def _safe_id(draft_id: Any) -> str:
    """A draft id that is safe as a file name."""
    text = "".join(
        ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(draft_id or "")
    ).strip("._")
    return text or "draft"


def _relative(workspace: Any, absolute: Any) -> str:
    """The path of a file, relative to the workspace."""
    base = pathlib.Path(workspace)
    target = pathlib.Path(absolute)
    try:
        return str(target.resolve().relative_to(base.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(target).replace("\\", "/")


def _diff_lines(before: str, after: str, relative: str) -> List[str]:
    """A readable, unified-ish diff of one change."""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=relative,
        tofile="%s (draft)" % relative,
        lineterm="",
        n=3,
    )
    return list(diff)


def _decode(data: bytes) -> str:
    """The readable form of a file's bytes."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


#: The digest basis of a draft made now: the hash of the file's bytes.
DIGEST_BASIS_BYTES = "bytes"

#: The digest basis of a draft made before v0.7.76: the hash of the
#: decoded text. A file that is not valid UTF-8 has a different text
#: hash and a different byte hash, so the two bases cannot be mixed -
#: comparing a text hash against a byte hash would call every binary
#: file a conflict, forever.
DIGEST_BASIS_TEXT = "text"


def _digest_bytes(data: bytes) -> str:
    """The hash of some bytes."""
    return hashlib.sha256(data).hexdigest()


def _digest_text(data: bytes) -> str:
    """The hash of the decoded text of some bytes."""
    return short_sha(_decode(data), 64)


def _is_text(data: bytes) -> bool:
    """Whether some bytes are readable as UTF-8.

    A binary file cannot be shown as a diff, so the comparison says so
    instead of printing mojibake and calling it a difference.
    """
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def audit_chain(workspace: Any) -> AuditChain:
    """The tamper-evident chain of one workspace.

    The chain lives next to the drafts, so it travels with the folder
    it belongs to - the same rule the drafts follow.
    """
    return AuditChain(drafts_dir(workspace) / AuditChain.AUDIT_FILENAME)


def record_decision(
    workspace: Any,
    event: str,
    draft: Dict[str, Any],
    **fields: Any,
) -> Dict[str, Any]:
    """Write one human decision into the chain - and into the ledger.

    The decision is written twice on purpose:

    * into the chain next to the workspace, where the hash of every
      entry links it to the one before it, so a changed entry is
      visible;
    * into the main ledger, where ``mas ledger`` shows it, so the
      decision is visible in the audit trail of the whole machine.

    The two copies have to agree - that agreement is checked by
    ``mas audit`` and by the twenty-second gate. A failure to write
    the ledger copy must not lose the chain entry, so the ledger is
    written first and a problem with it is recorded in the chain
    rather than raised.
    """
    payload: Dict[str, Any] = {
        "draft_id": str(draft.get("id") or ""),
        "path": str(draft.get("path") or ""),
    }
    payload.update(fields)
    entry = audit_chain(workspace).append(event, **payload)
    # The ledger copy is written after the chain entry, so the chain -
    # the authoritative record - always exists first.
    if not append_ledger_copy(entry, workspace):
        entry["ledger_copy"] = "failed"
    return entry


def ledger_row_for(entry: Dict[str, Any], workspace: Any) -> Dict[str, Any]:
    """The main ledger's copy of one chain entry."""
    row: Dict[str, Any] = {
        "role": str(entry.get("event") or "event"),
        "run_id": "",
        "task_id": "",
        "iteration": 0,
        "event": str(entry.get("event") or "event"),
        "workspace": str(workspace),
        "audit_sha256": str(entry.get("sha256") or ""),
        "audit_prev_sha256": str(entry.get("prev_sha256") or ""),
    }
    for field in COPY_FIELDS:
        if field in entry:
            row[field] = entry.get(field)
    return row


def append_ledger_copy(entry: Dict[str, Any], workspace: Any) -> bool:
    """Copy one chain entry into the main ledger.

    Returns ``True`` when the copy was written. A failure is not
    fatal: the chain entry is the authoritative record, and the
    cross-check will report the missing copy instead of the undo
    being lost.
    """
    try:
        from mas.config import load_config
        from mas.ledger import Ledger

        config = load_config()
        path = pathlib.Path(config.get("state_dir") or "~/.mas")
        path = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(path)))
        ) / "ledgers" / "runs.jsonl"
        Ledger(path).append(ledger_row_for(entry, workspace))
        return True
    except Exception:  # noqa: BLE001 - the chain is the authoritative copy
        return False

def stage_draft(
    workspace: Any,
    relative_path: str,
    data: bytes,
    reason: str = "",
    source: str = "",
) -> Dict[str, Any]:
    """Record a proposed change to an existing file as a draft.

    The original file is **not** touched. The draft carries the bytes
    the change would produce, a diff against the current contents, and
    the reason the change was proposed.

    The draft is refused for a file that does not exist: a draft for a
    missing file is not an *edit*, it is a creation, and a creation is
    what the normal write path is for.
    """
    base = pathlib.Path(workspace)
    target = base / str(relative_path or "").strip().replace("\\", "/")
    try:
        resolved = target.resolve()
        resolved.relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("refusing to draft a change outside the workspace: %s" % relative_path) from exc
    if not resolved.exists() or not resolved.is_file():
        raise ValueError("cannot draft a change to a file that does not exist: %s" % relative_path)

    before = resolved.read_bytes()
    draft_id = "D-%s" % secrets.token_hex(4)
    relative = _relative(base, resolved)
    diff = _diff_lines(_decode(before), _decode(data), relative)
    payload = {
        "id": draft_id,
        "workspace": str(base),
        "path": relative,
        "absolute": str(resolved),
        "state": DRAFT_PENDING,
        "reason": str(reason or ""),
        "source": str(source or ""),
        "created_at": now_iso(),
        "resolved_at": None,
        # The hash is taken over the *bytes*, and the basis is recorded
        # next to it. A draft that records only a number cannot say
        # how that number was made, and a comparison across two bases
        # is not a comparison at all - it is a false alarm.
        "before_sha256": _digest_bytes(before),
        "after_sha256": _digest_bytes(data),
        "digest_basis": DIGEST_BASIS_BYTES,
        "before_bytes": len(before),
        "after_bytes": len(data),
        "changed": before != data,
        "diff": diff,
    }
    ensure_dir(drafts_dir(base))
    _draft_file(base, draft_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # The bytes themselves live next to the draft: a diff is a report of
    # a change, not the change. Approving the draft has to write the
    # *bytes* the editor produced, so they are stored as well.
    (drafts_dir(base) / ("%s.data" % draft_id)).write_bytes(data)
    # The baseline is kept as well. A draft that remembers only *that*
    # the file was different cannot say *how* it was different - and
    # "how" is the whole question when somebody is about to overwrite
    # newer work with --force.
    (drafts_dir(base) / ("%s.base" % draft_id)).write_bytes(before)
    # The chain records the whole life of the draft: it was proposed,
    # then (maybe) approved, then (maybe) taken back. The proposal
    # itself stays in the chain only - it is not a human decision, so
    # it does not belong in the ledger of decisions.
    audit_chain(base).append(
        "draft_stage",
        draft_id=draft_id,
        path=relative,
        baseline_sha256=_digest_bytes(before),
        approved_sha256=_digest_bytes(data),
        note=str(reason or ""),
    )
    return payload


def get_draft(workspace: Any, draft_id: str) -> Optional[Dict[str, Any]]:
    """One draft, or ``None`` when there is no such draft."""
    path = _draft_file(workspace, _safe_id(draft_id))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def list_drafts(workspace: Any, state: Optional[str] = None) -> List[Dict[str, Any]]:
    """The drafts of a workspace, newest last."""
    folder = drafts_dir(workspace)
    if not folder.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("D-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if state is None or str(data.get("state")) == str(state):
            rows.append(data)
    return rows


def _draft_bytes(workspace: Any, draft_id: str) -> bytes:
    """The bytes a draft stands for."""
    path = drafts_dir(workspace) / ("%s.data" % _safe_id(draft_id))
    if not path.exists():
        return b""
    return path.read_bytes()


def file_digest(path: Any) -> str:
    """The hash of what a file holds right now.

    A missing file hashes to an empty string, so a file that vanished
    reads as "nothing" rather than as a crash.
    """
    target = pathlib.Path(path)
    try:
        if not target.exists() or not target.is_file():
            return ""
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return ""


def draft_conflict(workspace: Any, draft: Dict[str, Any]) -> Dict[str, Any]:
    """Whether the file moved under the draft while it waited.

    A draft records the hash of the file it was made against. If the
    file changed since then, approving the draft would silently throw
    the newer work away - the classic *lost update* of two agents
    editing the same file.

    The check is *optimistic*: it locks nothing, it only notices. The
    caller decides what to do about it.
    """
    absolute = str(draft.get("absolute") or "")
    if not absolute:
        return {"conflict": False, "reason": "no_file"}
    expected = str(draft.get("before_sha256") or "")
    if not expected:
        # An old draft without a recorded hash: nothing to compare
        # against, so nothing to claim about a conflict.
        return {"conflict": False, "reason": "no_recorded_hash"}
    current = file_digest(absolute)
    basis = str(draft.get("digest_basis") or "")
    if basis != DIGEST_BASIS_BYTES:
        # A draft made before v0.7.76 recorded the hash of the
        # *decoded text*. That number is not comparable with a byte
        # hash: for a file that decodes as UTF-8 the two are the
        # *same* number (decoding and re-encoding gives the same
        # bytes back), and for a file that does not decode they are
        # different numbers for the same file. Either way the
        # number alone says nothing about whether the file moved -
        # and a comparison that sometimes works by coincidence is
        # not a comparison. So the recorded number is NOT used
        # here at all: such a draft is judged the only honest way
        # there is, by reading the baseline it was made against, if
        # it kept one.
        baseline = _draft_baseline_bytes(workspace, draft)
        if baseline is None:
            # The draft is old, has no basis, and kept no baseline.
            # Nothing can be said about the file with confidence, so
            # nothing is claimed: a false alarm is worse than no
            # alarm, because it teaches the human to ignore the real
            # ones.
            return {
                "conflict": False,
                "reason": "unknown_basis",
                "expected_sha256": expected,
                "current_sha256": current,
            }
        if _digest_bytes(baseline) == current:
            return {"conflict": False, "reason": "unchanged"}
        return {
            "conflict": True,
            "reason": "file_changed",
            "path": str(draft.get("path") or ""),
            "expected_sha256": expected,
            "current_sha256": current,
        }
    if current == expected:
        return {"conflict": False, "reason": "unchanged"}
    return {
        "conflict": True,
        "reason": "file_changed",
        "path": str(draft.get("path") or ""),
        "expected_sha256": expected,
        "current_sha256": current,
    }


def _draft_data_path(workspace: Any, draft_id: str, suffix: str) -> pathlib.Path:
    """The path of a side file that belongs to a draft."""
    return drafts_dir(workspace) / ("%s%s" % (_safe_id(draft_id), suffix))


def _draft_baseline_bytes(workspace: Any, draft: Dict[str, Any]) -> Optional[bytes]:
    """The bytes the target held when the draft was made.

    ``None`` when the draft did not keep them - which is the case for
    every draft made before v0.7.76.
    """
    draft_id = str(draft.get("id") or "")
    if not draft_id:
        return None
    path = _draft_data_path(workspace, draft_id, ".base")
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def draft_compare(workspace: Any, draft: Dict[str, Any]) -> Dict[str, Any]:
    """The three versions of one file, side by side.

    A conflict says "the file moved". That is a fact, but it does not
    tell the human *what* moved, and "what" is the only thing that
    makes a decision possible. This compares three versions:

    * the **baseline** - what the file held when the draft was made;
    * the **current** file - what it holds now;
    * the **draft** - what the draft proposes to write.

    The interesting part is not the two diffs but the *overlap*: the
    lines the human changed themselves, and which the draft would
    therefore throw away. That set is what ``would_lose`` names.

    Nothing is written and nothing is decided here. The comparison is
    a report, so it can be asked for as often as one likes.
    """
    absolute = str(draft.get("absolute") or "")
    relative = str(draft.get("path") or "")
    draft_id = str(draft.get("id") or "")
    proposed = _draft_bytes(workspace, draft_id)
    baseline = _draft_baseline_bytes(workspace, draft)

    current: Optional[bytes] = None
    if absolute:
        target = pathlib.Path(absolute)
        try:
            if target.exists() and target.is_file():
                current = target.read_bytes()
        except OSError:
            current = None

    readable = baseline is not None and _is_text(baseline)
    if readable and current is not None and _is_text(current):
        base_text = _decode(baseline)
        current_text = _decode(current)
        draft_text = _decode(proposed)
        baseline_to_current = _diff_lines(base_text, current_text, relative)
        current_to_draft = _diff_lines(current_text, draft_text, relative)
        lost = _lost_lines(base_text, current_text, draft_text)
    else:
        base_text = ""
        current_text = ""
        draft_text = ""
        baseline_to_current = []
        current_to_draft = []
        lost = []

    clash = draft_conflict(workspace, draft)
    return {
        "draft_id": draft_id,
        "path": relative,
        "conflict": bool(clash.get("conflict")),
        "reason": clash.get("reason"),
        "expected_sha256": clash.get("expected_sha256"),
        "current_sha256": clash.get("current_sha256"),
        "baseline_sha256": _digest_bytes(baseline) if baseline is not None else "",
        "draft_sha256": _digest_bytes(proposed),
        "baseline_bytes": len(baseline) if baseline is not None else 0,
        "current_bytes": len(current) if current is not None else 0,
        "draft_bytes": len(proposed),
        "file_missing": current is None,
        "readable": bool(readable and current is not None and _is_text(current)),
        "baseline_to_current": baseline_to_current,
        "current_to_draft": current_to_draft,
        "would_lose": lost,
        "would_lose_count": len(lost),
        "baseline_available": baseline is not None,
    }


def _lost_lines(before: str, current: str, draft: str) -> List[str]:
    """The lines the human changed and the draft would take back.

    A line counts as lost when it is in the *current* file but not in
    the *draft*, and it is not in the baseline either - so it is
    something the human added or rewrote after the draft was made.
    A line the draft also carries is not lost, whatever else it did to
    it.
    """
    base_lines = set(before.splitlines())
    draft_lines = set(draft.splitlines())
    lost: List[str] = []
    for line in current.splitlines():
        if not line.strip():
            continue
        if line in draft_lines:
            continue
        if line in base_lines:
            continue
        if line not in lost:
            lost.append(line)
    return lost


def approve_draft(
    workspace: Any,
    draft_id: str,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Write a draft back to the file it changes - and only then.

    This is the *only* place where a draft becomes a real change to the
    original file. Until this is called, the file is byte-identical to
    what it was.

    The write is **conditional**: the file has to still be the file the
    draft was made against. If the human edited it while the draft
    waited, approving would throw their newer work away - so the
    approval is refused and the conflict is reported. ``force`` says
    "I know, write it anyway".
    """
    draft = get_draft(workspace, draft_id)
    if draft is None:
        raise KeyError("unknown draft: %s" % draft_id)
    if draft.get("state") not in (DRAFT_PENDING, DRAFT_UNDONE):
        raise ValueError(
            "draft %s is %s, not pending" % (draft.get("id"), draft.get("state"))
        )
    base = pathlib.Path(workspace)
    target = pathlib.Path(str(draft.get("absolute") or ""))
    data = _draft_bytes(workspace, str(draft.get("id") or ""))
    try:
        target.resolve().relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("refusing to write outside the workspace: %s" % draft.get("path")) from exc
    if not force:
        clash = draft_conflict(workspace, draft)
        if clash.get("conflict"):
            raise DraftConflict(clash, draft)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    draft["state"] = DRAFT_APPROVED
    draft["resolved_at"] = now_iso()
    draft["note"] = str(note or "")
    _draft_file(base, str(draft.get("id"))).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # The approval is a human decision, so it leaves a trace. The
    # chain holds the hash-linked copy; the ledger holds the copy a
    # reader sees in ``mas ledger``.
    record_decision(
        base,
        "draft_approve",
        draft,
        baseline_sha256=str(draft.get("before_sha256") or ""),
        approved_sha256=_digest_bytes(data),
        current_sha256=_digest_bytes(data),
        note=str(note or ""),
        would_lose_count=0,
        forced=bool(force),
    )
    return draft


def reject_draft(
    workspace: Any,
    draft_id: str,
    note: str = "",
) -> Dict[str, Any]:
    """Drop a draft. The original file is not touched at all.

    A rejection changes nothing - that is the whole point. The file
    keeps the bytes it had, and the draft is marked so a reader can see
    that the change was seen and turned down.
    """
    draft = get_draft(workspace, draft_id)
    if draft is None:
        raise KeyError("unknown draft: %s" % draft_id)
    if draft.get("state") != DRAFT_PENDING:
        raise ValueError(
            "draft %s is %s, not pending" % (draft.get("id"), draft.get("state"))
        )
    draft["state"] = DRAFT_REJECTED
    draft["resolved_at"] = now_iso()
    draft["note"] = str(note or "")
    _draft_file(workspace, str(draft.get("id"))).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return draft


def draft_undo_plan(workspace: Any, draft: Dict[str, Any]) -> Dict[str, Any]:
    """What an undo would do - and what it would cost.

    An undo puts the file back the way it was before the draft was
    made. That is only safe while the file still holds the draft's
    own bytes: if somebody edited the file *after* the approval, the
    undo would throw *their* work away, and that is a different - and
    worse - loss than the one the undo is meant to repair.

    So the plan says three things before anything is written:

    * ``ready``      - the undo can go ahead without losing anything;
    * ``changed``    - the file moved after the approval, so the undo
      would take those changes away and needs ``force``;
    * ``no_baseline``- the draft kept no baseline (it was made before
      v0.7.76), so there is nothing to go back to.

    Nothing is written here. This is a report, like the comparison.
    """
    draft_id = str(draft.get("id") or "")
    # The state on the disk is the truth. A caller may hold a copy of
    # the draft from before it was approved - and an undo that used
    # that stale copy would either refuse an undo that should work or
    # undo a draft that was never approved at all.
    current_draft = get_draft(workspace, draft_id) or draft
    absolute = str(current_draft.get("absolute") or "")
    relative = str(current_draft.get("path") or "")
    state = str(current_draft.get("state") or "")

    baseline = _draft_baseline_bytes(workspace, current_draft)
    proposed = _draft_bytes(workspace, draft_id)

    current: Optional[bytes] = None
    if absolute:
        target = pathlib.Path(absolute)
        try:
            if target.exists() and target.is_file():
                current = target.read_bytes()
        except OSError:
            current = None

    result: Dict[str, Any] = {
        "draft_id": draft_id,
        "path": relative,
        "state": state,
        "ready": False,
        "reason": "",
        "baseline_available": baseline is not None,
        "baseline_sha256": _digest_bytes(baseline) if baseline is not None else "",
        "current_sha256": _digest_bytes(current) if current is not None else "",
        "approved_sha256": _digest_bytes(proposed),
        "current_bytes": len(current) if current is not None else 0,
        "baseline_bytes": len(baseline) if baseline is not None else 0,
        "file_missing": current is None,
        "would_lose": [],
        "would_lose_count": 0,
        "current_to_baseline": [],
    }

    if state != DRAFT_APPROVED:
        result["reason"] = "not_approved"
        return result
    if baseline is None:
        result["reason"] = "no_baseline"
        return result
    if current is None:
        result["reason"] = "file_missing"
        return result

    # The file has to still hold what the approval wrote. Anything
    # else means somebody - or another agent - worked on it since.
    if _digest_bytes(current) != _digest_bytes(proposed):
        result["reason"] = "file_changed"
        if _is_text(current) and _is_text(baseline):
            lost = _lost_lines(_decode(proposed), _decode(current), _decode(baseline))
            result["would_lose"] = lost
            result["would_lose_count"] = len(lost)
            result["current_to_baseline"] = _diff_lines(
                _decode(current), _decode(baseline), relative
            )
        return result

    result["ready"] = True
    result["reason"] = "ready"
    return result


def undo_draft(
    workspace: Any,
    draft_id: str,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Take an approved change back: the file goes back to the baseline.

    The draft kept the bytes the file held when it was made, so the
    undo is not a guess - it puts back exactly what was there. The
    draft itself is **not** thrown away: it returns to ``pending``, so
    the same change can be approved again once the reason it was
    undone is understood.

    The undo is **conditional**, in the same spirit as the approval:
    if the file was changed after the approval, undoing would throw
    that newer work away, so the undo is refused unless ``force`` says
    "I know".
    """
    draft = get_draft(workspace, draft_id)
    if draft is None:
        raise KeyError("unknown draft: %s" % draft_id)
    base = pathlib.Path(workspace)
    target = pathlib.Path(str(draft.get("absolute") or ""))
    try:
        target.resolve().relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError(
            "refusing to write outside the workspace: %s" % draft.get("path")
        ) from exc

    plan = draft_undo_plan(workspace, draft)
    if not force:
        if plan.get("reason") == "not_approved":
            raise ValueError(
                "draft %s is %s, only an approved draft can be undone"
                % (draft.get("id"), draft.get("state"))
            )
        if plan.get("reason") == "no_baseline":
            raise ValueError(
                "draft %s kept no baseline, so there is nothing to undo to"
                % draft.get("id")
            )
        if plan.get("reason") == "file_missing":
            raise ValueError(
                "the file %s is gone, so there is nothing to undo"
                % draft.get("path")
            )
        if plan.get("reason") == "file_changed":
            raise DraftUndoConflict(plan, draft)

    baseline = _draft_baseline_bytes(workspace, draft)
    if baseline is None:
        raise ValueError(
            "draft %s kept no baseline, so there is nothing to undo to"
            % draft.get("id")
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(baseline)
    draft["state"] = DRAFT_UNDONE
    draft["undone_at"] = now_iso()
    draft["undo_note"] = str(note or "")
    draft["resolved_at"] = None
    _draft_file(base, str(draft.get("id"))).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # "Once was enough" is not a fact the draft can hold on its own:
    # the draft's state says the *newest* thing about it, so an
    # approval that was taken back and then approved again leaves no
    # trace by itself. The chain keeps every decision, in order, and
    # the ledger gets a copy of each one.
    lost = 0
    try:
        lost = int(plan.get("would_lose_count") or 0)
    except (TypeError, ValueError):
        lost = 0
    record_decision(
        base,
        "draft_undo",
        draft,
        baseline_sha256=str(plan.get("baseline_sha256") or ""),
        approved_sha256=str(plan.get("approved_sha256") or ""),
        current_sha256=_digest_bytes(baseline),
        note=str(note or ""),
        would_lose_count=lost,
        forced=bool(force),
    )
    return draft


def draft_overlay(workspace: Any) -> Dict[str, bytes]:
    """The pending drafts, as an overlay for the machine checks.

    A check that reads a file the loop wants to *change* should see the
    changed version - otherwise the check judges the old bytes and the
    loop can never close on its own proposal. The overlay is exactly
    the same idea the parallel candidate ranking uses, and it never
    touches the disk.
    """
    overlay: Dict[str, bytes] = {}
    for draft in list_drafts(workspace, DRAFT_PENDING):
        absolute = str(draft.get("absolute") or "")
        if not absolute:
            continue
        overlay[absolute] = _draft_bytes(workspace, str(draft.get("id") or ""))
    return overlay


def draft_roots(base: Any) -> List[pathlib.Path]:
    """The folders of ``base`` that may hold drafts.

    A run writes into ``<state_dir>/artifacts/<run_id>/``, so the
    draft sits one folder deeper than the artifacts root the user
    usually names. The named folder is the first candidate and its
    sub-folders follow, so a reader does not have to know the id of
    the run that produced the draft.
    """
    root = pathlib.Path(base)
    roots = [root]
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name != DRAFTS_DIRNAME:
                roots.append(child)
    except OSError:
        pass
    return roots


def drafts_in(base: Any, state: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every draft under ``base``, newest last.

    This is the one place that knows how to look into the
    sub-folders, so the CLI and the MCP server answer the same
    question the same way.
    """
    rows: List[Dict[str, Any]] = []
    for root in draft_roots(base):
        rows.extend(list_drafts(root, state))
    return rows


def find_draft(base: Any, draft_id: str) -> Optional[Tuple[pathlib.Path, Dict[str, Any]]]:
    """The folder that owns a draft, and the draft itself.

    ``None`` when no such draft exists.
    """
    for root in draft_roots(base):
        draft = get_draft(root, draft_id)
        if draft is not None:
            return root, draft
    return None


def human_token_path(state_dir: Any) -> pathlib.Path:
    """Where the human token lives inside a state directory."""
    return pathlib.Path(state_dir) / HUMAN_TOKEN_FILENAME


def ensure_human_token(state_dir: Any) -> str:
    """The human token of this machine, created if it does not exist.

    The token is what separates *proposing* a change from *making* it.
    An agent can stage a draft without any secret at all - proposing
    is safe, because the file does not change. But approving a draft
    writes to a file the human owns, so it needs a token that only the
    human can read.

    The token is created lazily by a *human* action (the CLI), and
    the file is made unreadable to other users on the platforms that
    have such a thing.
    """
    path = human_token_path(state_dir)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        # Windows has no POSIX mode bits. The folder is the protection
        # there, and the token is only an obstacle, never a wall.
        os.chmod(path, 0o600)
    return token


def read_human_token(state_dir: Any) -> str:
    """The human token, or an empty string when there is none."""
    path = human_token_path(state_dir)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


#: The file inside the state directory that holds the ticket hashes.
TICKET_STORE_FILENAME = "tickets.json"

#: How long a ticket stays usable, in seconds.
TICKET_TTL_SECONDS = 900

#: The action a ticket may authorize.
TICKET_ACTION_APPROVE = "approve"
TICKET_ACTION_REJECT = "reject"


def ticket_store_path(state_dir: Any) -> pathlib.Path:
    """Where the signed tickets live inside a state directory."""
    return pathlib.Path(state_dir) / TICKET_STORE_FILENAME


def _load_tickets(state_dir: Any) -> Dict[str, Any]:
    """The signed tickets of this machine, as a mapping."""
    path = ticket_store_path(state_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_tickets(state_dir: Any, tickets: Dict[str, Any]) -> None:
    """Write the signed tickets back."""
    path = ticket_store_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(tickets, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _expired(created_at: Any) -> bool:
    """Whether a ticket has outlived its welcome."""
    try:
        stamp = datetime.datetime.fromisoformat(str(created_at))
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - stamp).total_seconds()
    return age > TICKET_TTL_SECONDS


def issue_ticket(
    state_dir: Any,
    draft_id: str,
    action: str = TICKET_ACTION_APPROVE,
    human_token: Any = None,
) -> Dict[str, Any]:
    """Sign a one-shot ticket for exactly one draft.

    A ticket is what a human hands to an agent so the agent can carry
    out *one* decision. Three things make it different from the
    standing human token:

    * it is bound to **one draft** - it cannot approve another one;
    * it is **one-shot** - the first use burns it;
    * it **expires** - a ticket left lying around stops working.

    Only the *hash* of the ticket is stored. The signed ticket itself
    is handed out once and never written down, so a store that gets
    copied does not carry a usable secret with it.

    The human token is what proves the ticket was signed by the human
    rather than by an agent that found the ticket store.
    """
    action = str(action or TICKET_ACTION_APPROVE).strip().lower()
    if action not in (TICKET_ACTION_APPROVE, TICKET_ACTION_REJECT):
        raise ValueError("a ticket can only approve or reject")
    if not verify_human_token(state_dir, human_token):
        raise ValueError("signing a ticket needs the human token")
    target = str(draft_id or "").strip()
    if not target:
        raise ValueError("a ticket needs a draft id")

    secret = secrets.token_urlsafe(24)
    ticket_id = "T-%s" % secrets.token_hex(6)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    record = {
        "id": ticket_id,
        "draft_id": target,
        "action": action,
        "digest": digest,
        "created_at": now_iso(),
        "used_at": None,
        "used": False,
    }
    tickets = _load_tickets(state_dir)
    tickets[ticket_id] = record
    _save_tickets(state_dir, tickets)
    return {"id": ticket_id, "secret": secret, "draft_id": target, "action": action}


def _find_ticket(
    state_dir: Any,
    draft_id: str,
    ticket: Any,
    action: str = TICKET_ACTION_APPROVE,
) -> Dict[str, Any]:
    """Look a ticket up without spending it.

    This is the *read* half of :func:`redeem_ticket`: it answers "does
    this ticket open this door?" and leaves the ticket untouched. The
    two are kept apart on purpose - a caller has to be able to ask
    the question before it commits anything, so that a check which
    fails for some other reason does not burn the one decision the
    human handed over.
    """
    given = str(ticket or "").strip()
    action = str(action or TICKET_ACTION_APPROVE).strip().lower()
    target = str(draft_id or "").strip()
    if not given:
        return {"ok": False, "reason": "no_ticket"}
    if not target:
        return {"ok": False, "reason": "no_draft"}

    tickets = _load_tickets(state_dir)
    for ticket_id, record in list(tickets.items()):
        if not isinstance(record, dict):
            continue
        digest = str(record.get("digest") or "")
        if not digest or not secrets.compare_digest(digest, hashlib.sha256(given.encode("utf-8")).hexdigest()):
            continue
        if record.get("used"):
            return {"ok": False, "reason": "ticket_already_used", "ticket_id": ticket_id}
        if _expired(record.get("created_at")):
            return {"ok": False, "reason": "ticket_expired", "ticket_id": ticket_id}
        if str(record.get("draft_id") or "") != target:
            return {"ok": False, "reason": "ticket_bound_to_another_draft", "ticket_id": ticket_id}
        if str(record.get("action") or "") != action:
            return {"ok": False, "reason": "ticket_action_mismatch", "ticket_id": ticket_id}
        return {"ok": True, "ticket_id": ticket_id, "draft_id": target, "action": action}
    return {"ok": False, "reason": "unknown_ticket"}


def peek_ticket(
    state_dir: Any,
    draft_id: str,
    ticket: Any,
    action: str = TICKET_ACTION_APPROVE,
) -> Dict[str, Any]:
    """Whether a ticket would open this door - without spending it.

    A ticket is one-shot, so asking "would this work?" must not cost
    the ticket. The caller can check the ticket first, check whatever
    else it needs to check, and only then spend the ticket on the
    decision it was signed for.
    """
    return _find_ticket(state_dir, draft_id, ticket, action=action)


def redeem_ticket(
    state_dir: Any,
    draft_id: str,
    ticket: Any,
    action: str = TICKET_ACTION_APPROVE,
) -> Dict[str, Any]:
    """Use a ticket once, for the draft it was signed for.

    Returns a record describing what happened. `ok` is `False`
    with a `reason` when the ticket does not open the door.
    """
    given = str(ticket or "").strip()
    action = str(action or TICKET_ACTION_APPROVE).strip().lower()
    target = str(draft_id or "").strip()
    if not given:
        return {"ok": False, "reason": "no_ticket"}
    if not target:
        return {"ok": False, "reason": "no_draft"}

    found = _find_ticket(state_dir, draft_id, ticket, action=action)
    if not found.get("ok"):
        return found
    ticket_id = str(found.get("ticket_id") or "")
    tickets = _load_tickets(state_dir)
    record = tickets.get(ticket_id)
    if not isinstance(record, dict):
        return {"ok": False, "reason": "unknown_ticket"}
    record["used"] = True
    record["used_at"] = now_iso()
    tickets[ticket_id] = record
    _save_tickets(state_dir, tickets)
    return {"ok": True, "ticket_id": ticket_id, "draft_id": target, "action": action}


def verify_human_token(state_dir: Any, token: Any) -> bool:
    """Whether a token opens the approval door.

    An empty token never opens it: a machine that has no token yet
    cannot be talked into approving anything.
    """
    expected = read_human_token(state_dir)
    given = str(token or "").strip()
    if not expected or not given:
        return False
    return secrets.compare_digest(expected, given)
